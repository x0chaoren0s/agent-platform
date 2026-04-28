from __future__ import annotations

from . import team_runtime as legacy_team_tools

from ..base import BaseTool, ToolContext


class TeamDispatchTool(BaseTool):
    ABSTRACT_TOOL = True
    tool_name: str = ""
    tool_desc: str = ""
    tool_roles: frozenset[str] = frozenset({"member", "orchestrator"})
    tool_signature: str = ""
    tool_is_red: bool = False

    name = ""
    roles = frozenset({"member", "orchestrator"})
    is_red = False
    desc = ""
    signature = ""
    args_schema = {"type": "object", "properties": {}}
    output_schema = {"type": "string"}
    examples = []

    def __init__(self) -> None:
        self.name = self.tool_name
        self.roles = self.tool_roles
        self.is_red = self.tool_is_red
        self.desc = self.tool_desc
        self.signature = self.tool_signature

    async def run(self, args: dict, ctx: ToolContext) -> str:
        fn = legacy_team_tools.TEAM_TOOL_DISPATCH[self.tool_name]
        return await fn(
            project_dir=str(ctx.project_dir),
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
            **args,
        )


class AssignTaskTool(TeamDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "assign_task"
    tool_desc = "向成员派发任务并设置依赖"
    tool_roles = frozenset({"orchestrator"})
    tool_signature = "assign_task(assignee*, title*, brief*, ...)"


class UpdateTaskTool(TeamDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "update_task"
    tool_desc = "更新任务状态或追加进度说明"
    tool_roles = frozenset({"member"})
    tool_signature = "update_task(task_id*, status?, progress_note?)"


class SubmitDeliverableTool(TeamDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "submit_deliverable"
    tool_desc = "提交任务最终成果"
    tool_roles = frozenset({"member"})
    tool_signature = "submit_deliverable(task_id*, content|file_path, summary*)"


class ListTasksTool(TeamDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "list_tasks"
    tool_desc = "查看任务列表与状态"
    tool_roles = frozenset({"member", "orchestrator"})
    tool_signature = "list_tasks(scope?, status?)"


class SendMessageTool(TeamDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "send_message"
    tool_desc = "发送内部协调消息"
    tool_roles = frozenset({"member", "orchestrator", "temp"})
    tool_signature = "send_message(to*, content*, cc?, related_task?)"


class AskUserTool(TeamDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "ask_user"
    tool_desc = "向用户发起决策问题"
    tool_roles = frozenset({"member", "orchestrator"})
    tool_signature = "ask_user(question*, options?, related_task?, urgency?)"


class GiveUpTool(TeamDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "give_up"
    tool_desc = "放弃任务并通知 orchestrator"
    tool_roles = frozenset({"member"})
    tool_signature = "give_up(task_id*, reason*)"


class LoadSkillTool(TeamDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "load_skill"
    tool_desc = "加载挂载到你身上的 Skill 完整说明"
    tool_roles = frozenset({"member", "temp"})
    tool_signature = "load_skill(name*)"

