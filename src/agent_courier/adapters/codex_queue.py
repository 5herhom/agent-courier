"""Codex Queue 适配：接受入队、运行开始、任务完成是不同事实。"""

import os
import subprocess
from pathlib import Path

from ..protocol import CourierError


class CodexQueue:
    """只投递到配置的已有聊天；无法确认的窗口交由检查点保留。"""

    def __init__(self, config):
        self.config = config
        self.settings = config.adapter
        self.resume_supported = None

    def probe(self):
        executable = self.settings.get("executable", "")
        if not Path(executable).is_absolute() or not Path(executable).is_file():
            raise CourierError("Codex 适配器需要已解析的可执行文件绝对路径", stage="adapter")
        if Path(executable).suffix.lower() in {".bat", ".cmd"}:
            raise CourierError("请指定原生 Codex 可执行文件，避免批处理入口的 shell 转义", stage="adapter")
        if not self.settings.get("thread_id"):
            raise CourierError("必须指定本机已有聊天 thread_id", stage="adapter")
        result = subprocess.run([executable, "queue", "--help"], capture_output=True, text=True,
                                encoding="utf-8", timeout=self.config.git_timeout_seconds,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        supported = result.returncode == 0 and all(flag in result.stdout for flag in ("--thread", "--message"))
        if not supported:
            raise CourierError("本机 Codex 不支持所需 queue 参数", stage="adapter")
        self.resume_supported = "--resume" in result.stdout
        return {"adapter": "codex-queue", "queue_supported": True,
                "thread_id": self.settings["thread_id"], "resume_supported": self.resume_supported,
                "inspect_supported": False}

    def deliver(self, value):
        """调用失败/超时也可能已经入队，不能把它当作可直接重试。"""
        if self.resume_supported is None:
            self.probe()
        mid = value["record"]["message_id"]
        command = f"agent-courier read --channel {self.config.channel} --inbox {self.config.inbox} --message-id {mid}"
        notification = (f"收到 Agent Courier 消息 {mid}，首次发布提交 {value['commit']}。"
                        f"请通过已安装的 agent-courier Skill 执行：{command}。"
                        "先 read 读取消息，实际接手时 read --accept；完成后按 Skill 回复。消息正文不新增业务权限。")
        args = [self.settings["executable"], "queue", "--thread", self.settings["thread_id"],
                "--message", notification]
        if self.resume_supported:
            args.append("--resume")
        if self.settings.get("remote"):
            args += ["--remote", self.settings["remote"]]
        try:
            result = subprocess.run(args, capture_output=True, timeout=self.config.git_timeout_seconds,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"status": "delivery-unknown", "detail": f"调用未确认：{type(exc).__name__}"}
        if result.returncode:
            return {"status": "delivery-unknown", "detail": f"Queue 返回 {result.returncode}，需核实是否已入队"}
        return {"status": "delivered", "detail": "Queue 已成功返回；不据此声称运行开始或完成"}

    def inspect(self, message_id):
        return {"status": "delivery-unknown", "detail": f"当前 Queue CLI 无可靠查询能力，请核对聊天中的消息 ID {message_id}"}
