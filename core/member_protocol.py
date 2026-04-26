"""Shared task execution protocol injected into member agents."""

from __future__ import annotations

MEMBER_TASK_PROTOCOL = """【任务执行协议】（你必须遵守，否则任务不会被系统判定为完成）

可用任务工具（在回复末尾使用 tool_call 代码块）：
- submit_deliverable：提交最终成果并将任务置为 done
- update_task：更新任务状态（in_progress / blocked_on_user）或补充进度
- send_message：与团队成员沟通，默认会通知 orchestrator
- ask_user：需要用户决策时弹出选项
- give_up：无法继续时放弃任务并通知 orchestrator
- list_tasks：查看任务清单和状态

调用语法（必须是合法 JSON）：
```tool_call
{"tool":"submit_deliverable","args":{"task_id":"task-0001","content":"最终成果全文","summary":"一句话总结"}}
```

收到【新任务】后：
1) 理解 title / brief / deadline；
2) 完成业务内容产出；
3) 在同一轮回复末尾调用 submit_deliverable 提交最终成果。
没有 submit_deliverable，任务不会从 ready 变为 done。

跨多轮任务处理规则：
- 开始执行时先 update_task(status="in_progress")
- 中间若有阻塞，可 update_task(progress_note=...)
- 完成时必须 submit_deliverable

信息不足时：
- 优先 ask_user，不要猜测

无法完成时：
- 调用 give_up(task_id, reason)

格式约束：
- tool_call 代码块必须放在回复末尾
- 一次回复可包含多个 tool_call，按顺序执行
- args 必须是合法 JSON
"""


def compose_member_instructions(business_instructions: str) -> str:
    """Compose the effective system prompt for fixed member agents."""
    cleaned = (business_instructions or "").strip()
    if not cleaned:
        return MEMBER_TASK_PROTOCOL
    return f"{MEMBER_TASK_PROTOCOL}\n\n【业务说明】\n{cleaned}"


MEMBER_TOOLS = [
    {
        "name": "submit_deliverable",
        "desc": "提交任务最终成果，任务状态自动变为 done",
        "is_red": False,
    },
    {
        "name": "update_task",
        "desc": "更新任务状态或追加进度说明",
        "is_red": False,
    },
    {
        "name": "send_message",
        "desc": "向团队成员发送消息（自动 CC orchestrator）",
        "is_red": False,
    },
    {
        "name": "ask_user",
        "desc": "向用户提问并提供可选项",
        "is_red": False,
    },
    {
        "name": "give_up",
        "desc": "放弃当前任务并通知 orchestrator",
        "is_red": False,
    },
    {
        "name": "list_tasks",
        "desc": "查看任务列表与状态",
        "is_red": False,
    },
]

ORCHESTRATOR_TOOLS = [
    {"name": "assign_task", "desc": "向成员派发任务并设置依赖", "is_red": False},
    {"name": "list_tasks", "desc": "查看团队任务状态", "is_red": False},
    {"name": "send_message", "desc": "发送内部协调消息", "is_red": False},
    {"name": "ask_user", "desc": "向用户发起决策问题", "is_red": False},
    {"name": "recruit_temp", "desc": "招募一次性临时工", "is_red": False},
    {"name": "list_team", "desc": "查看当前团队成员列表", "is_red": False},
    {"name": "recruit_fixed", "desc": "招募固定成员（需确认）", "is_red": True},
    {"name": "dismiss_member", "desc": "解雇成员（需确认）", "is_red": True},
    {
        "name": "update_project_context",
        "desc": "更新项目背景（需确认）",
        "is_red": True,
    },
]


def get_tools_for_role(role: str, is_temp: bool) -> list[dict[str, str | bool]]:
    """Return UI-facing tool metadata by role."""
    if is_temp:
        return []
    if role == "orchestrator":
        return ORCHESTRATOR_TOOLS
    return MEMBER_TOOLS
