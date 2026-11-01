"""SQLite 保存已验证历史、待发布记录及投递意图，支持中断后继续核对。"""

import json
import re
import sqlite3

from .protocol import CourierError, decode, encode, validate_endpoint, validate_record


class State:
    """每个通道端点独立的运行检查点，不承担机器身份认证。"""

    def __init__(self, config, transport):
        self.config = config
        self.transport = transport
        config.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(config.directory / "state.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, record TEXT NOT NULL, commit_sha TEXT NOT NULL, path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipts (
                sequence INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                record TEXT NOT NULL, commit_sha TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, record TEXT NOT NULL, commit_sha TEXT
            );
            CREATE TABLE IF NOT EXISTS payloads (
                operation_id TEXT NOT NULL, path TEXT NOT NULL, content BLOB NOT NULL,
                PRIMARY KEY(operation_id, path)
            );
            CREATE TABLE IF NOT EXISTS dispatch (
                message_id TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT NOT NULL
            );
        """)
        binding = encode({"remote": transport.url, "branch": config.branch,
                          "root": config.protocol_root, "actor": config.actor})
        previous = self.get("binding")
        if previous is not None and previous != binding:
            raise CourierError("检查点绑定的远端、分支、目录或主机发生变化", stage="config")
        self.set("binding", binding)

    def close(self):
        self.db.close()

    def get(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))

    def message(self, message_id):
        row = self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if row is None:
            raise CourierError(f"消息不存在：{message_id}")
        return {"record": json.loads(row["record"]), "commit": row["commit_sha"], "path": row["path"]}

    def latest_receipt(self, message_id, receiver):
        rows = self.db.execute("SELECT record FROM receipts ORDER BY sequence DESC")
        for row in rows:
            value = json.loads(row[0])
            if value["message_id"] == message_id and value["receiver"] == receiver:
                return value
        return None

    def scan(self, head):
        """逐提交验证只追加约束；协议失败时事务回滚，不更新可信基线。"""
        baseline = self.get("baseline")
        if baseline == head:
            return
        previous = baseline
        with self.db:
            for commit in self.transport.commits(baseline, head):
                self._scan_commit(previous, commit)
                previous = commit
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('baseline', ?)", (head,))

    def _scan_commit(self, previous, commit):
        root = self.config.protocol_root
        for path in sorted(self.transport.changed(commit)):
            if path.startswith(f"{root}/payloads/"):
                old = self.transport.read(previous, path, missing=True)
                new = self.transport.read(commit, path, missing=True)
                existed = previous is not None and path in self.transport.paths(previous, path)
                exists = path in self.transport.paths(commit, path)
                if existed and (not exists or old != new):
                    raise CourierError(f"已发布快照被改写或删除：{path}", stage="history")
                continue
            match = re.fullmatch(re.escape(root) + r"/(messages|receipts)/\d{4}-\d{2}-\d{2}\.jsonl", path)
            if not match:
                continue
            old = self.transport.read(previous, path, missing=True)
            new = self.transport.read(commit, path, missing=True)
            if not new.startswith(old) or (old and not old.endswith(b"\n")):
                raise CourierError(f"历史记录被改写或删除：{path}", stage="history")
            tail = new[len(old):]
            if tail and not tail.endswith(b"\n"):
                raise CourierError(f"JSONL 最后一条记录缺少换行：{path}")
            for line in tail.splitlines():
                if not line:
                    raise CourierError(f"JSONL 不允许空记录：{path}")
                try:
                    value = decode(line)
                except (ValueError, UnicodeError) as exc:
                    raise CourierError(f"无效 UTF-8 JSONL：{path}") from exc
                kind = match[1]
                validate_record(value, kind, path, root)
                self._insert(kind, value, commit, path)

    def _insert(self, kind, value, commit, path):
        try:
            if kind == "messages":
                for file in value["files"]:
                    self.transport.read(commit, file["path"])
                if value["kind"] == "reply":
                    original = self.message(value["in_reply_to"])["record"]
                    if value["from"] not in original["recipients"] or value["recipients"] != [original["from"]]:
                        raise CourierError("回复的发件端或收件端与原消息不一致")
                self.db.execute("INSERT INTO messages VALUES (?, ?, ?, ?)",
                                (value["message_id"], encode(value), commit, path))
            else:
                original = self.message(value["message_id"])
                if original["commit"] != value["message_commit"]:
                    raise CourierError("回执关联的首次发布提交不一致")
                if value["receiver"] not in original["record"]["recipients"]:
                    raise CourierError("回执机器不在原消息收件端中")
                self.db.execute("INSERT INTO receipts(id, record, commit_sha) VALUES (?, ?, ?)",
                                (value["receipt_id"], encode(value), commit))
        except sqlite3.IntegrityError as exc:
            raise CourierError(f"重复记录 ID：{path}", stage="history") from exc

    def participants(self, head):
        root = self.config.protocol_root
        result = []
        for path in self.transport.paths(head, f"{root}/participants/"):
            value = decode(self.transport.read(head, path))
            if not isinstance(value, dict):
                raise CourierError(f"端点登记必须是 JSON 对象：{path}")
            actor = {"machine": value.get("machine"), "inbox": value.get("inbox")}
            validate_endpoint(actor)
            expected = f"{root}/participants/{actor['machine']}/{actor['inbox']}.json"
            if path != expected or value.get("protocol_version") != 1 or type(value.get("enabled")) is not bool:
                raise CourierError(f"端点登记无效：{path}")
            result.append(value)
        return result

    def stage(self, kind, record, payloads):
        key = "message_id" if kind == "messages" else "receipt_id"
        operation = record[key]
        with self.db:
            self.db.execute("INSERT INTO outbox VALUES (?, ?, ?, NULL)", (operation, kind, encode(record)))
            self.db.executemany("INSERT INTO payloads VALUES (?, ?, ?)",
                                [(operation, path, value) for path, value in payloads.items()])
        return operation

    def dispatch(self, message_id):
        row = self.db.execute("SELECT * FROM dispatch WHERE message_id=?", (message_id,)).fetchone()
        return dict(row) if row else None

    def set_dispatch(self, message_id, status, detail):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO dispatch VALUES (?, ?, ?)", (message_id, status, detail))
