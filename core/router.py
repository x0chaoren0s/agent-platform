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
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from agent_framework._types import Content

from .capability_table import CapabilityTable
from .registry import AgentRegistry
from .session_store import SessionStore
from .skill_index_provider import set_agent_skills, get_agent_skills, has_agent_skills

logger = logging.getLogger(__name__)

# System tag added to auto-forwarded messages so the UI can style them
TAG_AUTO_FORWARD = "auto_forward"
TAG_TEMP = "temp"
_OUTGOING_COMM_TOOLS: frozenset[str] = frozenset(
    {"submit_deliverable", "send_message", "assign_task"}
)
_MENTION_RE = re.compile(r"@([\w\-\u4e00-\u9fff]+)")
_TOOL_CALL_CONTENT_RE = re.compile(r"```tool[_-]?call\s*\n(.*?)(?:\n```|$)", re.DOTALL)


def _extract_tool_names(text: str) -> list[str]:
    """Extract tool names from tool_call JSON blocks in text."""
    names: list[str] = []
    for m in _TOOL_CALL_CONTENT_RE.finditer(text):
        try:
            payload = json.loads((m.group(1) or "").strip())
            if isinstance(payload, dict):
                name = str(payload.get("tool", ""))
                if name:
                    names.append(name)
        except Exception:
            pass
    return names


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

    # Token budget controls (DeepSeek v4 = ~1M context)
    MODEL_MAX_TOKENS: int = 1_000_000
    TOKEN_BUDGET_RATIO: float = 0.55      # inbox budget: 55% of model max (~550K); rest ~450K for system prompt + summary + SQLite history
    MAX_TOOL_RESULT_CHARS: int = 3_000     # per-result truncation limit

    # Tool compression: keep last N messages full, compress tool parts in older ones
    KEEP_FULL_MESSAGES: int = 20
    TOOL_COMPRESS_PREVIEW_CHARS: int = 150

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token count for mixed Chinese/English text (~0.75 tok/char)."""
        if not text:
            return 0
        return max(1, len(text) * 3 // 4)

    def __init__(
        self,
        registry: AgentRegistry,
        session_store: SessionStore,
        thread_id: str = "default",
        log_path: Path | None = None,
        max_inbox_messages: int = 60,
        broadcaster: Any | None = None,
        tool_executor: Callable[[str, str, str], Awaitable[list[dict[str, Any]]]] | None = None,
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
        self._broadcaster = broadcaster
        self._tool_executor = tool_executor
        # per-agent rolling summary cache: agent_name → summary markdown
        self._inbox_summary: dict[str, str] = {}
        self._recent_msgs: dict[tuple[str, tuple[str, ...]], list[float]] = {}
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
    # Runtime skill state management
    # ------------------------------------------------------------------

    def seed_agent_skills_from_yaml(self, agent_name: str) -> None:
        """Initialize runtime skill state from YAML config (only if not already set)."""
        if has_agent_skills(agent_name):
            return  # already seeded (even if empty)
        cfg = self._registry.get_config(agent_name)
        if cfg is None:
            return
        raw_skills = cfg.get("skills", [])
        if isinstance(raw_skills, list):
            skills = [str(s).strip() for s in raw_skills if str(s).strip()]
            set_agent_skills(agent_name, skills)

    def get_mounted_skills(self, agent_name: str) -> list[str]:
        """Return the list of skills currently mounted for an agent."""
        self.seed_agent_skills_from_yaml(agent_name)
        return get_agent_skills(agent_name)

    def mount_skill(self, agent_name: str, skill_name: str) -> str:
        """Add a skill to the agent's runtime skill set (session-only)."""
        self.seed_agent_skills_from_yaml(agent_name)
        current = set(get_agent_skills(agent_name))
        if skill_name in current:
            return f"Skill「{skill_name}」已挂载在 {agent_name} 上，无需重复操作。"
        current.add(skill_name)
        set_agent_skills(agent_name, sorted(current))
        logger.info("Mounted skill '%s' onto agent '%s' (runtime)", skill_name, agent_name)
        return f"✅ 已挂载 Skill「{skill_name}」到 {agent_name}（运行时生效，本次对话有效）"

    def unmount_skill(self, agent_name: str, skill_name: str) -> str:
        """Remove a skill from the agent's runtime skill set."""
        self.seed_agent_skills_from_yaml(agent_name)
        current = set(get_agent_skills(agent_name))
        if skill_name not in current:
            return f"Skill「{skill_name}」未挂载在 {agent_name} 上。"
        current.remove(skill_name)
        set_agent_skills(agent_name, sorted(current))
        logger.info("Unmounted skill '%s' from agent '%s' (runtime)", skill_name, agent_name)
        return f"✅ 已卸载 Skill「{skill_name}」从 {agent_name}（运行时生效，本次对话有效）"

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

    def _extract_mentions(self, content: str) -> list[str]:
        if not content:
            return []
        seen: set[str] = set()
        mentions: list[str] = []
        for name in _MENTION_RE.findall(content):
            cleaned = str(name).strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            mentions.append(cleaned)
        return mentions

    def _inbox_for(self, agent_name: str) -> list[Envelope]:
        """Filter global log to only messages this agent should see, capped by token budget."""
        full = [
            env
            for env in self._global_log
            if agent_name in env.recipients() or env.sender == agent_name
        ]
        budget = int(self.MODEL_MAX_TOKENS * self.TOKEN_BUDGET_RATIO)
        kept: list[Envelope] = []
        used = 0
        for env in reversed(full):
            est = self._estimate_tokens(env.content or "") + 20  # +20 for metadata
            if used + est > budget:
                break
            kept.append(env)
            used += est
        kept.reverse()
        return kept

    def _format_for_prompt(self, env: Envelope, *, compress_tools: bool = False) -> str:
        """Format one envelope for the history section of the prompt.

        When *compress_tools* is True and the envelope is a tool result or
        contains tool_call blocks, the content is compressed to a summary.
        """
        to_str = ", ".join(env.to) if env.to else "(all)"
        cc_str = f" | CC: {', '.join(env.cc)}" if env.cc else ""
        header = f"[{env.timestamp[:19]}] From: {env.sender} → To: {to_str}{cc_str}\n"

        if not compress_tools:
            return header + env.content

        # Case 1: tool result (platform message)
        if env.metadata.get("tool_feedback"):
            tools_summary = []
            for tr in env.metadata.get("tool_results", []):
                tool_name = tr.get("tool", "?")
                result_text = tr.get("result", "") or ""
                preview = result_text[:self.TOOL_COMPRESS_PREVIEW_CHARS].replace("\n", " ")
                total = len(result_text)
                ok = "✗" if result_text[:50].startswith("错误") else "✓"
                suffix = "…" if len(result_text) > self.TOOL_COMPRESS_PREVIEW_CHARS else ""
                tools_summary.append(
                    f"  {ok} {tool_name} | {total}字 | {preview}{suffix}"
                )
            return header + "【工具结果摘要】\n" + "\n".join(tools_summary)

        # Case 2: agent reply containing tool_call blocks
        content = env.content or ""
        if "```tool_call" in content or "```toolcall" in content or "```tool-call" in content:
            tool_names = _extract_tool_names(content)
            first_marker = None
            for marker in ("```tool_call", "```toolcall", "```tool-call"):
                idx = content.find(marker)
                if idx != -1 and (first_marker is None or idx < first_marker):
                    first_marker = idx
            body = content[:first_marker].strip() if first_marker is not None else ""
            tags = f"[已调用: {', '.join(tool_names)}]" if tool_names else ""
            if body:
                return header + body + "\n" + tags
            else:
                return header + f"[工具调用: {', '.join(tool_names)}]" if tool_names else header + "(工具调用)"

        # Case 3: normal message
        return header + content

    def _build_prompt_for(self, agent_name: str, new_envelope: Envelope) -> str:
        """Build text-only prompt string (used when no images)."""
        inbox = self._inbox_for(agent_name)
        history_lines = []
        for i, env in enumerate(inbox):
            if env.id == new_envelope.id:
                continue
            is_old = i < len(inbox) - self.KEEP_FULL_MESSAGES
            history_lines.append(self._format_for_prompt(env, compress_tools=is_old))

        new_msg = f"【新消息 from {new_envelope.sender}】\n{new_envelope.content}"

        parts = []
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

    async def dispatch_internal(
        self,
        sender: str,
        to: list[str],
        cc: list[str],
        content: str,
        metadata: dict[str, Any] | None = None,
        images: list[str] | None = None,
    ) -> None:
        metadata = dict(metadata or {})
        dedup_to = list(dict.fromkeys([name for name in to if name]))
        dedup_cc = list(dict.fromkeys([name for name in cc if name and name not in dedup_to]))
        flood_warning = self.check_flood(sender=sender, to=dedup_to)
        if flood_warning:
            metadata["flood_warning"] = True
            orchestrator = self._registry.get_orchestrator_name()
            if orchestrator and orchestrator != sender and orchestrator not in dedup_cc:
                dedup_cc.append(orchestrator)
        # Lint: if message text mentions @name but recipients do not include name.
        # This is advisory-only and does not block delivery.
        if sender not in {"user", "platform"}:
            recipient_lc = {n.lower() for n in dedup_to + dedup_cc}
            missing_mentions = [
                name for name in self._extract_mentions(content)
                if name.lower() not in recipient_lc
            ]
            if missing_mentions:
                advisory_text = (
                    "【系统提醒】检测到你在消息中提及了 "
                    + ", ".join(f"@{name}" for name in missing_mentions)
                    + "，但当前 to/cc 未包含这些成员。请确认是否发错对象。"
                )
                self.record_system_advisory(
                    to_agent=sender,
                    text=advisory_text,
                    metadata={
                        "mention_lint": True,
                        "missing_mentions": missing_mentions,
                    },
                )
        broadcaster = self._broadcaster
        thread_id = self._thread_id

        async def _consume() -> None:
            async for event in self._dispatch_inner(
                sender=sender,
                to=dedup_to,
                cc=dedup_cc,
                content=content,
                metadata=metadata,
                images=images or [],
                _depth=0,
            ):
                if broadcaster is not None:
                    try:
                        await broadcaster(thread_id, event)
                    except Exception:
                        pass
        asyncio.create_task(_consume())

    async def notify_assignee(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("id", ""))
        assignee = str(task.get("assignee", ""))
        title = str(task.get("title", "")).strip() or "未命名任务"
        brief = str(task.get("brief", "")).strip()
        deadline = task.get("deadline")
        ddl_text = f"\nDDL: {deadline}" if deadline else ""
        content = f"【新任务】{task_id} - {title}\n{brief}{ddl_text}".strip()
        await self.dispatch_internal(
            sender="orchestrator",
            to=[assignee],
            cc=[],
            content=content,
            metadata={"type": "task_assignment", "task_id": task_id},
        )

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

        # Ensure runtime skill state is seeded from YAML
        self.seed_agent_skills_from_yaml(agent_name)

        session = self._session_store.load(agent_name, self._thread_id)
        if session is None:
            session = agent.create_session()

        current_envelope = envelope
        auto_continue_rounds = 0
        max_auto_continue_rounds = 15
        tools_called = False
        has_outgoing_comm = False
        advisory_sent = False
        handoff_gap_rounds = 0

        while True:
            # Trim SQLite agent history before it exceeds model context
            self._trim_sqlite_history(agent_name)
            # Proactive summarization: compress before token budget is exceeded
            if self._needs_token_based_summary(agent_name):
                await self.do_rolling_summary()
            run_input = self._build_run_input(agent_name, current_envelope)
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
                break

            reply_text = "".join(full_reply)
            if not reply_text.strip():
                break

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

            should_continue = False
            if self._tool_executor is not None:
                try:
                    tool_results = await self._tool_executor(self._thread_id, agent_name, reply_text)
                except Exception:
                    logger.exception("Tool executor failed for agent '%s'", agent_name)
                    tool_results = []
                if tool_results:
                    tools_called = True
                    outgoing_this_round = any(
                        (item.get("tool") or "") in _OUTGOING_COMM_TOOLS for item in tool_results
                    )
                    if outgoing_this_round:
                        has_outgoing_comm = True
                        handoff_gap_rounds = 0
                    else:
                        handoff_gap_rounds += 1
                    formatted_results = [
                        {
                            "tool": item.get("tool", ""),
                            "result": item.get("result", ""),
                            "triggered_by": agent_name,
                        }
                        for item in tool_results
                    ]
                    yield {
                        "type": "tool_results",
                        "agent": agent_name,
                        "results": formatted_results,
                    }
                    # ask_user is a blocking operation — agent must wait for user answer
                    has_ask_user = any(
                        (item.get("tool") or "") == "ask_user" for item in tool_results
                    )
                    if has_ask_user:
                        self.record_tool_feedback(agent_name, tool_results)
                    elif auto_continue_rounds < max_auto_continue_rounds:
                        feedback_env = self.record_tool_feedback(agent_name, tool_results)
                        if feedback_env:
                            current_envelope = Envelope.from_dict(feedback_env)
                            auto_continue_rounds += 1
                            should_continue = True

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
            if (
                not should_continue
                and tools_called
                and not has_outgoing_comm
                and not advisory_sent
                and handoff_gap_rounds >= 2
            ):
                advisory_text = (
                    "【系统提醒｜非用户反馈】你已获取工具执行结果，但尚未将结果传达给其他成员。"
                    "请使用 send_message、assign_task 或 submit_deliverable 将结果整理后发送给下游成员，避免信息断层。"
                )
                advisory_env = self.record_system_advisory(
                    to_agent=agent_name,
                    text=advisory_text,
                    metadata={"missing_handoff": True},
                )
                if advisory_env:
                    current_envelope = Envelope.from_dict(advisory_env)
                    advisory_sent = True
                    should_continue = True
            if should_continue:
                continue
            break

        self._session_store.save(agent_name, self._thread_id, session)

    async def _collect_agent_events(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Collect all events from _run_agent into a list (for gather)."""
        events: list[dict[str, Any]] = []
        async for event in self._run_agent(**kwargs):
            events.append(event)
        return events

    def check_flood(self, sender: str, to: list[str]) -> bool:
        now = datetime.now().timestamp()
        key = (sender, tuple(sorted(set(to))))
        window_seconds = 300
        limit = 6
        recent = [ts for ts in self._recent_msgs.get(key, []) if now - ts <= window_seconds]
        is_flood = len(recent) >= limit
        if not is_flood:
            recent.append(now)
        self._recent_msgs[key] = recent
        return is_flood

    def record_tool_feedback(self, triggered_by: str, tool_results: list[dict[str, str]]) -> dict[str, Any] | None:
        """
        Write tool execution results into the global log so agents see them on the next turn.

        Without this, outcomes only go to the WebSocket UI as ``tool_result`` events and are
        never included in :meth:`_build_prompt_for` history.
        """
        if not tool_results or not (triggered_by or "").strip():
            return None
        lines: list[str] = []
        for tr in tool_results:
            result = str(tr.get("result", ""))
            tool_name = str(tr.get("tool", ""))
            if len(result) > self.MAX_TOOL_RESULT_CHARS:
                total = len(result)
                result = (
                    result[:self.MAX_TOOL_RESULT_CHARS]
                    + f"\n\n⚠️ 结果过长（{total} 字符），已截断至前 {self.MAX_TOOL_RESULT_CHARS} 字符。"
                    f"如需完整内容，请用 {tool_name} 重新读取指定片段。"
                )
            lines.append(f"【{tool_name}】{result}")
        content = "【工具执行结果】\n" + "\n".join(lines)
        structured = [
            {
                "tool": str(tr.get("tool", "")),
                "result": str(tr.get("result", "")),
                "triggered_by": triggered_by,
            }
            for tr in tool_results
        ]
        envelope = Envelope(
            id=self._next_id(),
            sender="platform",
            to=[triggered_by],
            cc=[],
            content=content,
            metadata={"tool_feedback": True, "tool_results": structured},
        )
        self._global_log.append(envelope)
        self._flush_log()
        return envelope.to_dict()

    def record_system_advisory(
        self,
        *,
        to_agent: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not (to_agent or "").strip() or not (text or "").strip():
            return None
        final_metadata = {"system_advisory": True}
        if metadata:
            final_metadata.update(metadata)
        envelope = Envelope(
            id=self._next_id(),
            sender="platform",
            to=[to_agent],
            cc=[],
            content=text,
            metadata=final_metadata,
        )
        self._global_log.append(envelope)
        self._flush_log()
        return envelope.to_dict()

    def get_global_log(self) -> list[dict[str, Any]]:
        return [env.to_dict() for env in self._global_log]

    def get_recent_envelopes(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the most recent n envelopes as dicts (for summarization)."""
        return [env.to_dict() for env in self._global_log[-n:]]

    def needs_summarization(self) -> bool:
        """True when global log is large enough to warrant rolling summary (message-count fallback)."""
        return len(self._global_log) > int(self._max_inbox_messages * 0.8)

    def _needs_token_based_summary(self, agent_name: str) -> bool:
        """Check if agent's estimated inbox tokens exceed budget before an LLM call."""
        inbox = self._inbox_for(agent_name)
        total_est = sum(self._estimate_tokens(e.content or "") + 20 for e in inbox)
        summary_est = self._estimate_tokens(self._inbox_summary.get(agent_name, ""))
        threshold = int(self.MODEL_MAX_TOKENS * self.TOKEN_BUDGET_RATIO)
        return (total_est + summary_est) > threshold

    def _trim_sqlite_history(self, agent_name: str, max_rows: int = 50) -> None:
        """Limit per-agent SQLite history rows so context stays under model limit."""
        if self._log_path is None:
            return
        db_path = self._log_path.parent.parent / "memory" / "long_term.db"
        if not db_path.exists():
            return
        try:
            import sqlite3
            db = sqlite3.connect(str(db_path))
            table = f"history_{agent_name}"
            cnt = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            if cnt and cnt[0] > max_rows:
                last = db.execute(f'SELECT rowid FROM "{table}" ORDER BY rowid DESC LIMIT ?', (max_rows,)).fetchall()
                if last:
                    min_id = min(r[0] for r in last)
                    db.execute(f'DELETE FROM "{table}" WHERE rowid < ?', (min_id,))
                    db.commit()
            db.close()
        except Exception:
            pass  # table may not exist yet or agent name mismatch

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
