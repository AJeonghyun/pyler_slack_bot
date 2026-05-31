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
    CATEGORY_LABELS,
    build_category_blocks,
    build_category_fallback_text,
    build_close_summary_blocks,
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
VOTE_TRIGGER_REACTION = os.getenv("VOTE_TRIGGER_REACTION", "ballot_box_with_ballot")

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
            created_by=event.get("item_user") or event.get("user"),
        )
        if not created and case.get("vote_message_ts"):
            logger.info("Review case already exists: %s", case["case_id"])
            return
        if not created:
            logger.info("Retrying review message creation for case %s", case["case_id"])

        if case["status"] == "categorizing":
            text = build_category_fallback_text(case)
            blocks = build_category_blocks(case)
        else:
            stats = db.get_vote_stats(case["case_id"])
            text = build_vote_fallback_text(case, stats)
            blocks = build_vote_blocks(case, stats)
        response = client.chat_postMessage(
            channel=channel_id,
            thread_ts=root_ts,
            text=text,
            blocks=blocks,
        )
        vote_message_ts = response["ts"]
        db.set_vote_message_ts(case["case_id"], vote_message_ts)
        logger.info("Created review case %s in channel %s", case["case_id"], channel_id)
    except Exception:
        logger.exception("Failed to handle reaction_added event")


@app.action(re.compile(r"^select_category_.+$"))
def handle_select_category(
    ack: Any,
    body: dict[str, Any],
    action: dict[str, Any],
    client: Any,
) -> None:
    ack()
    try:
        payload = json.loads(action["value"])
        case_id = payload["case_id"]
        category = payload["category"]
        user_id = body["user"]["id"]

        if category not in CATEGORY_LABELS.values():
            logger.warning("Invalid category action payload: %s", payload)
            return

        case = db.get_case(case_id)
        if not case:
            case = _recover_case_from_action_body(
                case_id=case_id,
                body=body,
                status="categorizing",
                user_id=user_id,
            )
            if not case:
                _post_ephemeral_from_body(
                    client,
                    body,
                    user_id,
                    "이 투표 카드는 서버 재배포 전 카드라 처리할 수 없습니다. 새로 이모지를 달아 투표 카드를 다시 만들어주세요.",
                )
                logger.warning("Category action for unknown case_id=%s", case_id)
                return
            case_id = case["case_id"]
        if case["status"] == "closed":
            logger.info("Ignoring category action for closed case_id=%s", case_id)
            return
        if case.get("created_by") and user_id != case["created_by"]:
            _post_ephemeral(
                client,
                case,
                user_id,
                "카테고리는 스크린샷을 올린 사람만 선택할 수 있습니다.",
            )
            logger.info(
                "Ignoring category action case_id=%s user_id=%s created_by=%s",
                case_id,
                user_id,
                case["created_by"],
            )
            return

        updated_case = db.set_category(case_id, category)
        if not updated_case:
            logger.warning("Could not set category for case_id=%s", case_id)
            return

        stats = db.get_vote_stats(case_id)
        _update_vote_message(client, case_id, case=updated_case, stats=stats)
        logger.info("Selected category case_id=%s category=%s", case_id, category)
    except Exception:
        logger.exception("Failed to handle category action")


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
            case = _recover_case_from_action_body(
                case_id=case_id,
                body=body,
                status="voting",
                user_id=user_id,
            )
            if not case:
                _post_ephemeral_from_body(
                    client,
                    body,
                    user_id,
                    "이 투표 카드는 서버 재배포 전 카드라 처리할 수 없습니다. 새로 이모지를 달아 투표 카드를 다시 만들어주세요.",
                )
                logger.warning("Vote action for unknown case_id=%s", case_id)
                return
            case_id = case["case_id"]
        if case["status"] != "voting":
            logger.info("Ignoring vote for non-voting case_id=%s status=%s", case_id, case["status"])
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
            recovered_case = _recover_case_from_action_body(
                case_id=case_id,
                body=body,
                status="voting",
                user_id=body["user"]["id"],
            )
            if not recovered_case:
                _post_ephemeral_from_body(
                    client,
                    body,
                    body["user"]["id"],
                    "이 투표 카드는 서버 재배포 전 카드라 처리할 수 없습니다. 새로 이모지를 달아 투표 카드를 다시 만들어주세요.",
                )
                logger.warning("Close action for unknown case_id=%s", case_id)
                return
            case_id = recovered_case["case_id"]
            case, newly_closed = db.close_case(case_id)
            if not case:
                logger.warning("Close action could not recover case_id=%s", case_id)
                return

        stats = db.get_vote_stats(case_id)
        _update_vote_message(client, case_id, case=case, stats=stats)
        if newly_closed:
            client.chat_postMessage(
                channel=case["channel_id"],
                thread_ts=case["root_ts"],
                text=format_close_summary(stats),
                blocks=build_close_summary_blocks(stats),
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


def _post_ephemeral(client: Any, case: dict[str, Any], user_id: str, text: str) -> None:
    try:
        client.chat_postEphemeral(
            channel=case["channel_id"],
            user=user_id,
            text=text,
        )
    except Exception:
        logger.exception("Failed to post ephemeral message")


def _recover_case_from_action_body(
    *,
    case_id: str,
    body: dict[str, Any],
    status: str,
    user_id: str,
) -> dict[str, Any] | None:
    channel_id = _action_channel_id(body)
    vote_message_ts = _action_message_ts(body)
    root_ts = _action_root_ts(body)
    if not channel_id or not vote_message_ts or not root_ts:
        logger.warning(
            "Cannot recover case_id=%s channel_id=%s root_ts=%s vote_message_ts=%s",
            case_id,
            channel_id,
            root_ts,
            vote_message_ts,
        )
        return None

    case = db.recover_case_from_message(
        case_id=case_id,
        channel_id=channel_id,
        root_ts=root_ts,
        vote_message_ts=vote_message_ts,
        status=status,
        created_by=user_id,
    )
    logger.info(
        "Recovered orphaned case_id=%s channel_id=%s root_ts=%s vote_message_ts=%s status=%s",
        case["case_id"],
        channel_id,
        root_ts,
        vote_message_ts,
        status,
    )
    return case


def _action_channel_id(body: dict[str, Any]) -> str | None:
    channel = body.get("channel") or {}
    container = body.get("container") or {}
    return channel.get("id") or container.get("channel_id")


def _action_message_ts(body: dict[str, Any]) -> str | None:
    message = body.get("message") or {}
    container = body.get("container") or {}
    return container.get("message_ts") or message.get("ts")


def _action_root_ts(body: dict[str, Any]) -> str | None:
    message = body.get("message") or {}
    container = body.get("container") or {}
    return message.get("thread_ts") or container.get("thread_ts") or _action_message_ts(body)


def _post_ephemeral_from_body(
    client: Any,
    body: dict[str, Any],
    user_id: str,
    text: str,
) -> None:
    channel_id = _action_channel_id(body)
    if not channel_id:
        logger.warning("Cannot post ephemeral without channel_id")
        return
    try:
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=text,
        )
    except Exception:
        logger.exception("Failed to post ephemeral message")


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
