"""Dynamic SkillIndexProvider that injects skill index as a system message at runtime.

This replaces the build-time skill index baking in effective_instructions
with a runtime HistoryProvider, enabling dynamic skill changes without
recreating agents or reloading YAML.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework import HistoryProvider, Message
from agent_framework._types import Content

from .skill_store import build_skill_index

# Module-level agent-to-skills mapping, updated at runtime.
_AGENT_SKILL_MAP: dict[str, list[str]] = {}


def get_agent_skills(agent_name: str) -> list[str]:
    """Return the currently registered skills for *agent_name*."""
    return _AGENT_SKILL_MAP.get(agent_name, [])


def set_agent_skills(agent_name: str, skills: list[str]) -> None:
    """Set the skills for *agent_name* to *skills*."""
    _AGENT_SKILL_MAP[agent_name] = skills


class SkillIndexProvider(HistoryProvider):
    """HistoryProvider that injects the skill index as a system message at runtime.

    This is Layer 3.5 (dynamic skill index) in the four-tier memory architecture.
    """

    def __init__(self, agent_name: str, project_dir: Path) -> None:
        super().__init__(source_id=f"skill-index-{agent_name}", load_messages=True)
        self._agent_name = agent_name
        self._project_dir = project_dir

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        skills = get_agent_skills(self._agent_name)
        text = build_skill_index(str(self._project_dir), skills)
        if not text:
            return []
        return [
            Message(
                role="system",
                contents=[Content(type="text", text=text)],
            )
        ]

    async def save_messages(
        self,
        session_id: str | None,
        messages: list[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        pass  # skill index is generated on-the-fly, nothing to persist
