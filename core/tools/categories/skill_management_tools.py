"""Skill management tools: list, propose, create, update."""

from __future__ import annotations

from ..base import BaseTool, ToolContext

from .skill_management_runtime import (
    list_skills,
    propose_skill,
    list_proposals,
    create_skill,
    update_skill,
)


class ListSkillsTool(BaseTool):
    name = "list_skills"
    roles = frozenset({"member", "orchestrator"})
    is_red = False
    desc = "列出当前项目所有可用的 Skills（id + name + description + scope）"
    signature = "list_skills()"
    args_schema = {"type": "object", "properties": {}}
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        _ = args
        return await list_skills(
            project_dir=str(ctx.project_dir),
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
        )


class ProposeSkillTool(BaseTool):
    name = "propose_skill"
    roles = frozenset({"member"})
    is_red = False
    desc = "提交 skill 提案（含完整草案），供给 orchestrator 和用户审阅"
    signature = "propose_skill(skill_name*, description*, content*, extra_files?, mount_to?, rationale?, scope?)"
    args_schema = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "技能标识符（字母数字+连字符）",
            },
            "description": {
                "type": "string",
                "description": "技能描述，将出现在 list_skills 中",
            },
            "content": {
                "type": "string",
                "description": "完整 SKILL.md 内容（含 YAML frontmatter：---\\nname: ...\\ndescription: ...\\n---\\n正文）",
            },
            "extra_files": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "附属文件：{\"references/xxx.md\": \"content...\"}",
            },
            "mount_to": {
                "type": "array",
                "items": {"type": "string"},
                "description": "创建后自动挂载到哪些 agent（可选）",
            },
            "rationale": {
                "type": "string",
                "description": "提案理由，为什么需要这个 skill",
            },
            "scope": {
                "type": "string",
                "enum": ["project", "system"],
                "description": "写入范围：project（默认，当前项目）或 system（全局，需用户确认）",
            },
        },
        "required": ["skill_name", "description", "content"],
    }
    output_schema = {"type": "string"}

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await propose_skill(
            project_dir=str(ctx.project_dir),
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
            **args,
        )


class ListProposalsTool(BaseTool):
    name = "list_proposals"
    roles = frozenset({"orchestrator"})
    is_red = False
    desc = "查看 skill 提案列表（可筛选状态）"
    signature = "list_proposals(status?)"
    args_schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pending", "approved", "rejected", "cancelled"],
                "description": "按状态筛选（不传则返回全部）",
            },
        },
    }
    output_schema = {"type": "string"}

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await list_proposals(
            project_dir=str(ctx.project_dir),
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
            **args,
        )


class CreateSkillTool(BaseTool):
    name = "create_skill"
    roles = frozenset({"orchestrator"})
    is_red = True
    desc = "创建新 Skill（红色操作，需用户确认）。支持从提案创建或直接指定参数"
    signature = "create_skill(proposal_id|skill_name*, description*, content*, extra_files?, mount_to?, scope?)"
    args_schema = {
        "type": "object",
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": "从已批准的提案创建（提供此参数后可省略 skill_name/description/content）",
            },
            "skill_name": {
                "type": "string",
                "description": "技能标识符（字母数字+连字符）",
            },
            "description": {
                "type": "string",
                "description": "技能描述",
            },
            "content": {
                "type": "string",
                "description": "完整 SKILL.md 内容（含 YAML frontmatter）",
            },
            "extra_files": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "附属文件：{\"references/xxx.md\": \"content...\"}",
            },
            "mount_to": {
                "type": "array",
                "items": {"type": "string"},
                "description": "创建后自动挂载到哪些 agent",
            },
            "scope": {
                "type": "string",
                "enum": ["project", "system"],
                "description": "写入范围：project 或 system",
            },
        },
        "required": [],
    }
    output_schema = {"type": "string"}

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await create_skill(
            project_dir=str(ctx.project_dir),
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
            **args,
        )


class UpdateSkillTool(BaseTool):
    name = "update_skill"
    roles = frozenset({"orchestrator"})
    is_red = True
    desc = "更新现有 Skill（红色操作，需用户确认，自动备份旧版）"
    signature = "update_skill(skill_name*, description*, content*, extra_files?)"
    args_schema = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "要更新的技能标识符",
            },
            "description": {
                "type": "string",
                "description": "更新后的描述",
            },
            "content": {
                "type": "string",
                "description": "更新后的完整 SKILL.md 内容（含 YAML frontmatter）",
            },
            "extra_files": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "更新附属文件",
            },
        },
        "required": ["skill_name", "description", "content"],
    }
    output_schema = {"type": "string"}

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await update_skill(
            project_dir=str(ctx.project_dir),
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
            **args,
        )
