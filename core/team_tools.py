"""Deprecated compatibility: import from core.tools.categories.team_runtime instead."""

from __future__ import annotations

from .tools.categories.team_runtime import (
    TEAM_TOOL_DISPATCH,
    _get_task_store,
    assign_task,
    ask_user,
    get_project_dir,
    give_up,
    list_files,
    list_tasks,
    load_skill,
    send_message,
    set_broadcaster,
    set_router,
    submit_deliverable,
    update_task,
)

__all__ = [
    "TEAM_TOOL_DISPATCH",
    "_get_task_store",
    "assign_task",
    "ask_user",
    "get_project_dir",
    "give_up",
    "list_files",
    "list_tasks",
    "load_skill",
    "send_message",
    "set_broadcaster",
    "set_router",
    "submit_deliverable",
    "update_task",
]
