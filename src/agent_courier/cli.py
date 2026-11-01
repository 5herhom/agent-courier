"""面向 Agent 的简短参数入口；所有协议字段由程序生成。"""

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .listener import watch
from .protocol import CourierError, STATUSES
from .service import Courier


def parser():
    result = argparse.ArgumentParser(prog="agent-courier")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    for command in ("send", "reply", "read", "ack", "watch", "status", "retry",
                    "install-listener", "disable-listener", "uninstall-listener", "install-skill"):
        p = commands.add_parser(command)
        p.add_argument("--channel", default="work")
        p.add_argument("--inbox", default="default")
        if command in {"send", "reply"}:
            p.add_argument("--message", required=True)
        if command == "send":
            p.add_argument("--to", action="append", required=True)
        if command == "reply":
            p.add_argument("--in-reply-to", required=True)
            p.add_argument("--complete", action="store_true")
        if command in {"read", "ack", "retry"}:
            p.add_argument("--message-id", required=True)
        if command == "read":
            p.add_argument("--accept", action="store_true")
        if command == "ack":
            p.add_argument("--status", choices=sorted(STATUSES), required=True)
            p.add_argument("--detail", required=True)
        if command == "watch":
            p.add_argument("--once", action="store_true")
        if command == "status":
            p.add_argument("--sync", action="store_true")
        if command == "retry":
            p.add_argument("--verified-not-delivered", action="store_true")
            p.add_argument("--evidence")
        if command == "install-listener":
            p.add_argument("--repo", required=True)
            p.add_argument("--remote", default="origin")
            p.add_argument("--branch", default="agent-courier")
            p.add_argument("--protocol-root", default="agent-courier")
            p.add_argument("--poll-interval-seconds", type=float, default=10)
            p.add_argument("--git-timeout-seconds", type=float, default=30)
            p.add_argument("--file-threshold-chars", type=int, default=1000)
            p.add_argument("--git-backend", choices=["native", "wsl"], default="native")
            p.add_argument("--wsl-distribution", default="Ubuntu")
            p.add_argument("--git-ssh-command", default="", help="本机受信任的 Git SSH 启动配置，不从消息读取")
            p.add_argument("--codex-executable")
            p.add_argument("--thread-id")
            p.add_argument("--codex-remote")
            p.add_argument("--description", default="")
            p.add_argument("--create-branch", action="store_true")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--manual", action="store_true", help="只配置端点，不注册系统任务；供验收或手工监听")
        if command == "install-skill":
            p.add_argument("--target", required=True)
    return result


def execute(args):
    if args.command == "install-listener":
        from .startup import install
        adapter = {}
        if args.codex_executable or args.thread_id:
            if not args.codex_executable or not args.thread_id:
                raise CourierError("请同时指定 --codex-executable 与 --thread-id", stage="config")
            adapter = {"type": "codex-queue", "executable": str(Path(args.codex_executable).resolve()),
                       "thread_id": args.thread_id}
            if args.codex_remote:
                adapter["remote"] = args.codex_remote
        config = Config(channel=args.channel, inbox=args.inbox, repo=str(Path(args.repo).resolve()),
                        remote=args.remote, branch=args.branch, protocol_root=args.protocol_root,
                        poll_interval_seconds=args.poll_interval_seconds,
                        failure_backoff_max_seconds=max(60, args.poll_interval_seconds),
                        git_timeout_seconds=args.git_timeout_seconds,
                        agent_message_file_threshold_chars=args.file_threshold_chars,
                        git_backend=args.git_backend, wsl_distribution=args.wsl_distribution,
                        git_ssh_command=args.git_ssh_command,
                        description=args.description, adapter=adapter).validate()
        return install(config, dry_run=args.dry_run, manual=args.manual, create_branch=args.create_branch)
    if args.command == "install-skill":
        from .skill import install_skill
        return install_skill(Path(args.target))
    config = Config.load(args.channel, args.inbox)
    if args.command == "watch":
        return watch(config, once=args.once)
    if args.command in {"disable-listener", "uninstall-listener"}:
        from .startup import disable
        return disable(config, uninstall=args.command == "uninstall-listener")
    with Courier(config) as courier:
        if args.command == "send":
            return courier.send(args.message, args.to)
        if args.command == "reply":
            return courier.send(args.message, in_reply_to=args.in_reply_to, complete=args.complete)
        if args.command == "read":
            return courier.read(args.message_id, accept=args.accept)
        if args.command == "ack":
            return courier.ack(args.message_id, args.status, args.detail)
        if args.command == "status":
            if args.sync:
                courier.refresh()
            from .startup import backend
            return {**courier.status(), "scheduler": backend(config).status()}
        if args.command == "retry":
            courier.flush()
            courier.refresh()
            original = courier.state.message(args.message_id)["record"]
            if config.actor not in original["recipients"]:
                raise CourierError("只能恢复本机收件端的投递")
            current = courier.state.dispatch(args.message_id)
            if not args.verified_not_delivered:
                return {"dispatch": current, "action": "请先核实原聊天或运行记录；未重新投递"}
            if not args.evidence or not args.evidence.strip():
                raise CourierError("重新投递需 --evidence 说明已核实没有入队的证据")
            latest = courier.state.latest_receipt(args.message_id, config.actor)
            if latest and latest["status"] in {"delivered", "started", "completed"}:
                raise CourierError("已有接受或执行证据，不能重新入队")
            courier.state.set_dispatch(args.message_id, "ready", args.evidence)
            return {"message_id": args.message_id, "verified_not_delivered": True, "evidence": args.evidence}
    raise CourierError("未知命令")


def main(argv=None):
    # 跨 Windows 控制台与后台管道保持同一种 JSON 文本编码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (CourierError, OSError, ValueError, TypeError) as exc:
        stage = exc.stage if isinstance(exc, CourierError) else "local"
        print(json.dumps({"ok": False, "stage": stage, "error": str(exc)}, ensure_ascii=False))
        return 1
