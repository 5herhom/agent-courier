"""Linux 当前用户 systemd unit；不擅自设置 linger 或切换账户。"""

import subprocess
import sys
from pathlib import Path

from ..protocol import CourierError


def quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$").replace("\n", "\\n") + '"'


class LinuxService:
    """登录后的用户服务，任务定义持久保存，但不宣称无人登录开机运行。"""

    def __init__(self, config):
        self.config = config
        self.name = f"agent-courier-{config.channel}-{config.inbox}.service"
        self.unit = Path.home() / ".config/systemd/user" / self.name

    def command(self, *args, check=True):
        result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                                timeout=self.config.git_timeout_seconds)
        if check and result.returncode:
            raise CourierError(f"systemd 用户服务失败：{result.stderr.strip()}", stage="startup")
        return result

    def check(self):
        self.command("show-environment")

    def prepare(self, runner):
        self.unit.parent.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, str(runner), "watch", "--channel", self.config.channel,
                "--inbox", self.config.inbox]
        content = ("[Unit]\nDescription=Agent Courier Git listener\nStartLimitIntervalSec=0\n\n"
                   "[Service]\nType=simple\n"
                   f"ExecStart={' '.join(quote(a) for a in args)}\n"
                   f"Restart=on-failure\nRestartSec={self.config.poll_interval_seconds}\n\n"
                   "[Install]\nWantedBy=default.target\n")
        self.unit.write_text(content, encoding="utf-8")
        self.command("daemon-reload")

    def start(self):
        self.command("enable", "--now", self.name)

    def status(self):
        state = self.command("show", self.name, "--property=LoadState,ActiveState,SubState,UnitFileState,MainPID", check=False)
        values = dict(line.split("=", 1) for line in state.stdout.splitlines() if "=" in line)
        return {"name": self.name, "unit": str(self.unit), "registered": self.unit.exists(),
                "running": values.get("ActiveState") == "active", **values}

    def disable(self, *, uninstall=False):
        self.command("disable", self.name, check=self.unit.exists())
        # 配置有误、尚未加载的单元也必须能取消自启动并卸载。
        self.command("stop", self.name, check=False)
        if self.status()["running"]:
            raise CourierError("用户服务尚未停止，保留单元等待核实", stage="startup")
        if uninstall and self.unit.exists():
            self.unit.unlink()
            self.command("daemon-reload")
