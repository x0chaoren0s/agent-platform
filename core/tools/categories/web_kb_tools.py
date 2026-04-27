from __future__ import annotations

from core import knowledge_base as kb_mod
from . import web_runtime as web_tools

from ..base import BaseTool, ToolContext


class WebSearchTool(BaseTool):
    name = "web_search"
    roles = frozenset({"member", "temp"})
    is_red = False
    desc = "搜索互联网信息"
    signature = "web_search(query*, limit?)"
    args_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await web_tools.web_search(
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
            **args,
        )


class WebReadTool(BaseTool):
    name = "web_read"
    roles = frozenset({"member", "temp"})
    is_red = False
    desc = "读取网页正文"
    signature = "web_read(url*)"
    args_schema = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await web_tools.web_read(
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
            **args,
        )


class KbWriteTool(BaseTool):
    name = "kb_write"
    roles = frozenset({"member", "orchestrator"})
    is_red = False
    desc = "写入共享知识库"
    signature = "kb_write(title*, content*, tags?, author?)"
    args_schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await kb_mod.kb_write(project_dir=ctx.project_dir, **args)


class KbSearchTool(BaseTool):
    name = "kb_search"
    roles = frozenset({"member", "orchestrator"})
    is_red = False
    desc = "检索共享知识库"
    signature = "kb_search(query*, limit?)"
    args_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    output_schema = {"type": "string"}
    examples = []

    async def run(self, args: dict, ctx: ToolContext) -> str:
        return await kb_mod.kb_search(project_dir=ctx.project_dir, **args)

