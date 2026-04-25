"""
Email-style message router.

Rules:
- Every message has: sender, to (list), cc (list), content
- An agent only receives messages where its name appears in `to` or `cc`
- The user (sender="user") always sends to explicit recipients
- Agents reply back to the user; their replies are cc'd to nobody by default
- The global message log (shown in UI) contains ALL messages
- Each agent's context only contains messages addressed to it

Escalation protocol:
- If an agent's reply contains  【需要协助:capability_name:description】
  the router will:
    1. Look up capability_name in the CapabilityTable
    2. If found → forward the original task to that agent automatically
    3. If not found → forward to orchestrator asking it to recruit a temp agent
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from agent_framework._types import Content

from .capability_table import CapabilityTable
from .registry import AgentRegistry
from .session_store import SessionStore

logger = logging.getLogger(__name__)

# System tag added to auto-forwarded messages so the UI can style them
TAG_AUTO_FORWARD = "auto_forward"
TAG_TEMP = "temp"


@dataclass
class Envelope:
    """A single message in the global message log."""

    id: str
    sender: str
    to: list[str]
    cc: list[str]
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)   # data URIs

    def recipients(self) -> set[str]:
        return set(self.to + self.cc)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "sender": self.sender,
            "to": self.to,
            "cc": self.cc,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }
        if self.images:
            d["images"] = self.images
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Envelope":
        return cls(
            id=d["id"],
            sender=d["sender"],
            to=d.get("to", []),
            cc=d.get("cc", []),
            content=d.get("content", ""),
            timestamp=d.get("timestamp", datetime.now().isoformat()),
            metadata=d.get("metadata", {}),
            images=d.get("images", []),
        )


class MessageRouter:
    """
    Maintains the global message log and routes messages to agents.

    - `dispatch()` sends a message to all named recipients and streams
      their responses as async events.
    - Agents see only their own inbox (messages addressed to them).
    - Escalation signals inside agent replies trigger automatic re-routing.
    - `max_inbox_messages` caps how many historical messages each agent sees
      per LLM call; older messages are replaced by a rolling summary.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        session_store: SessionStore,
        thread_id: str = "default",
        log_path: Path | None = None,
        max_inbox_messages: int = 60,
    ) -> None:
        self._registry = registry
        self._session_store = session_store
        self._thread_id = thread_id
        self._log_path = log_path
        self._global_log: list[Envelope] = []
        self._msg_counter = 0
        self._temp_agents: set[str] = set()
        self._flush_lock = asyncio.Lock()
        self._max_inbox_messages = max_inbox_messages
        # per-agent rolling summary cache: agent_name → summary markdown
        self._inbox_summary: dict[str, str] = {}
        # Load persisted log if available
        if log_path is not None:
            self._load_log()

    # ------------------------------------------------------------------
    # Log persistence
    # ------------------------------------------------------------------

    def _load_log(self) -> None:
        """Load persisted global log from disk (called once at init)."""
        if self._log_path is None or not self._log_path.exists():
            return
        try:
            raw = json.loads(self._log_path.read_text(encoding="utf-8"))
            self._global_log = [Envelope.from_dict(d) for d in raw]
            # Restore counter so new IDs don't collide
            if self._global_log:
                last_id = self._global_log[-1].id  # "msg-0042"
                try:
                    self._msg_counter = int(last_id.split("-")[-1])
                except ValueError:
                    self._msg_counter = len(self._global_log)
            logger.info("Loaded %d log entries from %s", len(self._global_log), self._log_path)
        except Exception:
            logger.exception("Failed to load chat log from %s", self._log_path)

    def _flush_log(self) -> None:
        """Atomically write global log to disk."""
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._log_path.with_suffix(".tmp")
            data = json.dumps(
                [e.to_dict() for e in self._global_log],
                ensure_ascii=False,
                indent=None,        # compact — logs can get large
            )
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self._log_path)  # atomic on same filesystem
        except Exception:
            logger.exception("Failed to flush chat log to %s", self._log_path)

    # ------------------------------------------------------------------
    # Temp agent management
    # ------------------------------------------------------------------

    def register_temp_agent(self, name: str) -> None:
        self._temp_agents.add(name)

    def is_temp(self, name: str) -> bool:
        return name in self._temp_agents

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        self._msg_counter += 1
        return f"msg-{self._msg_counter:04d}"

    def _inbox_for(self, agent_name: str) -> list[Envelope]:
        """Filter global log to only messages this agent should see, capped at max_inbox_messages."""
        full = [
            env
            for env in self._global_log
            if agent_name in env.recipients() or env.sender == agent_name
        ]
        if len(full) > self._max_inbox_messages:
            return full[-self._max_inbox_messages:]
        return full

    def _build_prompt_for(self, agent_name: str, new_envelope: Envelope) -> str:
        """Build text-only prompt string (used when no images)."""
        inbox = self._inbox_for(agent_name)
        history_lines = []
        for env in inbox:
            if env.id == new_envelope.id:
                continue
            to_str = ", ".join(env.to) if env.to else "(all)"
            cc_str = f" | CC: {', '.join(env.cc)}" if env.cc else ""
            history_lines.append(
                f"[{env.timestamp[:19]}] From: {env.sender} → To: {to_str}{cc_str}\n{env.content}"
            )
        new_msg = f"【新消息 from {new_envelope.sender}】\n{new_envelope.content}"

        parts = []
        # Prepend rolling summary if available for this agent
        summary = self._inbox_summary.get(agent_name, "")
        if summary:
            parts.append(f"【历史摘要（早期对话已压缩）】\n{summary}")
        if history_lines:
            parts.append(f"【历史消息（仅你可见）】\n" + "\n\n---\n".join(history_lines))
        parts.append(new_msg)
        return "\n\n".join(parts)

    def _build_run_input(
        self, agent_name: str, new_envelope: Envelope
    ) -> "str | list[Content]":
        """
        Return agent run input: plain str when no images, or a list of Content
        objects [text, image1, image2, ...] when images are present.
        """
        prompt_text = self._build_prompt_for(agent_name, new_envelope)
        if not new_envelope.images:
            return prompt_text
        contents: list[Content] = [Content.from_text(prompt_text)]
        for data_uri in new_envelope.images:
            try:
                contents.append(Content.from_uri(data_uri))
            except Exception:
                logger.warning("Failed to attach image to agent input, skipping")
        return contents

    def _get_cap_table(self) -> CapabilityTable:
        return self._registry.cap_table

    def _find_escalation_target(
        self, capability: str
    ) -> tuple[str | None, bool]:
        """
        Returns (agent_name, found_in_cap_table).
        If capability not found, falls back to orchestrator.
        """
        cap_table = self._get_cap_table()
        target = cap_table.find_agent(capability)
        if target:
            return target, True
        orchestrator = self._registry.get_orchestrator_name()
        return orchestrator, False

    # ------------------------------------------------------------------
    # Core dispatch
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        sender: str,
        to: list[str],
        cc: list[str],
        content: str,
        metadata: dict[str, Any] | None = None,
        images: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Record the envelope, then yield streaming events from each recipient agent.

        Yielded event shape:
            {
                "type": "text_delta" | "agent_done" | "error" | "envelope_recorded" | "escalation",
                "agent": str,
                "delta": str,       # for text_delta
                "message": str,     # for error
                "envelope": dict,   # for envelope_recorded / agent_done
            }
        """
        async for event in self._dispatch_inner(
            sender=sender,
            to=to,
            cc=cc,
            content=content,
            metadata=metadata or {},
            images=images or [],
        ):
            yield event

    async def _dispatch_inner(
        self,
        sender: str,
        to: list[str],
        cc: list[str],
        content: str,
        metadata: dict[str, Any],
        images: list[str] | None = None,
        _depth: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Internal dispatch with escalation recursion guard (max depth 3)."""
        MAX_ESCALATION_DEPTH = 3

        envelope = Envelope(
            id=self._next_id(),
            sender=sender,
            to=to,
            cc=cc,
            content=content,
            metadata=metadata,
            images=images or [],
        )
        self._global_log.append(envelope)
        self._flush_log()
        yield {"type": "envelope_recorded", "envelope": envelope.to_dict()}

        # Process `to` recipients sequentially
        for agent_name in to:
            async for event in self._run_agent(
                agent_name=agent_name,
                envelope=envelope,
                original_content=content,
                original_sender=sender,
                depth=_depth,
                max_depth=MAX_ESCALATION_DEPTH,
            ):
                yield event

        # Process `cc` recipients concurrently
        cc_names = list(dict.fromkeys(cc))  # deduplicate while preserving order
        if cc_names:
            results = await asyncio.gather(
                *[
                    self._collect_agent_events(
                        agent_name=name,
                        envelope=envelope,
                        original_content=content,
                        original_sender=sender,
                        depth=_depth,
                        max_depth=MAX_ESCALATION_DEPTH,
                    )
                    for name in cc_names
                ]
            )
            for event_list in results:
                for event in event_list:
                    yield event

    async def _run_agent(
        self,
        agent_name: str,
        envelope: Envelope,
        original_content: str,
        original_sender: str,
        depth: int,
        max_depth: int,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run a single agent and handle escalation."""
        agent = self._registry.get(agent_name)
        if agent is None:
            logger.warning("Agent '%s' not found in registry", agent_name)
            yield {
                "type": "error",
                "agent": agent_name,
                "message": f"Agent '{agent_name}' not found",
            }
            return

        run_input = self._build_run_input(agent_name, envelope)
        session = self._session_store.load(agent_name, self._thread_id)
        if session is None:
            session = agent.create_session()

        full_reply: list[str] = []
        try:
            async for update in agent.run(run_input, session=session, stream=True):
                if not update.contents:
                    continue
                for content in update.contents:
                    if content.type == "text" and content.text:
                        full_reply.append(content.text)
                        yield {"type": "text_delta", "agent": agent_name, "delta": content.text}
                    elif content.type == "text_reasoning":
                        # Reasoning/thinking token — route to separate event
                        # Use .text if available, fall back to protected_data prefix
                        reasoning_text = content.text or ""
                        if not reasoning_text and hasattr(content, "protected_data") and content.protected_data:
                            reasoning_text = f"[reasoning data: {content.protected_data[:120]}]"
                        if reasoning_text:
                            yield {"type": "reasoning_delta", "agent": agent_name, "delta": reasoning_text}
        except Exception as exc:
            logger.exception("Agent '%s' raised an error", agent_name)
            yield {"type": "error", "agent": agent_name, "message": str(exc)}
        finally:
            self._session_store.save(agent_name, self._thread_id, session)

        reply_text = "".join(full_reply)
        if reply_text.strip():
            reply_meta: dict[str, Any] = {}
            if self.is_temp(agent_name):
                reply_meta["is_temp"] = True

            reply_envelope = Envelope(
                id=self._next_id(),
                sender=agent_name,
                to=[original_sender],
                cc=[],
                content=reply_text,
                metadata=reply_meta,
            )
            self._global_log.append(reply_envelope)
            self._flush_log()
            yield {
                "type": "agent_done",
                "agent": agent_name,
                "envelope": reply_envelope.to_dict(),
            }

            # --- Escalation detection ---
            if depth < max_depth:
                cap_table = self._get_cap_table()
                escalation = cap_table.parse_escalation(reply_text)
                if escalation:
                    capability = escalation["capability"]
                    description = escalation["description"]
                    target, found = self._find_escalation_target(capability)
                    yield {
                        "type": "escalation",
                        "agent": agent_name,
                        "capability": capability,
                        "description": description,
                        "routed_to": target,
                        "found_in_cap_table": found,
                    }
                    if target:
                        fwd_content = (
                            f"【任务转发 from {agent_name}】\n"
                            f"原始任务：{original_content}\n\n"
                            f"所需能力：{capability}\n"
                            f"说明：{description}"
                        )
                        if not found:
                            # Orchestrator receives a recruit request
                            fwd_content = (
                                f"【临时招募请求 from {agent_name}】\n"
                                f"原始任务：{original_content}\n\n"
                                f"所需能力：{capability}\n"
                                f"说明：{description}\n\n"
                                f"请评估是否值得新增固定成员，若否则招募临时工完成任务。"
                            )
                        async for esc_event in self._dispatch_inner(
                            sender=agent_name,
                            to=[target],
                            cc=[],
                            content=fwd_content,
                            metadata={TAG_AUTO_FORWARD: True},
                            images=[],
                            _depth=depth + 1,
                        ):
                            yield esc_event

    async def _collect_agent_events(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Collect all events from _run_agent into a list (for gather)."""
        events: list[dict[str, Any]] = []
        async for event in self._run_agent(**kwargs):
            events.append(event)
        return events

    def get_global_log(self) -> list[dict[str, Any]]:
        return [env.to_dict() for env in self._global_log]

    def get_recent_envelopes(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the most recent n envelopes as dicts (for summarization)."""
        return [env.to_dict() for env in self._global_log[-n:]]

    def needs_summarization(self) -> bool:
        """True when global log is large enough to warrant rolling summary."""
        return len(self._global_log) > int(self._max_inbox_messages * 1.5)

    async def do_rolling_summary(self) -> None:
        """
        Summarize the oldest ~60% of messages per agent, trim the global log,
        and cache the summaries so _build_prompt_for() can prepend them.
        """
        from .summarizer import summarize_envelopes

        total = len(self._global_log)
        cutoff = int(total * 0.6)
        old_envelopes = [e.to_dict() for e in self._global_log[:cutoff]]

        # Gather unique agent names that appear in old messages
        agent_names: set[str] = set()
        for env in self._global_log[:cutoff]:
            agent_names.update(env.recipients())
            if env.sender != "user":
                agent_names.add(env.sender)

        for agent_name in agent_names:
            relevant = [
                e for e in old_envelopes
                if agent_name in (e.get("to") or []) + (e.get("cc") or [])
                or e.get("sender") == agent_name
            ]
            if relevant:
                summary = await summarize_envelopes(relevant)
                if summary:
                    # Merge with any existing summary
                    existing = self._inbox_summary.get(agent_name, "")
                    self._inbox_summary[agent_name] = (
                        existing + "\n\n---\n" + summary if existing else summary
                    )

        # Trim global log to newest 40%
        self._global_log = self._global_log[cutoff:]
        self._flush_log()
        logger.info(
            "Rolling summary done for thread=%s: trimmed %d messages, %d remain",
            self._thread_id, cutoff, len(self._global_log),
        )
