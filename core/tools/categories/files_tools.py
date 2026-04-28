from __future__ import annotations

from . import files_runtime

from ..base import BaseTool, ToolContext


class FilesDispatchTool(BaseTool):
    ABSTRACT_TOOL = True
    tool_name: str = ""
    tool_desc: str = ""
    tool_roles: frozenset[str] = frozenset({"member", "orchestrator", "temp"})
    tool_signature: str = ""
    tool_is_red: bool = False

    name = ""
    roles = frozenset({"member", "orchestrator", "temp"})
    is_red = False
    desc = ""
    signature = ""
    args_schema: dict = {"type": "object", "properties": {}}
    output_schema: dict = {"type": "string"}
    examples: list = []

    def __init__(self) -> None:
        self.name = self.tool_name
        self.roles = self.tool_roles
        self.is_red = self.tool_is_red
        self.desc = self.tool_desc
        self.signature = self.tool_signature

    async def run(self, args: dict, ctx: ToolContext) -> str:
        fn = files_runtime.FILES_TOOL_DISPATCH[self.tool_name]
        return await fn(
            project_dir=str(ctx.project_dir),
            thread_id=ctx.thread_id,
            caller_agent=ctx.caller_agent,
            **args,
        )


class ListFilesTool(FilesDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "list_files"
    tool_desc = "列出工作区目录文件列表"
    tool_signature = "list_files(path?, max_depth?, include_hidden?)"
    args_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "max_depth": {"type": "integer"},
            "include_hidden": {"type": "boolean"},
        },
    }


class ReadFileTool(FilesDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "read_file"
    tool_desc = "读取项目内文本文件（行号前缀 N|）"
    tool_signature = "read_file(path*, offset?, limit?)"
    args_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["path"],
    }


class WriteFileTool(FilesDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "write_file"
    tool_desc = "覆盖写入文本文件；workspace/ 外需用户确认"
    tool_signature = "write_file(path*, content*)"
    args_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }


class GrepTool(FilesDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "grep"
    tool_desc = "在目录下按正则搜索匹配行（path:line:content）"
    tool_signature = "grep(pattern*, path?, glob?, case_insensitive?, max_matches?)"
    args_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string"},
            "case_insensitive": {"type": "boolean"},
            "max_matches": {"type": "integer"},
        },
        "required": ["pattern"],
    }


class GlobFileSearchTool(FilesDispatchTool):
    ABSTRACT_TOOL = False
    tool_name = "glob_file_search"
    tool_desc = "按 glob 查找文件路径列表"
    tool_signature = "glob_file_search(glob_pattern*, target_directory?)"
    args_schema = {
        "type": "object",
        "properties": {"glob_pattern": {"type": "string"}, "target_directory": {"type": "string"}},
        "required": ["glob_pattern"],
    }
