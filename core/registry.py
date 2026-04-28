"""
Agent registry: load agents from YAML files, support hot-reload via watchdog.

YAML schema (projects/<project>/agents/<name>.yaml):
    name: script_writer
    description: "改编小说章节为专业剧本"
    role: member          # member | orchestrator (default: member)
    capabilities:
      - script_adaptation
      - story_structure
    instructions: |
      你是一个专业的漫画剧本改编专家...
    # optional
    tools: []
    max_history: 80
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from agent_framework import Agent, HistoryProvider, Message
from agent_framework._types import Content

from .capability_table import CapabilityTable
from .llm import build_client
from .memory import SQLiteHistoryProvider
from .member_protocol import (
    compose_base_instructions,
    compose_member_instructions,
    compose_temp_instructions,
)
from .skill_store import build_skill_index

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Layer 4 — Project Context Provider
# ------------------------------------------------------------------

class ProjectContextProvider(HistoryProvider):
    """
    Injects the project's context.md as a system-level message prepended
    to every agent's conversation.  This is Layer 4 (shared project context)
    in the four-tier memory architecture.
    """

    def __init__(self, context_path: Path) -> None:
        super().__init__(source_id="project-context", load_messages=True)
        self._context_path = context_path

    def _read(self) -> str:
        if self._context_path.exists():
            try:
                return self._context_path.read_text(encoding="utf-8").strip()
            except Exception:
                logger.exception("Failed to read context.md")
        return ""

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        text = self._read()
        if not text:
            return []
        return [
            Message(
                role="system",
                contents=[Content(type="text", text=f"【项目背景】\n{text}")],
            )
        ]

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        pass  # context.md is managed externally


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_agent(
    cfg: dict[str, Any],
    db_path: Path,
    context_path: Path | None = None,
    project_dir: Path | None = None,
) -> Agent:
    """Instantiate a MAF Agent from a YAML config dict."""
    agent_id: str = cfg["name"]
    instructions: str = cfg.get("instructions", "You are a helpful assistant.")
    max_history: int = cfg.get("max_history", 80)
    raw_role: str = cfg.get("role", "member")
    # Normalize LLM-hallucinated or legacy role values: only "orchestrator" is special;
    # everything else (including Chinese variants like "固定成员") maps to "member".
    role: str = raw_role if raw_role == "orchestrator" else "member"
    is_temp: bool = cfg.get("is_temp", False)
    effective_instructions = instructions
    if role == "member":
        if is_temp:
            effective_instructions = compose_temp_instructions(instructions)
        else:
            effective_instructions = compose_member_instructions(instructions)
            if project_dir is not None:
                raw_skills = cfg.get("skills", [])
                agent_skills = (
                    [str(item).strip() for item in raw_skills if str(item).strip()]
                    if isinstance(raw_skills, list)
                    else []
                )
                skill_index = build_skill_index(project_dir, agent_skills)
                if skill_index:
                    effective_instructions = (
                        f"{effective_instructions}\n\n{skill_index}"
                    )
    else:
        # Orchestrator and other roles should also inherit global behavior guardrails.
        effective_instructions = compose_base_instructions(instructions)
    cfg["_effective_instructions"] = effective_instructions

    memory_provider = SQLiteHistoryProvider(
        db_path=db_path,
        agent_id=agent_id,
        max_messages=max_history,
    )
    context_providers: list[Any] = [memory_provider]

    # Layer 4: project context injection rules
    # - Disabled for temp agents (their instructions already contain the task)
    # - Can be explicitly overridden via YAML field `project_context: false/true`
    explicit_ctx: bool | None = cfg.get("project_context", None)
    inject_context = (
        context_path is not None
        and (explicit_ctx is True or (explicit_ctx is None and not is_temp))
    )
    if inject_context:
        context_providers.insert(0, ProjectContextProvider(context_path))

    return Agent(
        name=agent_id,
        instructions=effective_instructions,
        client=build_client(),
        context_providers=context_providers,
    )


class AgentRegistry:
    """
    Loads all agent YAML files from a project directory and keeps them
    up-to-date via watchdog file-system watching.

    Integrates with CapabilityTable so the router can auto-route by capability.
    """

    def __init__(
        self,
        project_dir: str | Path,
        cap_table: CapabilityTable | None = None,
    ) -> None:
        self._project_dir = Path(project_dir)
        self._agents_dir = self._project_dir / "agents"
        self._db_path = self._project_dir / "memory" / "long_term.db"
        self._context_path = self._project_dir / "context.md"
        self._lock = threading.Lock()
        self._agents: dict[str, Agent] = {}
        self._configs: dict[str, dict[str, Any]] = {}
        self._cap_table: CapabilityTable = cap_table or CapabilityTable()
        self._observer: Observer | None = None
        self._load_all()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def cap_table(self) -> CapabilityTable:
        return self._cap_table

    def get(self, name: str) -> Agent | None:
        with self._lock:
            return self._agents.get(name)

    def get_config(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._configs.get(name, {}))

    def all(self) -> dict[str, Agent]:
        with self._lock:
            return dict(self._agents)

    def list_info(self) -> list[dict[str, str]]:
        """Return lightweight metadata for the UI (includes capabilities)."""
        with self._lock:
            return [
                {
                    "name": cfg["name"],
                    "description": cfg.get("description", ""),
                    "role": cfg.get("role", "member"),
                    "capabilities": cfg.get("capabilities", []),
                    "is_temp": cfg.get("is_temp", False),
                }
                for cfg in self._configs.values()
            ]

    def get_orchestrator_name(self) -> str | None:
        """Return the name of the orchestrator agent if one exists."""
        with self._lock:
            for name, cfg in self._configs.items():
                if cfg.get("role") == "orchestrator":
                    return name
        return None

    def unregister(self, name: str) -> None:
        """Remove an agent from the registry (but does not delete YAML file)."""
        with self._lock:
            self._agents.pop(name, None)
            self._configs.pop(name, None)
        self._cap_table.unregister(name)
        logger.info("Unregistered agent '%s'", name)

    def start_watching(self) -> None:
        """Start watchdog observer for hot-reload."""
        handler = _ReloadHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._agents_dir), recursive=False)
        self._observer.start()
        logger.info("Watching %s for agent YAML changes", self._agents_dir)

    def stop_watching(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        if not self._agents_dir.exists():
            logger.warning("Agents directory does not exist: %s", self._agents_dir)
            return
        for yaml_path in self._agents_dir.glob("*.yaml"):
            self._load_file(yaml_path)

    def _load_file(self, path: Path) -> None:
        try:
            cfg = _load_yaml(path)
            if not cfg.get("name"):
                logger.warning("Agent YAML missing 'name' field: %s", path)
                return
            cfg["_source"] = path.stem
            agent = _build_agent(
                cfg,
                self._db_path,
                self._context_path,
                project_dir=self._project_dir,
            )
            with self._lock:
                self._agents[cfg["name"]] = agent
                self._configs[cfg["name"]] = cfg
            # Register capabilities
            self._cap_table.register(
                agent_name=cfg["name"],
                capabilities=cfg.get("capabilities", []),
                description=cfg.get("description", ""),
                is_temp=cfg.get("is_temp", False),
            )
            logger.info("Loaded agent '%s' from %s", cfg["name"], path.name)
        except Exception:
            logger.exception("Failed to load agent YAML: %s", path)

    def _unload_file(self, path: Path) -> None:
        stem = path.stem
        removed = []
        with self._lock:
            for name, cfg in list(self._configs.items()):
                if cfg.get("_source") == stem:
                    removed.append(name)
            for name in removed:
                self._agents.pop(name, None)
                self._configs.pop(name, None)
        for name in removed:
            self._cap_table.unregister(name)
            logger.info("Unloaded agent '%s'", name)


class _ReloadHandler(FileSystemEventHandler):
    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith(".yaml"):
            logger.info("Agent YAML modified, reloading: %s", event.src_path)
            self._registry._load_file(Path(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith(".yaml"):
            logger.info("New agent YAML detected: %s", event.src_path)
            self._registry._load_file(Path(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and event.src_path.endswith(".yaml"):
            logger.info("Agent YAML deleted: %s", event.src_path)
            self._registry._unload_file(Path(event.src_path))
