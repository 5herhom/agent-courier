"""实际子进程验证单实例拒绝与长操作等待，避免 Windows 十秒上限回归。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agent_courier.locking import locked
from agent_courier.protocol import CourierError


class ProcessLockTests(unittest.TestCase):
    def test_wait_for_existing_operation_and_reject_second_listener(self):
        duration = 10.5 if os.name == "nt" else 0.3
        child_code = ("import sys,time; from pathlib import Path; "
                      "from agent_courier.locking import locked\n"
                      "with locked(Path(sys.argv[1])):\n"
                      " print('locked',flush=True)\n"
                      " time.sleep(float(sys.argv[2]))\n")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "operation.lock"
            child = subprocess.Popen([sys.executable, "-c", child_code, str(path), str(duration)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "locked")
                with self.assertRaises(CourierError):
                    with locked(path, blocking=False):
                        self.fail("second listener acquired lock")
                started = time.monotonic()
                with locked(path):
                    self.assertGreater(time.monotonic() - started, duration - 0.2)
                _, errors = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, errors)
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate()
