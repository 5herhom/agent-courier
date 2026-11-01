"""独立裸仓库读写消息分支；不 checkout 远端内容，不触碰用户暂存区。"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .protocol import CourierError


def redact(value):
    value = re.sub(r"(https?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1<redacted>@", value)
    return re.sub(r"(?i)(bearer\s+|token[=: ]+)[\w.-]+", r"\1<redacted>", value)


class GitTransport:
    """只访问配置中同一 origin 的明确分支，并保留并发发布者的全部记录。"""

    def __init__(self, config):
        self.config = config
        self.cache = config.directory / "git"
        self.url = self.source("remote", "get-url", config.remote).strip().decode()
        if urlsplit(self.url).password or ("://" in self.url and urlsplit(self.url).username):
            raise CourierError("请将 Git 凭据交给凭据管理器，不在远端 URL 中内嵌凭据", stage="config")
        self.source("check-ref-format", "--branch", config.branch)
        if not self.cache.exists():
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.run("init", "--bare", str(self.cache), cwd=config.repo)
            self.git("remote", "add", "origin", self.url)
            self.git("config", "core.hooksPath", str(config.directory / "empty-hooks"))
            for key in ("user.name", "user.email"):
                value = self.source("config", "--get", key, check=False).strip()
                if value:
                    self.git("config", key, value.decode())
        if self.git("remote", "get-url", "origin").strip().decode() != self.url:
            raise CourierError("通道仓库远端已变化，需显式重新安装并核对旧检查点", stage="config")

    def run(self, *args, cwd=None, data=None, env=None, check=True):
        settings = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        if self.config.git_ssh_command:
            settings["GIT_SSH_COMMAND"] = self.config.git_ssh_command
        if env:
            settings.update(env)
        command = [self.config.git_executable or shutil.which("git") or "git", *args]
        # 本地对象操作使用原生 Git；Windows 网络操作按配置进入 WSL，避免每次读对象都启动 WSL。
        if self.config.git_backend == "wsl" and args[0] in {"ls-remote", "fetch", "push"}:
            command = ["wsl.exe", "-d", self.config.wsl_distribution, "--cd", self._linux_path(str(cwd)),
                       "--", "env", "GIT_TERMINAL_PROMPT=0"]
            if self.config.git_ssh_command:
                command.append("GIT_SSH_COMMAND=" + self.config.git_ssh_command)
            command += [f"{key}={self._linux_path(value)}" for key, value in (env or {}).items()]
            command += ["git", *(self._linux_path(arg) for arg in args)]
        try:
            result = subprocess.run(command, cwd=cwd, input=data, capture_output=True,
                                    env=settings, timeout=self.config.git_timeout_seconds,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as exc:
            raise CourierError(f"Git {args[0]} 超时，远端结果需要重新核对", stage="git") from exc
        if check and result.returncode:
            detail = redact(result.stderr.decode("utf-8", errors="replace").strip())
            raise CourierError(f"Git {args[0]} 失败：{detail}", stage="git")
        return result.stdout if check or result.returncode == 0 else b""

    @staticmethod
    def _linux_path(value):
        if re.match(r"^[A-Za-z]:[\\/]", value):
            return f"/mnt/{value[0].lower()}/" + value[3:].replace("\\", "/")
        return value

    def source(self, *args, **kwargs):
        return self.run(*args, cwd=self.config.repo, **kwargs)

    def git(self, *args, **kwargs):
        return self.run(*args, cwd=self.cache, **kwargs)

    def remote_head(self):
        data = self.git("ls-remote", "--refs", "origin", f"refs/heads/{self.config.branch}")
        rows = data.decode().splitlines()
        if not rows:
            return None
        if len(rows) != 1:
            raise CourierError("远端分支查询结果不唯一", stage="git")
        return rows[0].split()[0]

    def fetch(self, *, allow_missing=False):
        head = self.remote_head()
        if not head:
            if allow_missing:
                return None
            raise CourierError("通信分支不存在；仅安装时可显式 --create-branch", stage="git")
        ref = f"refs/heads/{self.config.branch}:refs/remotes/origin/courier"
        self.git("fetch", "--quiet", "--no-tags", "origin", ref)
        return self.git("rev-parse", "refs/remotes/origin/courier").decode().strip()

    def paths(self, commit, prefix):
        if commit is None:
            return []
        return [p.decode("utf-8") for p in self.git("ls-tree", "-r", "-z", "--name-only", commit, "--", prefix).split(b"\0") if p]

    def read(self, commit, path, *, missing=False):
        if commit is None:
            return b""
        if missing and path not in self.paths(commit, path):
            return b""
        return self.git("show", f"{commit}:{path}")

    def commits(self, baseline, head):
        if baseline:
            ancestor = self.git("merge-base", baseline, head).decode().strip()
            if ancestor != baseline:
                raise CourierError("通信分支历史被回退或重写，停止投递", stage="history")
        rev = f"{baseline}..{head}" if baseline else head
        return self.git("rev-list", "--reverse", "--first-parent", rev).decode().splitlines()

    def changed(self, commit):
        return [p.decode() for p in self.git("diff-tree", "--root", "--first-parent", "--no-commit-id", "--name-only", "-r", "-z", commit).split(b"\0") if p]

    def publish(self, base, changes, subject):
        """用临时 index 构造普通提交，不执行 hooks、过滤器或远端文件。"""
        with tempfile.TemporaryDirectory(prefix="courier-index-") as directory:
            env = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
            self.git("read-tree", base or "--empty", env=env)
            for path, value in sorted(changes.items()):
                blob = self.git("hash-object", "-w", "--stdin", data=value).decode().strip()
                self.git("update-index", "--add", "--cacheinfo", "100644", blob, path, env=env)
            tree = self.git("write-tree", env=env).decode().strip()
            args = ["commit-tree", tree]
            if base:
                args += ["-p", base]
            commit = self.git(*args, data=(subject + "\n").encode()).decode().strip()
        self.git("push", "--quiet", "origin", f"{commit}:refs/heads/{self.config.branch}")
        remote = self.fetch()
        if self.git("merge-base", commit, remote).decode().strip() != commit:
            raise CourierError("push 后远端未包含本次提交", stage="git")
        return commit
