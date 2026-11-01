"""Windows 当前账户登录触发任务；原生 API 状态通过 PowerShell 查询。"""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from ..protocol import CourierError


class WindowsTask:
    """隐藏运行持久计划任务，账户与桌面 Agent 使用同一当前用户。"""

    def __init__(self, config):
        self.config = config
        self.name = f"AgentCourier-{config.channel}-{config.inbox}"

    def command(self, *args, check=True):
        result = subprocess.run(["schtasks.exe", *args], capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                timeout=self.config.git_timeout_seconds)
        if check and result.returncode:
            raise CourierError(f"计划任务操作失败，退出码={result.returncode}", stage="startup")
        return result

    def check(self):
        self.command("/Query", "/FO", "CSV")

    def prepare(self, runner):
        ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        ET.register_namespace("", ns)
        def element(parent, tag, text=None):
            item = ET.SubElement(parent, "{" + ns + "}" + tag)
            item.text = text
            return item
        task = ET.Element("{" + ns + "}Task", {"version": "1.2"})
        account = subprocess.run(["whoami.exe"], capture_output=True, check=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW).stdout.decode("mbcs").strip()
        triggers = element(task, "Triggers")
        trigger = element(triggers, "LogonTrigger")
        element(trigger, "Enabled", "true")
        element(trigger, "UserId", account)
        # 同一个任务定期尝试拉起；IgnoreNew 在监听存活时跳过，不创建模型任务。
        guard = element(triggers, "TimeTrigger")
        repetition = element(guard, "Repetition")
        element(repetition, "Interval", f"PT{self.config.task_restart_interval_seconds}S")
        element(repetition, "StopAtDurationEnd", "false")
        element(guard, "StartBoundary", datetime.now().astimezone().isoformat(timespec="seconds"))
        element(guard, "Enabled", "true")
        principal = element(element(task, "Principals"), "Principal")
        principal.set("id", "Author")
        element(principal, "UserId", account)
        element(principal, "LogonType", "InteractiveToken")
        element(principal, "RunLevel", "LeastPrivilege")
        settings = element(task, "Settings")
        for key, value in {"MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "false",
                           "StopIfGoingOnBatteries": "false", "StartWhenAvailable": "true",
                           "Enabled": "false", "Hidden": "true", "ExecutionTimeLimit": "PT0S"}.items():
            element(settings, key, value)
        restart = element(settings, "RestartOnFailure")
        element(restart, "Interval", f"PT{self.config.task_restart_interval_seconds}S")
        element(restart, "Count", str(self.config.task_restart_count))
        # 网络失败由监听循环退避；进程异常退出由计划任务恢复。
        action = element(element(task, "Actions"), "Exec")
        executable = Path(sys.executable).with_name("pythonw.exe")
        element(action, "Command", str(executable if executable.exists() else Path(sys.executable)))
        element(action, "Arguments", subprocess.list2cmdline([str(runner), "watch", "--channel", self.config.channel,
                                                              "--inbox", self.config.inbox]))
        element(action, "WorkingDirectory", str(self.config.directory))
        path = self.config.directory / "task.xml"
        ET.ElementTree(task).write(path, encoding="utf-16", xml_declaration=True)
        self.command("/Create", "/TN", self.name, "/XML", str(path), "/F")

    def start(self):
        self.command("/Change", "/TN", self.name, "/ENABLE")
        self.command("/Run", "/TN", self.name)

    def status(self):
        env = dict(os.environ, AGENT_COURIER_TASK=self.name)
        script = ("$t=Get-ScheduledTask -TaskName $env:AGENT_COURIER_TASK -ErrorAction SilentlyContinue;"
                  "if($t){@{registered=$true;running=($t.State -eq 'Running');state=[string]$t.State}|ConvertTo-Json -Compress}"
                  "else{@{registered=$false;running=$false}|ConvertTo-Json -Compress}")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW,
                                timeout=self.config.git_timeout_seconds)
        if result.returncode:
            raise CourierError("无法查询计划任务真实状态", stage="startup")
        return {"name": self.name, **json.loads(result.stdout.decode("utf-8-sig"))}

    def disable(self, *, uninstall=False):
        if not self.status().get("registered"):
            return
        self.command("/Change", "/TN", self.name, "/DISABLE")
        self.command("/End", "/TN", self.name, check=False)
        if uninstall:
            self.command("/Delete", "/TN", self.name, "/F")
