from __future__ import annotations

import json
from typing import Any

SCORE_RANGE = range(6)
DESC_SCORES = list(range(5, -1, -1))
MAX_FIELDS_PER_SECTION = 10
MAX_CONTEXT_ELEMENTS = 10
MAX_CONTEXT_IMAGES = MAX_CONTEXT_ELEMENTS - 1
SCORE_LABELS = {
    5: ("🟢", "5점"),
    4: ("🔵", "4점"),
    3: ("🟡", "3점"),
    2: ("🟠", "2점"),
    1: ("🔴", "1점"),
    0: ("⚫", "0점"),
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

    if not is_voting:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*최종 투표 결과입니다.*"},
            }
        )

    blocks.extend(_score_option_blocks(case["case_id"], stats, interactive=is_voting))
    blocks.append({"type": "divider"})
    blocks.extend(_summary_blocks(stats))

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
        icon, label = SCORE_LABELS[score]
        section: dict[str, Any] = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{icon} *{label}*"},
        }
        if interactive:
            section["accessory"] = _button(
                text="투표",
                action_id=f"vote_score_{score}",
                value={"case_id": case_id, "score": score},
            )
        blocks.append(section)

        count = counts.get(score, 0)
        voters = votes_by_score.get(score, [])
        blocks.append(
            {
                "type": "context",
                "elements": _option_context_elements(count, voters, voter_profiles),
            }
        )

    return blocks


def _summary_blocks(stats: dict[str, Any]) -> list[dict[str, Any]]:
    if stats["total_voters"] == 0:
        return [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "아직 투표가 없습니다."}],
            }
        ]

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": _summary_text(stats)}},
    ]


def _score_table_blocks(stats: dict[str, Any]) -> list[dict[str, Any]]:
    counts = stats["counts"]
    total = stats["total_voters"]

    fields: list[dict[str, Any]] = [
        {"type": "mrkdwn", "text": "*점수*"},
        {"type": "mrkdwn", "text": "*투표*"},
    ]
    for score in DESC_SCORES:
        count = counts.get(score, 0)
        percent = round(count / total * 100) if total else 0
        fields.append({"type": "mrkdwn", "text": f"{score}점"})
        fields.append({"type": "mrkdwn", "text": f"`{count:>2}명`  ({percent}%)"})

    return [
        {"type": "section", "fields": chunk}
        for chunk in _chunk(fields, MAX_FIELDS_PER_SECTION)
    ]


def _voters_text(stats: dict[str, Any]) -> str:
    lines = ["*투표자*"]
    for score in DESC_SCORES:
        count = stats["counts"].get(score, 0)
        if count == 0:
            continue
        lines.append(f"*{score}점* · {count}명")
    if len(lines) == 1:
        lines.append("_아직 투표자가 없습니다._")
    return "\n".join(lines)


def _summary_text(stats: dict[str, Any]) -> str:
    mode_text = ", ".join(f"{score}점" for score in stats["modes"]) or "-"
    return (
        f"*총 투표자:* {stats['total_voters']}명\n"
        f"*평균 점수:* {stats['average']:.2f}\n"
        f"*최빈 점수:* {mode_text}"
    )


def _option_context_elements(
    count: int,
    voters: list[str],
    voter_profiles: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    if count == 0:
        return [{"type": "mrkdwn", "text": "투표 없음"}]

    elements: list[dict[str, Any]] = []
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
        if len(elements) >= MAX_CONTEXT_IMAGES:
            break

    hidden_count = max(0, count - len(elements))
    if elements and hidden_count:
        count_text = f"{count}명 · +{hidden_count}명"
    else:
        count_text = f"{count}명"
    elements.append({"type": "plain_text", "emoji": True, "text": count_text})
    return elements


def format_vote_results(stats: dict[str, Any], markdown: bool = True) -> str:
    counts = stats["counts"]
    total = stats["total_voters"]

    lines = ["현재 투표 결과"]
    lines.extend(f"{score}점: {counts.get(score, 0)}명" for score in SCORE_RANGE)
    lines.append("")
    lines.append(f"총 투표자: {total}명")
    lines.append(f"평균 점수: {stats['average']:.2f}")
    mode_text = ", ".join(f"{score}점" for score in stats["modes"]) or "-"
    lines.append(f"최빈 점수: {mode_text}")
    return "\n".join(lines)

def build_close_summary_blocks(stats: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "🔴 *투표가 마감되었습니다.*"}},
    ]
    if stats["total_voters"] == 0:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_투표자가 없습니다._"},
            }
        )
        return blocks

    blocks.append({"type": "divider"})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*최종 점수별 결과*"}})
    blocks.extend(_score_table_blocks(stats))
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _voters_text(stats)}})
    return blocks


def format_close_summary(stats: dict[str, Any]) -> str:
    counts = stats["counts"]
    total = stats["total_voters"]

    if total == 0:
        return "투표가 마감되었습니다.\n투표자가 없습니다."

    non_zero_lines = [
        f"{score}점: {counts.get(score, 0)}명" for score in DESC_SCORES if counts.get(score, 0) > 0
    ]
    mode_text = ", ".join(f"{score}점" for score in stats["modes"]) or "-"
    return "\n".join(
        [
            "투표가 마감되었습니다.",
            "최종 투표 결과:",
            *non_zero_lines,
            f"총 투표자: {total}명",
            f"평균 점수: {stats['average']:.2f}",
            f"최빈 점수: {mode_text}",
        ]
    )


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


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
