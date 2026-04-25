"""
Team Capability Table.

Each agent declares its capabilities in YAML:
    capabilities:
      - creative_writing
      - script_adaptation
      - story_structure

The table maps  capability_name → agent_name  and is used by the router
to auto-forward 【需要协助:capability:description】 escalation signals.

Thread-safety: asyncio.Lock for writes (single-process FastAPI).
Reads are lock-free (Python dict lookup is GIL-safe).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Escalation signal prefix agents must use
ESCALATION_PREFIX = "【需要协助"


class CapabilityTable:
    """
    Stores capability → agent_name mappings for the current project.
    Also stores per-agent metadata (description, is_temp).
    """

    def __init__(self) -> None:
        self._cap_map: dict[str, str] = {}          # capability → agent_name
        self._agent_caps: dict[str, list[str]] = {}  # agent_name → capabilities
        self._agent_meta: dict[str, dict[str, Any]] = {}  # agent_name → {desc, is_temp}
        self._write_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Registration (called from registry on load/unload)
    # ------------------------------------------------------------------

    def register(
        self,
        agent_name: str,
        capabilities: list[str],
        description: str = "",
        is_temp: bool = False,
    ) -> None:
        """Register agent capabilities (synchronous, called from registry thread)."""
        # Remove previous registrations for this agent
        old_caps = self._agent_caps.get(agent_name, [])
        for cap in old_caps:
            self._cap_map.pop(cap, None)

        self._agent_caps[agent_name] = capabilities
        self._agent_meta[agent_name] = {"description": description, "is_temp": is_temp}
        for cap in capabilities:
            self._cap_map[cap.lower()] = agent_name
        logger.debug("Registered capabilities for '%s': %s", agent_name, capabilities)

    def unregister(self, agent_name: str) -> None:
        """Remove all capability registrations for an agent."""
        for cap in self._agent_caps.pop(agent_name, []):
            self._cap_map.pop(cap, None)
        self._agent_meta.pop(agent_name, None)
        logger.debug("Unregistered capabilities for '%s'", agent_name)

    # ------------------------------------------------------------------
    # Queries (lock-free reads)
    # ------------------------------------------------------------------

    def find_agent(self, capability: str) -> str | None:
        """Return the agent name that handles a given capability, or None."""
        return self._cap_map.get(capability.lower())

    def get_all(self) -> dict[str, Any]:
        """Return full table snapshot for UI/API."""
        return {
            name: {
                "capabilities": caps,
                **self._agent_meta.get(name, {}),
            }
            for name, caps in self._agent_caps.items()
        }

    def agent_list(self) -> list[dict[str, Any]]:
        """Return list of agent info dicts with capabilities included."""
        return [
            {
                "name": name,
                "capabilities": self._agent_caps.get(name, []),
                **self._agent_meta.get(name, {}),
            }
            for name in self._agent_caps
        ]

    # ------------------------------------------------------------------
    # Escalation signal parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_escalation(text: str) -> dict[str, str] | None:
        """
        Parse escalation signal from agent reply text.

        Expected format (anywhere in the text):
            【需要协助:capability_name:human description of the task】

        Returns {"capability": ..., "description": ...} or None.
        """
        import re
        pattern = r"【需要协助:([^:】]+):([^】]*)】"
        m = re.search(pattern, text)
        if m:
            return {"capability": m.group(1).strip(), "description": m.group(2).strip()}
        # Also accept simplified form without description
        pattern2 = r"【需要协助:([^】]+)】"
        m2 = re.search(pattern2, text)
        if m2:
            parts = m2.group(1).split(":", 1)
            return {
                "capability": parts[0].strip(),
                "description": parts[1].strip() if len(parts) > 1 else "",
            }
        return None
