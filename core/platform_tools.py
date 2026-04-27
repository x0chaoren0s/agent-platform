"""Compatibility layer: platform tool implementations moved to core.tools.categories.platform_runtime."""

from __future__ import annotations

from .tools.categories.platform_runtime import (
    dismiss_member,
    list_team,
    recruit_fixed,
    recruit_temp,
    update_project_context,
)

PLATFORM_TOOL_FUNCTIONS = [list_team, recruit_fixed, dismiss_member, recruit_temp, update_project_context]
