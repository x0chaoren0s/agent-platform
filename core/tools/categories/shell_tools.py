from __future__ import annotations

from . import shell_runtime
from ..base import BaseTool, ToolContext


class RunShellTool(BaseTool):
    name = "run_shell"
    roles = frozenset({"member", "orchestrator"})
    is_red = False
    desc = "在工作区执行 Shell 命令，返回 stdout/stderr/exit_code（cwd 限制在项目目录内）"
    signature = "run_shell(command*, cwd?, timeout?)"
    args_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["command"],
    }
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await shell_runtime.run_shell(
            project_dir=str(ctx.project_dir),
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
            **args,
        )
