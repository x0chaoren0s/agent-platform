"""Short-term session persistence: serialize/restore AgentSession per agent."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_framework import AgentSession

logger = logging.getLogger(__name__)


class SessionStore:
    """
    Persist AgentSession to JSON files so conversations survive server restarts.

    Layout:  sessions_dir/{agent_id}/{session_id}.json
    """

    def __init__(self, sessions_dir: str | Path) -> None:
        self._base = Path(sessions_dir)

    def _path(self, agent_id: str, session_id: str) -> Path:
        p = self._base / agent_id
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{session_id}.json"

    def save(self, agent_id: str, session_id: str, session: AgentSession) -> None:
        try:
            data = session.to_dict()
            self._path(agent_id, session_id).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.exception("Failed to save session agent=%s session=%s", agent_id, session_id)

    def load(self, agent_id: str, session_id: str) -> AgentSession | None:
        path = self._path(agent_id, session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AgentSession.from_dict(data)
        except Exception:
            logger.exception("Failed to load session agent=%s session=%s", agent_id, session_id)
            return None
