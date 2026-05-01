from __future__ import annotations

import asyncio

from .platform_runtime import (
    dismiss_member,
    list_team,
    recruit_fixed,
    recruit_temp,
    update_project_context,
)

from ..base import BaseTool, ToolContext


class ListTeamTool(BaseTool):
    name = "list_team"
    roles = frozenset({"orchestrator"})
    is_red = False
    desc = "查看当前团队成员列表"
    signature = "list_team()"
    args_schema = {"type": "object", "properties": {}}
    output_schema = {"type": "string"}
    examples = [{"tool": "list_team", "args": {}}]

    async def run(self, args: dict, ctx: ToolContext) -> str:
        _ = args
        return await asyncio.to_thread(list_team, str(ctx.project_dir))


class RecruitFixedTool(BaseTool):
    name = "recruit_fixed"
    roles = frozenset({"orchestrator"})
    is_red = True
    desc = "招募固定成员（需确认）"
    signature = "recruit_fixed(name*, description*, capabilities*, instructions*, role?, skills?, tools?)"
    args_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "skills": {"type": "array", "items": {"type": "string"}},
            "tools": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name"],
    }
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await asyncio.to_thread(recruit_fixed, project_dir=str(ctx.project_dir), **args)


class DismissMemberTool(BaseTool):
    name = "dismiss_member"
    roles = frozenset({"orchestrator"})
    is_red = True
    desc = "解雇成员（需确认）"
    signature = "dismiss_member(name*)"
    args_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await asyncio.to_thread(dismiss_member, project_dir=str(ctx.project_dir), **args)


class RecruitTempTool(BaseTool):
    name = "recruit_temp"
    roles = frozenset({"orchestrator"})
    is_red = False
    desc = "招募临时成员"
    signature = "recruit_temp(name*, description*, capabilities*, instructions*, task*)"
    args_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await asyncio.to_thread(recruit_temp, project_dir=str(ctx.project_dir), **args)


class UpdateProjectContextTool(BaseTool):
    name = "update_project_context"
    roles = frozenset({"orchestrator"})
    is_red = True
    desc = "更新项目背景（需确认）"
    signature = "update_project_context(content*)"
    args_schema = {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await asyncio.to_thread(update_project_context, project_dir=str(ctx.project_dir), **args)

