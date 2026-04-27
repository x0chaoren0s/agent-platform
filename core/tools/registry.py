from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from .base import BaseTool, ToolContext


class RuntimeToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._loaded = False

    def discover(self) -> None:
        if self._loaded:
            return
        self._import_categories()
        self._register_discovered_tools()
        self._loaded = True

    def _import_categories(self) -> None:
        root_pkg = "core.tools.categories"
        pkg = importlib.import_module(root_pkg)
        for module_info in pkgutil.walk_packages(pkg.__path__, prefix=f"{root_pkg}."):
            if module_info.ispkg:
                continue
            importlib.import_module(module_info.name)

    def _register_discovered_tools(self) -> None:
        for tool_cls in self._all_tool_subclasses(BaseTool):
            # Filter out indirect/non-category classes to keep boundaries explicit.
            mod = tool_cls.__module__
            if not mod.startswith("core.tools.categories."):
                continue
            if bool(getattr(tool_cls, "ABSTRACT_TOOL", False)):
                continue
            tool = tool_cls()
            self._validate_tool(tool)
            if tool.name in self._tools:
                raise RuntimeError(f"Duplicate tool name detected: {tool.name}")
            self._tools[tool.name] = tool

    @staticmethod
    def _all_tool_subclasses(base: type[BaseTool]) -> list[type[BaseTool]]:
        out: list[type[BaseTool]] = []
        stack = list(base.__subclasses__())
        seen: set[type[BaseTool]] = set()
        while stack:
            cls = stack.pop()
            if cls in seen:
                continue
            seen.add(cls)
            out.append(cls)
            stack.extend(cls.__subclasses__())
        return out

    @staticmethod
    def _validate_tool(tool: BaseTool) -> None:
        if not tool.name or not str(tool.name).strip():
            raise RuntimeError(f"Invalid tool: empty name in {tool.__class__.__name__}")
        if not isinstance(tool.roles, frozenset) or not tool.roles:
            raise RuntimeError(f"Invalid tool roles: {tool.name}")
        allowed_roles = {"orchestrator", "member", "temp"}
        if any(r not in allowed_roles for r in tool.roles):
            raise RuntimeError(f"Invalid tool role in {tool.name}: {tool.roles}")
        if not isinstance(tool.args_schema, dict):
            raise RuntimeError(f"Invalid args_schema in {tool.name}")
        if not isinstance(tool.output_schema, dict) or not tool.output_schema.get("type"):
            raise RuntimeError(f"Invalid output_schema in {tool.name}: must include type")

    def get(self, name: str) -> BaseTool | None:
        self.discover()
        return self._tools.get(str(name or "").strip())

    def list_all(self) -> list[BaseTool]:
        self.discover()
        return [self._tools[k] for k in sorted(self._tools.keys())]

    def list_for_role(self, role: str, is_temp: bool) -> list[BaseTool]:
        self.discover()
        role_key = "temp" if is_temp else role
        return [t for t in self.list_all() if role_key in t.roles]

    def list_red_tools(self) -> set[str]:
        self.discover()
        return {t.name for t in self._tools.values() if t.is_red}

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> str:
        tool = self.get(name)
        if tool is None:
            return f"未知工具：{name}"
        safe_args = args if isinstance(args, dict) else {}
        tool.validate_args(safe_args)
        await tool.precheck(safe_args, ctx)
        result = await tool.run(safe_args, ctx)
        await tool.post_success(result, safe_args, ctx)
        return result

    def ui_tools_for_role(self, role: str, is_temp: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tool in self.list_for_role(role, is_temp):
            out.append(
                {
                    "name": tool.name,
                    "desc": tool.desc,
                    "is_red": tool.is_red,
                    "signature": tool.signature,
                    "output_schema": tool.output_schema,
                }
            )
        return out

    def render_tools_for_prompt(self, role: str, is_temp: bool) -> str:
        tools = self.list_for_role(role, is_temp)
        if not tools:
            return "【可用工具】\n- （无）"
        lines = ["【可用工具】"]
        for tool in tools:
            sig = tool.signature or f"{tool.name}(args)"
            red = "（需确认）" if tool.is_red else ""
            lines.append(f"- {sig}{red}：{tool.desc}")
        lines.append(
            "\n【tool_call 格式示例】\n"
            "```tool_call\n"
            "{\"tool\":\"<name>\",\"args\":{...}}\n"
            "```"
        )
        return "\n".join(lines)


_RUNTIME: RuntimeToolRegistry | None = None
_REQUIRED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "assign_task",
        "update_task",
        "submit_deliverable",
        "list_tasks",
        "send_message",
        "ask_user",
        "give_up",
        "load_skill",
        "list_files",
        "list_team",
        "recruit_fixed",
        "dismiss_member",
        "recruit_temp",
        "update_project_context",
        "web_search",
        "web_read",
        "kb_write",
        "kb_search",
    }
)


def get_runtime_registry() -> RuntimeToolRegistry:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = RuntimeToolRegistry()
        _RUNTIME.discover()
    return _RUNTIME


def assert_required_tools_present() -> None:
    registry = get_runtime_registry()
    discovered = {t.name for t in registry.list_all()}
    missing = sorted(_REQUIRED_TOOL_NAMES - discovered)
    if missing:
        raise RuntimeError(f"Tool registry missing required tools: {', '.join(missing)}")

