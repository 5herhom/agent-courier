"""通道配置和运行状态分开保存，不在仓库内写入凭据或机器身份文件。"""

import json
import math
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .protocol import CourierError, machine_id, name, relative_path, timestamp


def home():
    override = os.environ.get("AGENT_COURIER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "AgentCourier"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "agent-courier"


@dataclass
class Config:
    """一个本机端点的通道与显式可调整的运行策略。"""

    channel: str
    repo: str
    remote: str = "origin"
    branch: str = "agent-courier"
    protocol_root: str = "agent-courier"
    inbox: str = "default"
    poll_interval_seconds: float = 10
    agent_message_file_threshold_chars: int = 1000
    timestamp_offset: str = "+08:00"
    git_timeout_seconds: float = 30
    git_executable: str = ""
    failure_backoff_max_seconds: float = 60
    startup_timeout_seconds: float = 30
    task_restart_interval_seconds: int = 60
    task_restart_count: int = 999
    log_max_bytes: int = 5242880
    log_backup_count: int = 3
    publish_attempts: int = 3
    startup_mode: str = "user-login"
    description: str = ""
    git_backend: str = "native"
    wsl_distribution: str = "Ubuntu"
    git_ssh_command: str = ""
    adapter: dict = field(default_factory=dict)

    def validate(self):
        name(self.channel)
        name(self.inbox)
        relative_path(self.protocol_root)
        timestamp(self.timestamp_offset)
        if not self.remote or self.remote.startswith("-"):
            raise CourierError("remote 必须是明确的 Git 远端名称", stage="config")
        if not self.branch or self.branch.startswith("-"):
            raise CourierError("branch 必须是明确的分支名称", stage="config")
        if not Path(self.repo).is_dir():
            raise CourierError(f"仓库目录不存在：{self.repo}", stage="config")
        for key in ("poll_interval_seconds", "git_timeout_seconds", "failure_backoff_max_seconds", "startup_timeout_seconds"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise CourierError(f"{key} 必须为正数，实际值={value!r}", stage="config")
        for key in ("agent_message_file_threshold_chars", "publish_attempts", "task_restart_interval_seconds", "task_restart_count", "log_max_bytes", "log_backup_count"):
            value = getattr(self, key)
            if type(value) is not int or value <= 0:
                raise CourierError(f"{key} 必须为正整数，实际值={value!r}", stage="config")
        if self.failure_backoff_max_seconds < self.poll_interval_seconds:
            raise CourierError("退避上限不能小于轮询间隔", stage="config")
        if self.startup_mode != "user-login":
            raise CourierError("首版支持显式用户登录启动", stage="config")
        if self.git_backend not in {"native", "wsl"} or (self.git_backend == "wsl" and os.name != "nt"):
            raise CourierError("git_backend 仅支持 native 或 Windows 上的 wsl", stage="config")
        if not self.git_executable:
            self.git_executable = shutil.which("git") or ""
        if not Path(self.git_executable).is_absolute() or not Path(self.git_executable).is_file():
            raise CourierError("Git 需要可用的本机可执行文件绝对路径", stage="config")
        return self

    @property
    def actor(self):
        return {"machine": machine_id(), "inbox": self.inbox}

    @property
    def directory(self):
        return home() / "channels" / self.channel / self.inbox

    def save(self):
        self.validate()
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / "config.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, target)

    @classmethod
    def load(cls, channel, inbox="default"):
        name(channel)
        name(inbox)
        path = home() / "channels" / channel / inbox / "config.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CourierError(f"通道尚未安装：{channel}/{inbox}", stage="config") from exc
        config = cls(**value).validate()
        if (config.channel, config.inbox) != (channel, inbox):
            raise CourierError("配置路径与通道端点不一致", stage="config")
        return config
