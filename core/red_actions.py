from __future__ import annotations

from datetime import datetime, timedelta

from .question_store import QuestionStore

RED_ACTIONS = {"dismiss_member", "recruit_fixed", "update_project_context"}


def marker_for_action(tool_name: str, args: dict) -> str | None:
    if tool_name == "dismiss_member":
        target = str(args.get("name", "")).strip()
        return f"[[confirm:dismiss:{target}]]" if target else None
    if tool_name == "recruit_fixed":
        target = str(args.get("name", "")).strip()
        return f"[[confirm:recruit:{target}]]" if target else None
    if tool_name == "update_project_context":
        return "[[confirm:context:rewrite]]"
    return None


def _action_label(tool_name: str) -> str:
    if tool_name == "dismiss_member":
        return "dismiss"
    if tool_name == "recruit_fixed":
        return "recruit"
    if tool_name == "update_project_context":
        return "context"
    return tool_name


async def check_confirm(
    qstore: QuestionStore,
    *,
    thread_id: str,
    tool_name: str,
    args: dict,
    max_age_seconds: int = 60,
) -> tuple[bool, str]:
    marker = marker_for_action(tool_name, args)
    action_label = _action_label(tool_name)
    if not marker:
        return False, f"错误：红色操作 {tool_name} 缺少必要目标参数，无法确认。"

    since_ts = (datetime.now() - timedelta(seconds=max_age_seconds)).isoformat(timespec="seconds")
    hit = await qstore.find_recent_answered_with_marker(
        thread_id=thread_id,
        marker=marker,
        since_ts_iso=since_ts,
    )
    if hit is None:
        return (
            False,
            (
                f"错误：本操作（{action_label}）属于不可逆动作，必须先经用户确认。"
                f"请先调用 ask_user，并在 question 中包含确认标记 {marker}，"
                '等待用户回答 "yes" 后再重试。'
            ),
        )

    answer = (hit.answer or "").strip().lower()
    if answer != "yes":
        return False, f'用户已拒绝该动作（answer="{hit.answer or ""}"），请勿重试。'
    return True, ""
