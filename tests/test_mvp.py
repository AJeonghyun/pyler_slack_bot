from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest

from db import VoteDatabase
from slack_blocks import build_vote_blocks, build_vote_fallback_text, format_close_summary


class FakeSlackClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []

    def chat_postMessage(self, **kwargs: dict) -> dict[str, str]:
        self.posts.append(kwargs)
        return {"ts": f"900.{len(self.posts):03d}"}

    def chat_update(self, **kwargs: dict) -> dict[str, bool]:
        self.updates.append(kwargs)
        return {"ok": True}


class VoteDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = os.path.join(tempfile.gettempdir(), "labeling_vote_bot_test.db")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.db = VoteDatabase(self.db_path)
        self.db.init_db()

    def tearDown(self) -> None:
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_case_vote_stats_and_close_are_idempotent(self) -> None:
        case, created = self.db.create_case_if_absent("C1", "123.456", "U1")
        duplicate, duplicate_created = self.db.create_case_if_absent("C1", "123.456", "U2")

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(case["case_id"], duplicate["case_id"])

        self.db.set_vote_message_ts(case["case_id"], "124.000")
        self.db.upsert_vote(case["case_id"], "U1", 3)
        self.db.upsert_vote(case["case_id"], "U2", 4)
        self.db.upsert_vote(case["case_id"], "U1", 4)

        stats = self.db.get_vote_stats(case["case_id"])
        self.assertEqual(stats["counts"][4], 2)
        self.assertEqual(stats["total_voters"], 2)
        self.assertEqual(round(stats["average"], 2), 4.00)
        self.assertEqual(stats["modes"], [4])

        closed_case, newly_closed = self.db.close_case(case["case_id"])
        closed_case_again, newly_closed_again = self.db.close_case(case["case_id"])

        self.assertTrue(newly_closed)
        self.assertFalse(newly_closed_again)
        self.assertEqual(closed_case["status"], "closed")
        self.assertEqual(closed_case_again["status"], "closed")

        blocks = build_vote_blocks(closed_case, stats)
        self.assertTrue(all(block.get("type") != "actions" for block in blocks))
        self.assertIn("CASE-", build_vote_fallback_text(closed_case, stats))
        self.assertIn("투표가 마감되었습니다.", format_close_summary(stats))


class SlackHandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = os.path.join(tempfile.gettempdir(), "labeling_vote_bot_handler_test.db")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"
        os.environ["SLACK_APP_TOKEN"] = "xapp-test"
        os.environ["SLACK_SIGNING_SECRET"] = "test"
        os.environ["DB_PATH"] = self.db_path
        os.environ["VOTE_TRIGGER_REACTION"] = "vote"

        import app

        self.app_module = importlib.reload(app)
        self.client = FakeSlackClient()

    def tearDown(self) -> None:
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_reaction_vote_revote_and_close_flow(self) -> None:
        event = {
            "reaction": "vote",
            "user": "U1",
            "item": {"channel": "C1", "ts": "123.456"},
        }
        self.app_module.handle_reaction_added(event, self.client)
        self.app_module.handle_reaction_added(event, self.client)

        self.assertEqual(len(self.client.posts), 1)

        case = self.app_module.db.create_case_if_absent("C1", "123.456", "U1")[0]
        case_id = case["case_id"]
        acks: list[str] = []

        self.app_module.handle_vote_score(
            lambda: acks.append("vote"),
            {"user": {"id": "U2"}},
            {"value": json.dumps({"case_id": case_id, "score": 3})},
            self.client,
        )
        self.app_module.handle_vote_score(
            lambda: acks.append("revote"),
            {"user": {"id": "U2"}},
            {"value": json.dumps({"case_id": case_id, "score": 5})},
            self.client,
        )

        stats = self.app_module.db.get_vote_stats(case_id)
        self.assertEqual(acks, ["vote", "revote"])
        self.assertEqual(stats["counts"][5], 1)
        self.assertEqual(stats["total_voters"], 1)
        self.assertEqual(len(self.client.updates), 2)

        self.app_module.handle_close_vote(
            lambda: acks.append("close"),
            {"user": {"id": "U3"}},
            {"value": json.dumps({"case_id": case_id})},
            self.client,
        )
        self.app_module.handle_close_vote(
            lambda: acks.append("close_again"),
            {"user": {"id": "U3"}},
            {"value": json.dumps({"case_id": case_id})},
            self.client,
        )

        self.assertEqual(acks[-2:], ["close", "close_again"])
        self.assertEqual(len(self.client.posts), 2)
        self.assertIn("투표가 마감되었습니다.", self.client.posts[-1]["text"])
        self.assertEqual(self.app_module.db.get_case(case_id)["status"], "closed")

        self.app_module.handle_vote_score(
            lambda: acks.append("closed_vote"),
            {"user": {"id": "U4"}},
            {"value": json.dumps({"case_id": case_id, "score": 0})},
            self.client,
        )
        self.assertEqual(self.app_module.db.get_vote_stats(case_id)["total_voters"], 1)


if __name__ == "__main__":
    unittest.main()
