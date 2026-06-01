from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest

from db import VoteDatabase
from slack_blocks import (
    CATEGORY_OPTIONS,
    build_close_summary_blocks,
    build_vote_blocks,
    build_vote_fallback_text,
    format_close_summary,
)


def _all_text(blocks: list[dict]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block.get("text"), dict):
            parts.append(block["text"].get("text", ""))
        for field in block.get("fields", []):
            parts.append(field.get("text", ""))
        for element in block.get("elements", []):
            if isinstance(element, dict):
                text = element.get("text", "")
                parts.append(text if isinstance(text, str) else text.get("text", ""))
    return "\n".join(parts)


def _score_ui_signature(blocks: list[dict]) -> list[str]:
    return [
        block["text"]["text"]
        for block in blocks
        if isinstance(block.get("text"), dict) and "점*" in block["text"].get("text", "")
    ]


class FakeSlackClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self.ephemerals: list[dict] = []
        self.user_info_calls: list[str] = []

    def chat_postMessage(self, **kwargs: dict) -> dict[str, str]:
        self.posts.append(kwargs)
        return {"ts": f"900.{len(self.posts):03d}"}

    def chat_update(self, **kwargs: dict) -> dict[str, bool]:
        self.updates.append(kwargs)
        return {"ok": True}

    def chat_postEphemeral(self, **kwargs: dict) -> dict[str, bool]:
        self.ephemerals.append(kwargs)
        return {"ok": True}

    def users_info(self, user: str) -> dict:
        self.user_info_calls.append(user)
        return {
            "ok": True,
            "user": {
                "id": user,
                "name": user.lower(),
                "profile": {
                    "display_name": user,
                    "image_48": f"https://example.com/{user}.png",
                },
            },
        }


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
        self.assertEqual(case["status"], "categorizing")

        case = self.db.set_category(case["case_id"], "선정")
        self.assertIsNotNone(case)
        self.assertEqual(case["status"], "voting")
        self.assertEqual(case["category"], "선정")

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
        closed_text = _all_text(blocks)
        self.assertTrue(all(block.get("type") != "actions" for block in blocks))
        self.assertNotIn("최종 투표 결과", closed_text)
        self.assertNotIn("평균 점수", closed_text)
        self.assertNotIn("최빈 점수", closed_text)
        self.assertIn("CASE-", build_vote_fallback_text(closed_case, stats))
        self.assertIn("투표가 마감되었습니다.", format_close_summary(stats))
        self.assertNotIn("평균 점수", format_close_summary(stats))
        self.assertNotIn("최빈 점수", format_close_summary(stats))

    def test_stats_track_who_voted_which_score(self) -> None:
        case, _ = self.db.create_case_if_absent("C2", "200.000", "U1")
        case_id = case["case_id"]
        self.db.upsert_vote(case_id, "U1", 4)
        self.db.upsert_vote(case_id, "U2", 4)
        self.db.upsert_vote(case_id, "U3", 2)
        self.db.upsert_vote(case_id, "U1", 5)  # revote moves U1 from 4 -> 5

        stats = self.db.get_vote_stats(case_id)
        self.assertEqual(stats["votes_by_score"][5], ["U1"])
        self.assertEqual(stats["votes_by_score"][4], ["U2"])
        self.assertEqual(stats["votes_by_score"][2], ["U3"])

        stats_with_profiles = dict(stats)
        stats_with_profiles["voter_profiles"] = {
            user_id: {"image_url": f"https://example.com/{user_id}.png", "alt_text": user_id}
            for user_id in ("U1", "U2", "U3")
        }
        blocks = build_vote_blocks(case, stats_with_profiles)
        voting_text = _all_text(blocks)
        image_urls = [
            element["image_url"]
            for block in blocks
            for element in block.get("elements", [])
            if element.get("type") == "image"
        ]
        self.assertNotIn("<@U1>", voting_text)
        self.assertNotIn("투표 없음", voting_text)
        self.assertIn(":five: *5점*", voting_text)
        self.assertIn("https://example.com/U1.png", image_urls)
        self.assertIn("https://example.com/U2.png", image_urls)
        self.assertIn("https://example.com/U3.png", image_urls)

        close_text = _all_text(build_close_summary_blocks(stats))
        self.assertNotIn("<@U1>", close_text)
        self.assertIn("투표가 마감되었습니다.", close_text)
        self.assertNotIn("최종 점수별 결과", close_text)

    def test_vote_score_ui_is_identical_for_all_categories(self) -> None:
        stats = {
            "counts": {0: 0, 1: 1, 2: 0, 3: 2, 4: 1, 5: 4},
            "votes_by_score": {score: [] for score in range(6)},
            "total_voters": 8,
            "average": 0.0,
            "modes": [5],
        }

        signatures = []
        for _, category in CATEGORY_OPTIONS:
            case = {
                "case_id": f"CASE-{category}",
                "status": "closed",
                "category": category,
            }
            blocks = build_vote_blocks(case, stats)
            text = _all_text(blocks)

            self.assertIn(":five: *5점*", text)
            self.assertIn("●", text)
            self.assertIn("○", text)
            self.assertNotIn(":large_green_square:", text)
            self.assertNotIn(":large_blue_square:", text)
            self.assertNotIn(":large_yellow_square:", text)
            self.assertNotIn(":large_orange_square:", text)
            self.assertNotIn(":large_red_square:", text)
            self.assertNotIn(":black_large_square:", text)
            self.assertNotIn("투표 없음", text)
            signatures.append(_score_ui_signature(blocks))

        self.assertTrue(all(signature == signatures[0] for signature in signatures))


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
            "item_user": "U1",
            "item": {"channel": "C1", "ts": "123.456"},
        }
        self.app_module.handle_reaction_added(event, self.client)
        self.app_module.handle_reaction_added(event, self.client)

        self.assertEqual(len(self.client.posts), 1)
        self.assertIn("카테고리 선택", self.client.posts[0]["text"])

        case = self.app_module.db.create_case_if_absent("C1", "123.456", "U1")[0]
        case_id = case["case_id"]
        acks: list[str] = []

        self.app_module.handle_vote_score(
            lambda: acks.append("ignored_vote"),
            {"user": {"id": "U2"}},
            {"value": json.dumps({"case_id": case_id, "score": 3})},
            self.client,
        )
        self.assertEqual(self.app_module.db.get_vote_stats(case_id)["total_voters"], 0)

        self.app_module.handle_select_category(
            lambda: acks.append("category"),
            {"user": {"id": "U1"}},
            {"value": json.dumps({"case_id": case_id, "category": "선정"})},
            self.client,
        )
        case = self.app_module.db.get_case(case_id)
        self.assertEqual(case["status"], "voting")
        self.assertEqual(case["category"], "선정")
        self.assertIn("카테고리: *선정*", _all_text(self.client.updates[-1]["blocks"]))

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
        self.assertEqual(acks, ["ignored_vote", "category", "vote", "revote"])
        self.assertEqual(stats["counts"][5], 1)
        self.assertEqual(stats["total_voters"], 1)
        self.assertEqual(len(self.client.updates), 3)
        latest_blocks = self.client.updates[-1]["blocks"]
        self.assertTrue(
            any(
                element.get("type") == "image" and element.get("image_url") == "https://example.com/U2.png"
                for block in latest_blocks
                for element in block.get("elements", [])
            )
        )

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

    def test_orphaned_vote_card_is_recovered_from_action_body(self) -> None:
        case_id = "CASE-20260531-0099"
        body = {
            "user": {"id": "U9"},
            "channel": {"id": "C9"},
            "container": {"channel_id": "C9", "message_ts": "999.002"},
            "message": {"ts": "999.002", "thread_ts": "999.001"},
        }
        acks: list[str] = []

        self.app_module.handle_vote_score(
            lambda: acks.append("vote"),
            body,
            {"value": json.dumps({"case_id": case_id, "score": 4})},
            self.client,
        )

        recovered = self.app_module.db.get_case(case_id)
        self.assertEqual(acks, ["vote"])
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["channel_id"], "C9")
        self.assertEqual(recovered["root_ts"], "999.001")
        self.assertEqual(recovered["vote_message_ts"], "999.002")
        self.assertEqual(recovered["status"], "voting")
        self.assertEqual(self.app_module.db.get_vote_stats(case_id)["counts"][4], 1)
        self.assertEqual(len(self.client.updates), 1)

        self.app_module.handle_close_vote(
            lambda: acks.append("close"),
            body,
            {"value": json.dumps({"case_id": case_id})},
            self.client,
        )

        self.assertEqual(acks, ["vote", "close"])
        self.assertEqual(self.app_module.db.get_case(case_id)["status"], "closed")
        self.assertIn("투표가 마감되었습니다.", self.client.posts[-1]["text"])


if __name__ == "__main__":
    unittest.main()
