"""消息收发、回执与文件快照协调；具体 Agent 执行由适配器承担。"""

import json
import os
from pathlib import Path

from .git_transport import GitTransport
from .locking import locked
from .protocol import (CourierError, STATUSES, encode, endpoint, endpoint_key, freeze_file,
                       identifier, timestamp)
from .state import State


class Courier:
    """一次操作持有短锁，监听进程另持单实例锁，二者不互相阻塞。"""

    def __init__(self, config):
        self.config = config

    def __enter__(self):
        self.lock = locked(self.config.directory / "operation.lock")
        self.lock.__enter__()
        try:
            self.git = GitTransport(self.config)
            self.state = State(self.config, self.git)
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        self.state.close()
        self.lock.__exit__(*args)

    def refresh(self, *, allow_missing=False):
        head = self.git.fetch(allow_missing=allow_missing)
        if head:
            self.state.scan(head)
        return head

    def register(self, *, enabled=True, create_branch=False):
        """安装/禁用只更新本机登记，不替其他端点确认任务状态。"""
        actor = self.config.actor
        for attempt in range(self.config.publish_attempts):
            head = self.refresh(allow_missing=create_branch)
            record = {"protocol_version": 1, **actor, "description": self.config.description,
                      "enabled": enabled, "updated_at": timestamp(self.config.timestamp_offset)}
            path = f"{self.config.protocol_root}/participants/{actor['machine']}/{actor['inbox']}.json"
            try:
                commit = self.git.publish(head, {path: (encode(record) + "\n").encode()},
                                          f"courier: {'enable' if enabled else 'disable'} {actor['machine']}/{actor['inbox']}")
                self.refresh()
                return {"registered": record, "commit": commit}
            except CourierError:
                if attempt + 1 == self.config.publish_attempts:
                    raise

    def _recipients(self, head, targets):
        registered = {endpoint_key(p) for p in self.state.participants(head) if p["enabled"]}
        own = endpoint_key(self.config.actor)
        recipients = set()
        for target in targets:
            if target["machine"] == "*":
                recipients.update(k for k in registered if k[1] == target["inbox"] and k != own)
            else:
                key = endpoint_key(target)
                if key not in registered:
                    raise CourierError(f"目标尚未启用：{key[0]}/{key[1]}")
                recipients.add(key)
        if not recipients:
            raise CourierError("没有匹配的已启用收件端")
        return [{"machine": m, "inbox": i} for m, i in sorted(recipients)]

    def send(self, message, targets=None, *, in_reply_to=None, complete=False):
        """先冻结并持久化正文、ID 与广播名单，发布重试不重新生成消息。"""
        if complete and not in_reply_to:
            raise CourierError("--complete 只能用于回复原消息")
        head = self.refresh()
        if in_reply_to:
            original = self.state.message(in_reply_to)["record"]
            if self.config.actor not in original["recipients"]:
                raise CourierError("本端不是原消息收件人，不能替其回复")
            to = [original["from"]]
            recipients = to
        else:
            to = list({endpoint_key(endpoint(t, broadcast=True)): endpoint(t, broadcast=True)
                       for t in targets or []}.values())
            recipients = self._recipients(head, to)
        at = timestamp(self.config.timestamp_offset)
        mid = identifier("msg", self.config.actor, at)
        files, snapshots = freeze_file(self.config.repo, self.config.protocol_root, mid, message)
        record = {"protocol_version": 1, "message_id": mid, "created_at": at,
                  "kind": "reply" if in_reply_to else "request", "from": self.config.actor,
                  "to": to, "recipients": recipients, "in_reply_to": in_reply_to,
                  "message": message, "files": files}
        self.state.stage("messages", record, snapshots)
        if complete:
            self._stage_receipt(in_reply_to, "completed", f"结果回复：{mid}")
        self.flush()
        return {"message_id": mid, "commit": self.state.message(mid)["commit"], "recipients": recipients}

    def _stage_receipt(self, message_id, status, detail):
        if status not in STATUSES:
            raise CourierError(f"未知状态：{status}")
        original = self.state.message(message_id)
        if self.config.actor not in original["record"]["recipients"]:
            raise CourierError("不能替其他收件端确认")
        at = timestamp(self.config.timestamp_offset)
        record = {"protocol_version": 1, "receipt_id": identifier("rcpt", self.config.actor, at),
                  "responded_at": at, "message_id": message_id,
                  "message_commit": original["commit"], "receiver": self.config.actor,
                  "status": status, "detail": detail}
        self.state.stage("receipts", record, {})
        return record

    def ack(self, message_id, status, detail):
        """状态是接收端明确声明；调用者须提供实际依据。"""
        if not detail.strip():
            raise CourierError("状态回执必须提供非空实际依据 --detail")
        self.refresh()
        latest = self.state.latest_receipt(message_id, self.config.actor)
        if latest and latest["status"] == status and latest.get("detail") == detail:
            return latest
        if latest and latest["status"] == "completed" and status != "completed":
            raise CourierError("已完成消息不能退回未完成状态")
        order = {"received": 1, "delivered": 2, "started": 3, "completed": 4}
        if latest and status in {"received", "delivered", "delivery-unknown"}:
            if order.get(latest["status"], 0) > order.get(status, 0):
                return latest
        record = self._stage_receipt(message_id, status, detail)
        self.flush()
        return record

    def flush(self):
        """并发拒绝或 push 结果不明时，先核实远端 ID，再重新追加未发布记录。"""
        pending = self.state.db.execute("SELECT * FROM outbox WHERE commit_sha IS NULL ORDER BY rowid").fetchall()
        if not pending:
            return
        for attempt in range(self.config.publish_attempts):
            head = self.refresh()
            changes = {}
            remaining = []
            for row in pending:
                table = row["kind"]
                existing = self.state.db.execute(f"SELECT record, commit_sha FROM {table} WHERE id=?", (row["id"],)).fetchone()
                if existing:
                    if existing["record"] != row["record"]:
                        raise CourierError(f"同一 ID 的内容不一致：{row['id']}")
                    with self.state.db:
                        self.state.db.execute("UPDATE outbox SET commit_sha=? WHERE id=?", (existing["commit_sha"], row["id"]))
                    continue
                record = json.loads(row["record"])
                at = record["created_at" if table == "messages" else "responded_at"]
                path = f"{self.config.protocol_root}/{table}/{at[:10]}.jsonl"
                if path not in changes:
                    changes[path] = self.git.read(head, path, missing=True)
                changes[path] += (row["record"] + "\n").encode("utf-8")
                for file in self.state.db.execute("SELECT path, content FROM payloads WHERE operation_id=?", (row["id"],)):
                    changes[file["path"]] = file["content"]
                remaining.append(row)
            if not remaining:
                return
            try:
                self.git.publish(head, changes, "courier: append messages and receipts")
            except CourierError:
                if attempt + 1 == self.config.publish_attempts:
                    raise
                continue
            self.refresh()
            for row in remaining:
                value = self.state.db.execute(f"SELECT commit_sha FROM {row['kind']} WHERE id=?", (row["id"],)).fetchone()
                if value is None:
                    raise CourierError("远端发布结果缺少本次记录", stage="git")
                with self.state.db:
                    self.state.db.execute("UPDATE outbox SET commit_sha=? WHERE id=?", (value[0], row["id"]))
            return

    def read(self, message_id, *, accept=False):
        """按首次发布提交物化文件，消息原始路径不会被当成本机文件打开。"""
        self.refresh()
        value = self.state.message(message_id)
        files = []
        for file in value["record"]["files"]:
            content = self.git.read(value["commit"], file["path"])
            target = self.config.directory / "incoming" / message_id / Path(file["path"]).name
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(target.suffix + ".tmp")
            temp.write_bytes(content)
            os.replace(temp, target)
            files.append({**file, "local_path": str(target.resolve())})
        receipt = self.state.latest_receipt(message_id, self.config.actor)
        if accept and (not receipt or receipt["status"] != "completed"):
            receipt = self.ack(message_id, "started", "本收件端通过 read --accept 明确接手")
        return {**value, "files": files, "receiver_receipt": receipt}

    def status(self):
        """本机状态查询不默认访问远端；watch 或 --sync 才更新历史。"""
        pending = self.state.db.execute("SELECT id, kind FROM outbox WHERE commit_sha IS NULL").fetchall()
        return {"actor": self.config.actor, "channel": self.config.channel,
                "baseline": self.state.get("baseline"), "last_poll_at": self.state.get("last_poll_at"),
                "last_error": self.state.get("last_error"),
                "outbox": [dict(p) for p in pending],
                "dispatch": [dict(p) for p in self.state.db.execute("SELECT * FROM dispatch")],
                "file_threshold_chars": self.config.agent_message_file_threshold_chars}
