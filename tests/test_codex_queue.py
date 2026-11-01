"""兼容实机存在的两种 Queue CLI，不发送真实模型通知。"""

import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_courier.adapters.codex_queue import CodexQueue


class QueueCompatibilityTests(unittest.TestCase):
    def test_queue_with_and_without_optional_resume(self):
        config = SimpleNamespace(adapter={"executable": sys.executable, "thread_id": "existing-chat"},
                                 git_timeout_seconds=5, channel="work", inbox="default")
        for resume in (False, True):
            with self.subTest(resume=resume):
                help_text = "queue --thread <ID> --message <TEXT> --remote <HOST>"
                if resume:
                    help_text += " --resume"
                results = [subprocess.CompletedProcess([], 0, stdout=help_text),
                           subprocess.CompletedProcess([], 0, stdout=b"queued")]
                with patch("agent_courier.adapters.codex_queue.subprocess.run", side_effect=results) as run:
                    adapter = CodexQueue(config)
                    self.assertEqual(adapter.probe()["resume_supported"], resume)
                    result = adapter.deliver({"record": {"message_id": "test-message"}, "commit": "a" * 40})
                args = run.call_args.args[0]
                self.assertEqual("--resume" in args, resume)
                self.assertEqual(args[args.index("--thread") + 1], "existing-chat")
                self.assertIn("--message-id test-message", args[args.index("--message") + 1])
                self.assertEqual(result["status"], "delivered")
