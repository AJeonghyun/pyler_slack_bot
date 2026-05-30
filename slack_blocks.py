from __future__ import annotations

import json
from typing import Any


def build_vote_blocks(case: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(case["status"])
    status_label = "Voting" if status == "voting" else "Closed"
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Labeling Review Vote"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Case ID:* `{case['case_id']}`\n"
                    f"*상태:* {status_label}\n"
                    "안내: 점수를 선택해주세요. 의견 토론은 이 thread 댓글로 남겨주세요."
                ),
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": format_vote_results(stats)}},
    ]

    if status == "voting":
        blocks.extend(_voting_action_blocks(case["case_id"]))

    return blocks


def build_vote_fallback_text(case: dict[str, Any], stats: dict[str, Any]) -> str:
    status_label = "Voting" if case["status"] == "voting" else "Closed"
    return (
        "Labeling Review Vote\n"
        f"Case ID: {case['case_id']}\n"
        f"상태: {status_label}\n"
        f"{format_vote_results(stats, markdown=False)}"
    )


def format_vote_results(stats: dict[str, Any], markdown: bool = True) -> str:
    counts = stats["counts"]
    lines = ["*현재 투표 결과*" if markdown else "현재 투표 결과"]
    lines.extend(f"{score}점: {counts.get(score, 0)}명" for score in range(6))
    lines.append("")
    lines.append(f"총 투표자: {stats['total_voters']}명")
    lines.append(f"평균 점수: {stats['average']:.2f}")
    mode_text = ", ".join(f"{score}점" for score in stats["modes"]) or "-"
    lines.append(f"최빈 점수: {mode_text}")
    return "\n".join(lines)


def format_close_summary(stats: dict[str, Any]) -> str:
    counts = stats["counts"]
    non_zero_lines = [
        f"{score}점: {count}명" for score, count in counts.items() if count > 0
    ]
    if not non_zero_lines:
        non_zero_lines = ["투표자 없음"]

    mode_text = ", ".join(f"{score}점" for score in stats["modes"]) or "-"
    return "\n".join(
        [
            "투표가 마감되었습니다.",
            "최종 투표 결과:",
            *non_zero_lines,
            f"총 투표자: {stats['total_voters']}명",
            f"평균 점수: {stats['average']:.2f}",
            f"최빈 점수: {mode_text}",
        ]
    )


def _voting_action_blocks(case_id: str) -> list[dict[str, Any]]:
    score_buttons = [
        _button(
            text=f"{score}점",
            action_id=f"vote_score_{score}",
            value={"case_id": case_id, "score": score},
        )
        for score in range(6)
    ]
    close_button = _button(
        text="투표 마감",
        action_id="close_vote",
        value={"case_id": case_id},
        style="danger",
    )

    return [
        {"type": "actions", "elements": score_buttons[:5]},
        {"type": "actions", "elements": [score_buttons[5], close_button]},
    ]


def _button(
    text: str,
    action_id: str,
    value: dict[str, Any],
    style: str | None = None,
) -> dict[str, Any]:
    button: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": text},
        "action_id": action_id,
        "value": json.dumps(value, ensure_ascii=False),
    }
    if style:
        button["style"] = style
    return button
