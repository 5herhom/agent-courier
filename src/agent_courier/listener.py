"""无消息时只查询 Git 引用；有待办时依据持久检查点投递。"""

import logging
from logging.handlers import RotatingFileHandler
import time

from .adapters.codex_queue import CodexQueue
from .locking import locked
from .protocol import CourierError, timestamp
from .service import Courier

LOG = logging.getLogger(__name__)


def poll(config):
    """一次串行检查；回执或登记更新不会触发模型，未决投递不会盲重放。"""
    with Courier(config) as courier:
        remote = courier.git.remote_head()
        if not remote:
            raise CourierError("通信分支缺失，保留检查点", stage="git")
        if remote != courier.state.get("baseline"):
            courier.refresh()
        courier.flush()
        participants = courier.state.participants(courier.state.get("baseline"))
        enabled = any(p["machine"] == config.actor["machine"] and p["inbox"] == config.inbox
                      and p["enabled"] for p in participants)
        delivered = []
        if enabled:
            for row in courier.state.db.execute("SELECT id FROM messages ORDER BY rowid").fetchall():
                mid = row["id"]
                value = courier.state.message(mid)
                if config.actor not in value["record"]["recipients"]:
                    continue
                latest = courier.state.latest_receipt(mid, config.actor)
                dispatch = courier.state.dispatch(mid)
                if latest and latest["status"] in {"delivered", "started", "completed"}:
                    continue
                if dispatch and dispatch["status"] != "ready":
                    if dispatch["status"] == "intent":
                        result = CodexQueue(config).inspect(mid)
                        courier.state.set_dispatch(mid, result["status"], result["detail"])
                        courier.ack(mid, result["status"], result["detail"])
                        LOG.warning("message_id=%s %s；未重复入队", mid, result["detail"])
                    continue
                # 数据库丢失时已有 received 仍可能处在调用后确认前窗口。
                if latest and not dispatch:
                    courier.state.set_dispatch(mid, "delivery-unknown", "已有回执但缺少本地投递关联，先核实")
                    continue
                if config.adapter.get("type") != "codex-queue":
                    continue
                adapter = CodexQueue(config)
                adapter.probe()
                courier.ack(mid, "received", "本机已验证并持久保存消息")
                courier.state.set_dispatch(mid, "intent", "即将调用 Queue，重启后不得直接重发")
                result = adapter.deliver(value)
                courier.state.set_dispatch(mid, result["status"], result["detail"])
                courier.ack(mid, result["status"], result["detail"])
                delivered.append({"message_id": mid, **result})
        courier.state.set("last_poll_at", timestamp(config.timestamp_offset))
        courier.state.set("last_error", "")
        return {"delivered": delivered, "remote_commit": remote}


def watch(config, *, once=False, stop=None):
    """操作系统锁限制同端点监听实例，失败后指数退避，不调用模型告警循环。"""
    with locked(config.directory / "watch.lock", blocking=False):
        config.directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(config.directory / "listener.log", encoding="utf-8",
                                      maxBytes=config.log_max_bytes, backupCount=config.log_backup_count)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)
        try:
            return _watch(config, once=once, stop=stop)
        finally:
            LOG.removeHandler(handler)
            handler.close()


def _watch(config, *, once, stop):
    """运行锁与日志由外层管理；配置或网络故障不会中断后续退避。"""
    try:
        delay = config.poll_interval_seconds
        last_error = None
        while stop is None or not stop.is_set():
            try:
                result = poll(config)
                delay = config.poll_interval_seconds
                last_error = None
                if once:
                    return result
            except (CourierError, OSError, ValueError) as exc:
                error = str(exc)
                if error != last_error:
                    LOG.error("监听失败：%s", error)
                    last_error = error
                if once:
                    raise
                delay = min(config.failure_backoff_max_seconds, max(delay * 2, config.poll_interval_seconds))
                try:
                    with Courier(config) as courier:
                        courier.state.set("last_error", error)
                except (CourierError, OSError, ValueError):
                    # 初始化阶段也可能失败，此时以已经落盘的日志为准。
                    pass
            if stop is None:
                time.sleep(delay)
            else:
                stop.wait(delay)
    except KeyboardInterrupt:
        return {"stopped": True}
