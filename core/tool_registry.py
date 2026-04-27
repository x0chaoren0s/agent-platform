from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .tools.base import ToolContext as ToolExecContext
from .tools.registry import assert_required_tools_present, get_runtime_registry


@dataclass(frozen=True)
class ToolSpec:
    name: str
    is_red: bool = False


def get_tool_spec(tool_name: str) -> ToolSpec | None:
    tool = get_runtime_registry().get(tool_name)
    if tool is None:
        return None
    return ToolSpec(name=tool.name, is_red=tool.is_red)


async def execute_tool(tool_name: str, args: dict[str, Any], ctx: ToolExecContext) -> str:
    return await get_runtime_registry().execute(tool_name, args, ctx)


def ui_tools_for_role(role: str, is_temp: bool) -> list[dict[str, Any]]:
    return get_runtime_registry().ui_tools_for_role(role, is_temp)


def startup_consistency_check() -> None:
    assert_required_tools_present()


def render_prompt_tool_section(role: str, is_temp: bool) -> str:
    return get_runtime_registry().render_tools_for_prompt(role, is_temp)
