"""进程退出即释放的跨平台文件锁；锁文件存在不等于进程仍运行。"""

import errno
import os
import time
from contextlib import contextmanager

from .protocol import CourierError


@contextmanager
def locked(path, *, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            # LK_LOCK 最多只等约十秒；操作锁须像 flock 一样等待现有操作结束。
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if not blocking or exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise CourierError("本端点已有进程持有运行锁", stage="lock") from exc
                    time.sleep(0.1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise CourierError("本端点已有进程持有运行锁", stage="lock") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
