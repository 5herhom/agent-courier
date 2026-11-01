"""使用真实本地 bare 远端验证跨端收发，不调用真实 Agent 或生产系统。"""

import os
from argparse import Namespace
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agent_courier.config import Config
from agent_courier.cli import execute
from agent_courier.listener import poll
from agent_courier.protocol import CourierError, encode
from agent_courier.service import Courier


class GitFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"AGENT_COURIER_HOME": str(self.root / "state")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "source"
        self.run_git("init", "--bare", str(self.remote))
        self.run_git("init", str(self.repo))
        for key, value in (("user.name", "Courier Test"), ("user.email", "courier@example.invalid")):
            self.run_git("-C", str(self.repo), "config", key, value)
        self.run_git("-C", str(self.repo), "remote", "add", "origin", str(self.remote))
        (self.repo / "README.md").write_text("business workspace\n", encoding="utf-8")
        self.run_git("-C", str(self.repo), "add", "README.md")
        self.run_git("-C", str(self.repo), "commit", "-m", "initial business commit")
        for actor in ("alpha", "beta"):
            with self.client(actor) as courier:
                courier.register(create_branch=actor == "alpha")

    def run_git(self, *args):
        result = subprocess.run(["git", *args], capture_output=True, check=True)
        return result.stdout

    def config(self, actor):
        return Config(channel=actor, repo=str(self.repo))

    @contextmanager
    def client(self, actor):
        with patch("agent_courier.config.machine_id", return_value=actor):
            with Courier(self.config(actor)) as courier:
                yield courier

    def send(self, message="请检查并回复", actor="alpha", target="beta"):
        with self.client(actor) as courier:
            return courier.send(message, [target])

    def test_file_round_trip_freezes_bytes_and_preserves_wip(self):
        source = self.repo / "中文 SQL (草稿).md"
        content = "SELECT '中文', '\\n', '$HOME', '`not executed`';\n😀\n".encode()
        source.write_bytes(content)
        (self.repo / "README.md").write_text("staged business WIP\n", encoding="utf-8")
        self.run_git("-C", str(self.repo), "add", "README.md")
        status = self.run_git("-C", str(self.repo), "status", "--porcelain=v1", "-z")
        index = self.run_git("-C", str(self.repo), "write-tree")
        message = "请阅读文件 @file(中文 SQL (草稿).md)，按文件中的要求执行任务或回答问题。"
        sent = self.send(message)
        source.write_text("changed after publish", encoding="utf-8")
        with self.client("beta") as courier:
            read = courier.read(sent["message_id"])
            self.assertEqual(Path(read["files"][0]["local_path"]).read_bytes(), content)
            self.assertIsNone(courier.state.latest_receipt(sent["message_id"], courier.config.actor))
            courier.read(sent["message_id"], accept=True)
            receipt = courier.state.latest_receipt(sent["message_id"], courier.config.actor)
            self.assertEqual(receipt["status"], "started")
        self.assertEqual(self.run_git("-C", str(self.repo), "status", "--porcelain=v1", "-z"), status)
        self.assertEqual(self.run_git("-C", str(self.repo), "write-tree"), index)

    def test_reply_and_completion_are_linked_to_existing_original_commit(self):
        sent = self.send()
        with self.client("beta") as courier:
            reply = courier.send("检查已完成", in_reply_to=sent["message_id"], complete=True)
        with self.client("alpha") as courier:
            result = courier.read(reply["message_id"])
            self.assertEqual(result["record"]["in_reply_to"], sent["message_id"])
            receipt = courier.state.latest_receipt(sent["message_id"], {"machine": "beta", "inbox": "default"})
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["message_commit"], sent["commit"])

    def test_broadcast_freezes_recipients_and_receipts_are_per_endpoint(self):
        with self.client("gamma") as courier:
            courier.register()
        sent = self.send(target="*")
        self.assertEqual([r["machine"] for r in sent["recipients"]], ["beta", "gamma"])
        with self.client("delta") as courier:
            courier.register()
            courier.refresh()
            self.assertNotIn(courier.config.actor, courier.state.message(sent["message_id"])["record"]["recipients"])
        with self.client("beta") as courier:
            courier.ack(sent["message_id"], "completed", "已完成本机检查")
        with self.client("gamma") as courier:
            courier.refresh()
            self.assertIsNone(courier.state.latest_receipt(sent["message_id"], courier.config.actor))

    def test_invalid_paths_and_broken_template_fail_before_publication(self):
        for message in ["请看 @file(missing.md)",
                        "请阅读文件 @file(../outside.md)，按文件中的要求执行任务或回答问题。",
                        "请阅读文件 @file(C:/outside.md)，按文件中的要求执行任务或回答问题。"]:
            with self.subTest(message=message), self.client("alpha") as courier:
                head = courier.git.remote_head()
                with self.assertRaises(CourierError):
                    courier.send(message, ["beta"])
                self.assertEqual(courier.git.remote_head(), head)

    def test_history_rewrite_stops_without_advancing_trusted_baseline(self):
        sent = self.send()
        with self.client("beta") as courier:
            courier.refresh()
            baseline = courier.state.get("baseline")
        with self.client("alpha") as courier:
            head = courier.refresh()
            original = courier.state.message(sent["message_id"])
            changed = {**original["record"], "message": "修改已发布任务"}
            courier.git.publish(head, {original["path"]: (encode(changed) + "\n").encode()}, "invalid rewrite")
        with self.client("beta") as courier:
            with self.assertRaises(CourierError):
                courier.refresh()
            self.assertEqual(courier.state.get("baseline"), baseline)

    def test_ambiguous_delivery_after_crash_is_not_requeued(self):
        sent = self.send()
        config = self.config("beta")
        config.adapter = {"type": "codex-queue"}
        with patch("agent_courier.config.machine_id", return_value="beta"), \
             patch("agent_courier.listener.CodexQueue.probe"), \
             patch("agent_courier.listener.CodexQueue.deliver", side_effect=SystemExit("simulated crash")) as deliver:
            with self.assertRaises(SystemExit):
                poll(config)
            self.assertEqual(deliver.call_count, 1)
        with patch("agent_courier.config.machine_id", return_value="beta"), \
             patch("agent_courier.listener.CodexQueue.deliver") as deliver:
            poll(config)
            deliver.assert_not_called()
        with self.client("beta") as courier:
            self.assertEqual(courier.state.dispatch(sent["message_id"])["status"], "delivery-unknown")

    def test_concurrent_publisher_is_preserved_on_rejected_push(self):
        with self.client("alpha") as courier:
            publish = courier.git.publish
            calls = []

            def concurrent(base, changes, subject):
                if not calls:
                    calls.append(self.send("并发消息", actor="beta", target="alpha"))
                return publish(base, changes, subject)

            with patch.object(courier.git, "publish", side_effect=concurrent):
                sent = courier.send("原始消息", ["beta"])
            courier.refresh()
            self.assertEqual(courier.state.db.execute("SELECT count(*) FROM messages").fetchone()[0], 2)
            self.assertEqual(courier.state.message(calls[0]["message_id"])["record"]["message"], "并发消息")
            self.assertEqual(courier.state.message(sent["message_id"])["record"]["message"], "原始消息")

    def test_offline_outbox_recovers_same_id_and_original_file_bytes(self):
        source = self.repo / "offline.md"
        source.write_bytes(b"original snapshot\n")
        message = "请阅读文件 @file(offline.md)，按文件中的要求执行任务或回答问题。"
        with self.client("alpha") as courier:
            with patch.object(courier.git, "publish", side_effect=CourierError("offline", stage="git")):
                with self.assertRaises(CourierError):
                    courier.send(message, ["beta"])
            mid = courier.state.db.execute("SELECT id FROM outbox WHERE kind='messages'").fetchone()[0]
        source.write_bytes(b"changed after failure\n")
        with self.client("alpha") as courier:
            courier.flush()
            self.assertEqual(courier.state.db.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
        with self.client("beta") as courier:
            result = courier.read(mid)
            self.assertEqual(Path(result["files"][0]["local_path"]).read_bytes(), b"original snapshot\n")

    def test_verified_retry_resumes_pending_even_without_new_remote_head(self):
        sent = self.send()
        mid = sent["message_id"]
        with self.client("beta") as courier:
            courier.ack(mid, "delivery-unknown", "模拟旧调用未确认")
            courier.state.set_dispatch(mid, "delivery-unknown", "待核实")
            config = courier.config
            config.adapter = {"type": "codex-queue"}
            config.save()
        args = Namespace(command="retry", channel="beta", inbox="default", message_id=mid,
                         verified_not_delivered=True, evidence="测试队列确认没有入队")
        with patch("agent_courier.config.machine_id", return_value="beta"):
            execute(args)
            with patch("agent_courier.listener.CodexQueue.probe"), \
                 patch("agent_courier.listener.CodexQueue.deliver", return_value={"status": "delivered", "detail": "测试队列接受"}) as deliver:
                poll(config)
                poll(config)
                self.assertEqual(deliver.call_count, 1)

    def test_receipt_or_participant_only_changes_do_not_wake_agent(self):
        self.send()
        with self.client("beta") as courier:
            courier.register()
            courier.ack(courier.state.db.execute("SELECT id FROM messages").fetchone()[0], "received", "只读确认")
        config = self.config("alpha")
        config.adapter = {"type": "codex-queue"}
        with patch("agent_courier.config.machine_id", return_value="alpha"), \
             patch("agent_courier.listener.CodexQueue.deliver") as deliver:
            poll(config)
            deliver.assert_not_called()

    def test_cross_day_offline_read_and_reply_uses_original_commit(self):
        with patch("agent_courier.service.timestamp", return_value="2026-10-30T23:59:59+08:00"):
            sent = self.send()
        with patch("agent_courier.service.timestamp", return_value="2026-11-01T22:00:00+08:00"):
            with self.client("beta") as courier:
                reply = courier.send("跨日检查完成", in_reply_to=sent["message_id"], complete=True)
        with self.client("alpha") as courier:
            original = courier.read(sent["message_id"])
            self.assertTrue(original["path"].endswith("2026-10-30.jsonl"))
            self.assertEqual(original["commit"], sent["commit"])
            self.assertTrue(courier.read(reply["message_id"])["path"].endswith("2026-11-01.jsonl"))


if __name__ == "__main__":
    unittest.main()
