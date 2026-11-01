"""协议字段由程序生成；正文只作为数据传输，不解释或执行。"""

import json
import re
import secrets
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

VERSION = 1
FILE_PREFIX = "请阅读文件 @file("
FILE_SUFFIX = "，按文件中的要求执行任务或回答问题。"
STATUSES = {"received", "delivered", "started", "completed", "failed", "delivery-unknown"}
NAME = re.compile(r"[a-z0-9][a-z0-9_.-]*\Z")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class CourierError(Exception):
    """可向调用者报告的错误，区分配置、协议、发布和投递阶段。"""

    def __init__(self, message, *, stage="protocol"):
        super().__init__(message)
        self.stage = stage


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def decode(value):
    """严格读取 UTF-8 JSON，拒绝重复字段和非 JSON 数值。"""
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"重复 JSON 字段：{key}")
            result[key] = item
        return result

    def invalid(value):
        raise ValueError(f"无效 JSON 数值：{value}")

    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value, object_pairs_hook=pairs, parse_constant=invalid)


def machine_id():
    """每次取真实主机名，不持久化另一个机器身份。"""
    return name(socket.gethostname().strip().lower())


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise CourierError(f"无效的可读标识：{value!r}")
    return value


def endpoint(value, *, broadcast=False):
    """CLI 接受 machine[/inbox]，广播需由调用者显式允许。"""
    machine, sep, inbox = value.partition("/")
    if machine != "*" or not broadcast:
        name(machine)
    return {"machine": machine, "inbox": name(inbox if sep else "default")}


def endpoint_key(value):
    return value["machine"], value["inbox"]


def timestamp(offset="+08:00"):
    if not re.fullmatch(r"[+-]\d{2}:\d{2}", offset):
        raise CourierError("timestamp_offset 必须是 ±HH:MM")
    hours, minutes = map(int, offset[1:].split(":"))
    if hours > 23 or minutes > 59:
        raise CourierError("timestamp_offset 超出有效范围")
    delta = timedelta(hours=hours, minutes=minutes)
    tz = timezone(delta if offset[0] == "+" else -delta)
    return datetime.now(tz).isoformat(timespec="microseconds")


def identifier(prefix, actor, at):
    clock = datetime.fromisoformat(at).strftime("%Y%m%d-%H%M%S-%f")
    return f"{prefix}-{clock}-{actor['machine']}-{actor['inbox']}-{secrets.token_hex(6)}"


def relative_path(value):
    """校验仓库相对路径；同时识别 Windows 和 POSIX 绝对路径。"""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CourierError("文件路径不能为空或包含 NUL")
    if "\\" in value or PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise CourierError(f"请使用仓库相对路径和 / 分隔符：{value!r}")
    path = PurePosixPath(value)
    if ".." in path.parts or ".git" in path.parts or str(path) != value or value == ".":
        raise CourierError(f"路径越界或未规范化：{value!r}")
    return path


def referenced_file(message):
    """按完整固定模板取路径，支持空格、中文及路径内的括号。"""
    if not isinstance(message, str) or not message:
        raise CourierError("message 不能为空")
    end = ")" + FILE_SUFFIX
    if message.startswith(FILE_PREFIX) and message.endswith(end):
        value = message[len(FILE_PREFIX):-len(end)]
        relative_path(value)
        return value
    if "@file(" in message:
        raise CourierError("文件消息必须使用固定模板：请阅读文件 @file(仓库相对路径)，按文件中的要求执行任务或回答问题。")
    return None


def freeze_file(repo, root, message_id, message):
    """仅复制明确引用文件的原始字节，不提交或修改发送者工作区。"""
    source = referenced_file(message)
    if source is None:
        return [], {}
    base = Path(repo).resolve()
    path = (base / source).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise CourierError(f"引用文件不存在或解析到仓库外：{source}")
    snapshot = f"{root}/payloads/{message_id}/{path.name}"
    relative_path(snapshot)
    return [{"source_path": source, "path": snapshot}], {snapshot: path.read_bytes()}


def validate_record(record, kind, path, root):
    """验证收到的数据，而不将消息正文转成代码或额外权限。"""
    if not isinstance(record, dict) or record.get("protocol_version") != VERSION:
        raise CourierError(f"未知或缺失的协议版本：{path}")
    time_key = "created_at" if kind == "messages" else "responded_at"
    id_key = "message_id" if kind == "messages" else "receipt_id"
    name(record.get(id_key))
    try:
        at = datetime.fromisoformat(record[time_key])
    except (ValueError, KeyError, TypeError) as exc:
        raise CourierError(f"无效时间：{path}") from exc
    if at.utcoffset() is None or path != f"{root}/{kind}/{at.date()}.jsonl":
        raise CourierError(f"记录日期、时区或文件路径不一致：{path}")
    if kind == "messages":
        if record.get("kind") not in {"request", "reply"}:
            raise CourierError(f"未知消息类型：{id_key}")
        for key in ("from",):
            validate_endpoint(record.get(key))
        for key in ("to", "recipients"):
            if not isinstance(record.get(key), list) or not record[key]:
                raise CourierError(f"缺失 {key}：{record[id_key]}")
            for value in record[key]:
                validate_endpoint(value, broadcast=key == "to")
            keys = [endpoint_key(v) for v in record[key]]
            if len(keys) != len(set(keys)):
                raise CourierError(f"重复收件端：{record[id_key]}")
        if record["kind"] == "reply":
            name(record.get("in_reply_to"))
        elif record.get("in_reply_to") is not None:
            raise CourierError("普通请求不能设置 in_reply_to")
        source = referenced_file(record.get("message"))
        expected = [] if source is None else [source]
        files = record.get("files")
        if (not isinstance(files, list) or any(not isinstance(f, dict) for f in files)
                or [f.get("source_path") for f in files] != expected):
            raise CourierError(f"文件引用映射与 message 不一致：{record[id_key]}")
        for file in files:
            relative_path(file.get("path"))
            prefix = f"{root}/payloads/{record[id_key]}/"
            if not file["path"].startswith(prefix):
                raise CourierError("文件快照必须归属于本消息目录")
    else:
        validate_endpoint(record.get("receiver"))
        name(record.get("message_id"))
        if record.get("status") not in STATUSES:
            raise CourierError("未知回执状态")
        commit = record.get("message_commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise CourierError("回执缺少原消息发布提交")
        if not isinstance(record.get("detail"), str) or not record["detail"].strip():
            raise CourierError("回执缺少实际依据 detail")


def validate_endpoint(value, broadcast=False):
    if not isinstance(value, dict) or set(value) != {"machine", "inbox"}:
        raise CourierError("端点必须包含 machine 和 inbox")
    if value["machine"] != "*" or not broadcast:
        name(value["machine"])
    name(value["inbox"])
