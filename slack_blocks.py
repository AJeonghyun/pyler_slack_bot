from __future__ import annotations

import json
from typing import Any

SCORE_RANGE = range(6)
DESC_SCORES = list(range(5, -1, -1))
MAX_CONTEXT_ELEMENTS = 10
BAR_SEGMENTS = 8
FILLED_BAR_MARK = "●"
EMPTY_BAR_MARK = "○"
SCORE_LABELS = {
    5: (":five:", "5점"),
    4: (":four:", "4점"),
    3: (":three:", "3점"),
    2: (":two:", "2점"),
    1: (":one:", "1점"),
    0: (":zero:", "0점"),
}
CATEGORY_OPTIONS = [
    ("selection", "선정"),
    ("violence", "폭력"),
    ("controversy", "논란"),
    ("politics", "정치"),
    ("hate", "혐오"),
]
CATEGORY_LABELS = dict(CATEGORY_OPTIONS)


def build_vote_blocks(case: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(case["status"])
    is_voting = status == "voting"
    status_label = "Voting" if is_voting else "Closed"
    category = case.get("category") or "-"

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*라벨링 검토 투표*\n"
                    f"`{case['case_id']}` · 상태: *{status_label}* · 카테고리: *{category}*"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "점수를 선택해주세요. 의견 토론은 이 thread 댓글로 남겨주세요.",
                },
            ],
        },
        {"type": "divider"},
    ]

    blocks.extend(_score_option_blocks(case["case_id"], stats, interactive=is_voting))
    blocks.append({"type": "divider"})
    if is_voting:
        blocks.append(_close_action_block(case["case_id"]))

    return blocks


def build_vote_fallback_text(case: dict[str, Any], stats: dict[str, Any]) -> str:
    status_label = "투표 진행 중" if case["status"] == "voting" else "투표 마감"
    category = case.get("category") or "-"
    return (
        "라벨링 검토 투표\n"
        f"Case ID: {case['case_id']}\n"
        f"상태: {status_label}\n"
        f"카테고리: {category}\n"
        f"{format_vote_results(stats, markdown=False)}"
    )


def build_category_blocks(case: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*라벨링 검토 카테고리 선택*\n"
                    f"`{case['case_id']}` · 스크린샷 업로더가 먼저 카테고리를 선택하면 투표가 시작됩니다."
                ),
            },
        },
        {"type": "divider"},
    ]
    for key, label in CATEGORY_OPTIONS:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{label}*"},
                "accessory": _button(
                    text="선택",
                    action_id=f"select_category_{key}",
                    value={"case_id": case["case_id"], "category": label},
                ),
            }
        )
    return blocks


def build_category_fallback_text(case: dict[str, Any]) -> str:
    return (
        "라벨링 검토 카테고리 선택\n"
        f"Case ID: {case['case_id']}\n"
        "스크린샷을 올린 사람이 카테고리를 먼저 선택하면 투표가 시작됩니다."
    )


def _score_option_blocks(
    case_id: str,
    stats: dict[str, Any],
    interactive: bool,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    counts = stats["counts"]
    votes_by_score = stats.get("votes_by_score", {})
    voter_profiles = stats.get("voter_profiles", {})

    for score in DESC_SCORES:
        count = counts.get(score, 0)
        voters = votes_by_score.get(score, [])
        section: dict[str, Any] = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _score_result_text(score, count, stats["total_voters"]),
            },
        }
        if interactive:
            section["accessory"] = _button(
                text="투표",
                action_id=f"vote_score_{score}",
                value={"case_id": case_id, "score": score},
            )
        blocks.append(section)

        context_elements = _option_context_elements(count, voters, voter_profiles)
        if context_elements:
            blocks.append({"type": "context", "elements": context_elements})

    return blocks


def _option_context_elements(
    count: int,
    voters: list[str],
    voter_profiles: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    if count == 0:
        return []

    elements: list[dict[str, Any]] = []
    image_limit = MAX_CONTEXT_ELEMENTS - 1 if count > MAX_CONTEXT_ELEMENTS else MAX_CONTEXT_ELEMENTS
    for user_id in voters:
        profile = voter_profiles.get(user_id) or {}
        image_url = profile.get("image_url")
        if not image_url:
            continue
        elements.append(
            {
                "type": "image",
                "image_url": image_url,
                "alt_text": profile.get("alt_text") or user_id,
            }
        )
        if len(elements) >= image_limit:
            break

    hidden_count = max(0, count - len(elements))
    if hidden_count:
        elements.append({"type": "plain_text", "emoji": True, "text": f"+{hidden_count}명"})
    elif not elements:
        elements.append({"type": "plain_text", "emoji": True, "text": f"{count}명 참여"})
    return elements


def _score_result_text(score: int, count: int, total: int) -> str:
    icon, label = SCORE_LABELS[score]
    percent = round(count / total * 100) if total else 0
    if count == 0:
        return f"{icon} *{label}*\n{_score_bar(count, total)}"
    return f"{icon} *{label}*\n{_score_bar(count, total)} *{count}명* · {percent}%"


def _score_bar(count: int, total: int) -> str:
    filled = round(count / total * BAR_SEGMENTS) if total else 0
    filled = max(0, min(BAR_SEGMENTS, filled))
    return "".join([FILLED_BAR_MARK] * filled + [EMPTY_BAR_MARK] * (BAR_SEGMENTS - filled))


def format_vote_results(stats: dict[str, Any], markdown: bool = True) -> str:
    counts = stats["counts"]
    total = stats["total_voters"]

    lines = ["현재 투표 결과"]
    lines.extend(f"{score}점: {counts.get(score, 0)}명" for score in SCORE_RANGE)
    lines.append("")
    lines.append(f"총 투표자: {total}명")
    return "\n".join(lines)


def build_close_summary_blocks(stats: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "🔴 *투표가 마감되었습니다.*\n최종 결과는 위 투표 카드에서 확인해주세요.",
            },
        }
    ]


def format_close_summary(stats: dict[str, Any]) -> str:
    return "투표가 마감되었습니다.\n최종 결과는 위 투표 카드에서 확인해주세요."


def _close_action_block(case_id: str) -> dict[str, Any]:
    return {
        "type": "actions",
        "elements": [
            _button(
                text="투표 마감",
                action_id="close_vote",
                value={"case_id": case_id},
                style="danger",
            )
        ],
    }


def _button(
    text: str,
    action_id: str,
    value: dict[str, Any],
    style: str | None = None,
) -> dict[str, Any]:
    button: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": True},
        "action_id": action_id,
        "value": json.dumps(value, ensure_ascii=False),
    }
    if style:
        button["style"] = style
    return button
