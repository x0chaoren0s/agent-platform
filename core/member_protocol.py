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

可用研究工具（事实优先，禁止凭训练记忆编造）：
- web_search：以关键词搜索互联网，返回结果列表（标题/链接/摘要）
- web_read：抓取指定 URL 的正文内容并返回 markdown

调用语法（必须是合法 JSON）：
```tool_call
{"tool":"submit_deliverable","args":{"task_id":"task-0001","content":"最终成果全文","summary":"一句话总结"}}
```
```tool_call
{"tool":"web_search","args":{"query":"未来城市公众号 2026 头部账号"}}
```
```tool_call
{"tool":"web_read","args":{"url":"https://example.com/article"}}
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
- 如果是公开互联网信息（行业数据、竞品资料、文章定义等），先用 web_search → web_read 自己查；只有用户独有的私域信息才 ask_user

无法完成时：
- 调用 give_up(task_id, reason)

【行为准则 - 来自工程实践】
1. 先想再做：动手前把你对 brief 的理解、做了哪些假设说出来；如果 brief 有多种合理解读，列出来再请示，不要私自挑一个。
2. 简单优先：交付最少能解决问题的内容，不要堆砌没要求的章节、抽象、配置项。
3. 外科手术：只回应 brief 里明确要求的范围；如果发现 brief 之外的问题，说出来但不要顺手"修"。
4. 目标驱动：开工前先把"完成判据"写一遍（用户怎么验证我做对了），让自己有可验收锚点。

【事实诚信】
- 涉及外部世界的具体数据、引用、统计、竞品名称等），必须来自 web_search/web_read 的真实返回，禁止凭训练记忆编造。
- 你调用过的所有 URL 会被系统自动记录，submit_deliverable 时会自动附在交付物末尾作为 References。

【工具失败处理】
- web_search/web_read 失败时返回会以"错误："开头。同一查询连续失败 2 次后，请改用 ask_user 让用户提供数据或换思路，不要无限重试。

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


TEMP_TASK_PROTOCOL = """【临时任务协议】（你是一名临时招募的专家，完成任务后汇报给调用方）

可用研究工具（事实优先，禁止凭训练记忆编造）：
- web_search：以关键词搜索互联网，返回结果列表（标题/链接/摘要）
- web_read：抓取指定 URL 的正文内容并返回 markdown

调用语法（必须是合法 JSON）：
```tool_call
{"tool":"web_search","args":{"query":"关键词"}}
```
```tool_call
{"tool":"web_read","args":{"url":"https://example.com/article"}}
```
```tool_call
{"tool":"send_message","args":{"to":["orchestrator"],"content":"任务结果全文"}}
```

完成任务后：
- 使用 send_message 把结果汇报给 orchestrator（或派遣你的成员）
- 不要调用 submit_deliverable，你没有 task_id

【事实诚信】
- 涉及外部数据必须来自 web_search/web_read 的真实返回，禁止凭训练记忆编造
- 如果 web_search/web_read 失败（返回以"错误："开头），在消息里如实说明

格式约束：
- tool_call 代码块必须放在回复末尾
- 一次回复可包含多个 tool_call，按顺序执行
- args 必须是合法 JSON
"""


def compose_temp_instructions(business_instructions: str) -> str:
    """Compose the effective system prompt for temporary agents."""
    cleaned = (business_instructions or "").strip()
    if not cleaned:
        return TEMP_TASK_PROTOCOL
    return f"{TEMP_TASK_PROTOCOL}\n\n【任务说明】\n{cleaned}"


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
    {
        "name": "web_search",
        "desc": "搜索互联网获取标题/链接/摘要列表",
        "is_red": False,
    },
    {
        "name": "web_read",
        "desc": "抓取指定 URL 的页面正文（markdown）",
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
