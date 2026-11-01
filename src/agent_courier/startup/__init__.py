"""持久任务注册与本机停止优先；注册信息不能代替运行证据。"""

import json
import os
import sys
import time
from pathlib import Path

from ..adapters.codex_queue import CodexQueue
from .. import __version__
from ..config import home
from ..protocol import CourierError
from ..service import Courier


def backend(config):
    if os.name == "nt":
        from .windows import WindowsTask
        return WindowsTask(config)
    if sys.platform == "linux":
        from .linux import LinuxService
        return LinuxService(config)
    raise CourierError("当前平台没有支持的持久任务后端", stage="startup")


def runner(config):
    """任务使用稳定绝对路径；安装目录与非交互 PATH 无关。"""
    target = config.directory / "run.py"
    package_parent = str(Path(__file__).resolve().parents[2])
    text = ("import os,sys\n"
            f"os.environ['AGENT_COURIER_HOME']={str(home())!r}\n"
            f"sys.path.insert(0,{package_parent!r})\n"
            "from agent_courier.cli import main\n"
            "raise SystemExit(main())\n")
    target.write_text(text, encoding="utf-8")
    return target


def install(config, *, dry_run=False, manual=False, create_branch=False):
    """预览零副作用；实际安装失败时取消本机自启动，保留可核对配置。"""
    plan = {"config": config.__dict__, "directory": str(config.directory),
            "task": f"agent-courier-{config.channel}-{config.inbox}",
            "manual": manual, "create_branch": create_branch, "dry_run": dry_run}
    if dry_run:
        return plan
    if not manual:
        if config.adapter.get("type") != "codex-queue":
            raise CourierError("自动监听须配置支持的 Agent 适配器", stage="startup")
        CodexQueue(config).probe()
    config.save()
    target = runner(config)
    scheduler = None if manual else backend(config)
    try:
        if scheduler:
            scheduler.check()
            if scheduler.status().get("registered"):
                scheduler.disable(uninstall=False)
            scheduler.prepare(target)
        with Courier(config) as courier:
            registered = courier.register(create_branch=create_branch)
        if scheduler:
            scheduler.start()
            deadline = time.monotonic() + config.startup_timeout_seconds
            while True:
                state = scheduler.status()
                if state.get("running"):
                    break
                if time.monotonic() >= deadline:
                    raise CourierError(f"任务已写入但未运行：{state}", stage="startup")
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        else:
            state = {"mode": "manual", "running": False}
        manifest = {"version": __version__, "python": sys.executable,
                    "package": str(Path(__file__).resolve().parents[1]),
                    "git_executable": config.git_executable, "scheduler": state}
        (config.directory / "installation.json").write_text(json.dumps(manifest), encoding="utf-8")
        return {**plan, **registered, "installation": manifest, "scheduler": state}
    except BaseException:
        if scheduler:
            try:
                scheduler.disable(uninstall=False)
            except CourierError:
                pass
        raise


def disable(config, *, uninstall=False):
    """即使远端不可达，也先停止本机任务并关闭下次登录触发。"""
    scheduler = backend(config)
    scheduler.disable(uninstall=uninstall)
    result = {"local_stopped": True, "uninstalled": uninstall, "scheduler": scheduler.status()}
    try:
        with Courier(config) as courier:
            result.update(courier.register(enabled=False))
    except CourierError as exc:
        result["registration_update_error"] = str(exc)
    return result
