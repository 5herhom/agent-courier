"""将同版本 Python 核心随 Skill 一起安装，不依赖开发机的源码路径。"""

import shutil
from pathlib import Path

from . import __version__
from .protocol import CourierError


def install_skill(target):
    """显式选择 Skill 目录；保留已有非本工具文件，不递归删除目录。"""
    target = target.expanduser().resolve()
    package = Path(__file__).resolve().parent
    scripts = target / "scripts"
    destination = scripts / "agent_courier"
    if destination == package or destination.is_relative_to(package):
        raise CourierError("Skill 安装目录不能位于 Python 源码包内部")
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copytree(package, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(package / "resources/SKILL.md", target / "SKILL.md")
    (scripts / "courier.py").write_text(
        "from agent_courier.cli import main\nraise SystemExit(main())\n", encoding="utf-8")
    return {"installed_skill": str(target), "version": __version__,
            "launcher": str(scripts / "courier.py"), "python_minimum": "3.11"}
