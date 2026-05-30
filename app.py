from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from db import VoteDatabase
from slack_blocks import (
    build_vote_blocks,
    build_vote_fallback_text,
    format_close_summary,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
DB_PATH = os.getenv("DB_PATH", "./labeling_vote_bot.db")
VOTE_TRIGGER_REACTION = os.getenv("VOTE_TRIGGER_REACTION", "vote")

if not SLACK_BOT_TOKEN:
    raise RuntimeError("SLACK_BOT_TOKEN is required")
if not SLACK_APP_TOKEN:
    raise RuntimeError("SLACK_APP_TOKEN is required")
if not SLACK_SIGNING_SECRET:
    raise RuntimeError("SLACK_SIGNING_SECRET is required")

db = VoteDatabase(DB_PATH)
db.init_db()

app = App(
    token=SLACK_BOT_TOKEN,
    signing_secret=SLACK_SIGNING_SECRET,
    token_verification_enabled=False,
)
BOT_USER_ID: str | None = None


@app.event("reaction_added")
def handle_reaction_added(event: dict[str, Any], client: Any) -> None:
    try:
        if event.get("reaction") != VOTE_TRIGGER_REACTION:
            return
        if BOT_USER_ID and event.get("user") == BOT_USER_ID:
            return

        item = event.get("item") or {}
        channel_id = item.get("channel")
        root_ts = item.get("ts")
        if not channel_id or not root_ts:
            logger.warning("reaction_added event has no channel or ts: %s", event)
            return

        case, created = db.create_case_if_absent(
            channel_id=channel_id,
            root_ts=root_ts,
            created_by=event.get("user"),
        )
        if not created and case.get("vote_message_ts"):
            logger.info("Vote case already exists: %s", case["case_id"])
            return
        if not created:
            logger.info("Retrying vote message creation for case %s", case["case_id"])

        stats = db.get_vote_stats(case["case_id"])
        response = client.chat_postMessage(
            channel=channel_id,
            thread_ts=root_ts,
            text=build_vote_fallback_text(case, stats),
            blocks=build_vote_blocks(case, stats),
        )
        vote_message_ts = response["ts"]
        db.set_vote_message_ts(case["case_id"], vote_message_ts)
        logger.info("Created vote case %s in channel %s", case["case_id"], channel_id)
    except Exception:
        logger.exception("Failed to handle reaction_added event")


@app.action(re.compile(r"^vote_score_[0-5]$"))
def handle_vote_score(ack: Any, body: dict[str, Any], action: dict[str, Any], client: Any) -> None:
    ack()
    try:
        payload = json.loads(action["value"])
        case_id = payload["case_id"]
        score = int(payload["score"])
        user_id = body["user"]["id"]

        case = db.get_case(case_id)
        if not case:
            logger.warning("Vote action for unknown case_id=%s", case_id)
            return
        if case["status"] == "closed":
            logger.info("Ignoring vote for closed case_id=%s", case_id)
            return

        db.upsert_vote(case_id=case_id, user_id=user_id, score=score)
        _update_vote_message(client, case_id)
        logger.info("Recorded vote case_id=%s user_id=%s score=%s", case_id, user_id, score)
    except Exception:
        logger.exception("Failed to handle vote action")


@app.action("close_vote")
def handle_close_vote(ack: Any, body: dict[str, Any], action: dict[str, Any], client: Any) -> None:
    ack()
    try:
        payload = json.loads(action["value"])
        case_id = payload["case_id"]

        case, newly_closed = db.close_case(case_id)
        if not case:
            logger.warning("Close action for unknown case_id=%s", case_id)
            return

        stats = db.get_vote_stats(case_id)
        _update_vote_message(client, case_id, case=case, stats=stats)
        if newly_closed:
            client.chat_postMessage(
                channel=case["channel_id"],
                thread_ts=case["root_ts"],
                text=format_close_summary(stats),
            )
        logger.info("Closed vote case_id=%s by user_id=%s", case_id, body["user"]["id"])
    except Exception:
        logger.exception("Failed to handle close vote action")


def _update_vote_message(
    client: Any,
    case_id: str,
    case: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
) -> None:
    case = case or db.get_case(case_id)
    if not case:
        logger.warning("Cannot update missing case_id=%s", case_id)
        return
    if not case.get("vote_message_ts"):
        logger.warning("Cannot update case_id=%s without vote_message_ts", case_id)
        return

    stats = stats or db.get_vote_stats(case_id)
    client.chat_update(
        channel=case["channel_id"],
        ts=case["vote_message_ts"],
        text=build_vote_fallback_text(case, stats),
        blocks=build_vote_blocks(case, stats),
    )


def initialize_bot_user_id() -> None:
    global BOT_USER_ID
    try:
        auth = app.client.auth_test()
        BOT_USER_ID = auth.get("user_id")
        logger.info("Bot user id: %s", BOT_USER_ID)
    except Exception:
        logger.exception("Failed to fetch bot user id")


if __name__ == "__main__":
    initialize_bot_user_id()
    logger.info("Starting Labeling Vote Bot with trigger reaction :%s:", VOTE_TRIGGER_REACTION)
    SocketModeHandler(app, SLACK_APP_TOKEN).start()
