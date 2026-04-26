from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

from .conversation_store import ConversationStore
from .task_store import TaskStore

logger = logging.getLogger("heartbeat")


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).strip()
    if not raw:
        return None
    # Supports both "2026-04-25T22:38:52" and "2026-04-25 15:08:55"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


class HeartbeatScheduler:
    def __init__(
        self,
        *,
        interval_seconds: int,
        thresholds_seconds: dict[str, int],
        advisory_min_gap_seconds: int,
        conversation_store: ConversationStore,
        thread_ids_provider: Callable[[], list[str]],
        project_dir_provider: Callable[[str], str | None],
        router_provider: Callable[[str], object | None],
        task_store_provider: Callable[[str], Awaitable[TaskStore]],
        broadcaster: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._thresholds_seconds = thresholds_seconds
        self._advisory_min_gap_seconds = advisory_min_gap_seconds
        self._conversation_store = conversation_store
        self._thread_ids_provider = thread_ids_provider
        self._project_dir_provider = project_dir_provider
        self._router_provider = router_provider
        self._task_store_provider = task_store_provider
        self._broadcaster = broadcaster
        self._task: asyncio.Task | None = None
        self._last_silent_count: dict[str, int] = {}

    def get_last_silent_count(self, thread_id: str) -> int:
        return int(self._last_silent_count.get(thread_id, 0))

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                await self._tick()
        except asyncio.CancelledError:
            logger.info("Heartbeat scheduler stopped")
            raise

    async def _tick(self) -> None:
        thread_ids = list(dict.fromkeys(self._thread_ids_provider()))
        if not thread_ids:
            return
        for thread_id in thread_ids:
            try:
                await self._scan_thread(thread_id)
            except Exception:
                logger.exception("Heartbeat scan failed: thread=%s", thread_id)

    async def _scan_thread(self, thread_id: str) -> None:
        async def _emit_heartbeat(count: int) -> None:
            if self._broadcaster is not None:
                await self._broadcaster(
                    thread_id,
                    {"type": "heartbeat", "thread_id": thread_id, "silent_count": int(count)},
                )

        is_paused = await self._conversation_store.is_paused(thread_id)
        if is_paused:
            self._last_silent_count[thread_id] = 0
            await _emit_heartbeat(0)
            return
        project_dir = self._project_dir_provider(thread_id)
        router = self._router_provider(thread_id)
        if not project_dir or router is None:
            self._last_silent_count[thread_id] = 0
            await _emit_heartbeat(0)
            return
        store = await self._task_store_provider(project_dir)
        now_iso = datetime.now().isoformat(timespec="seconds")
        silent_tasks = await store.list_silent_tasks(
            thread_id=thread_id,
            now_iso=now_iso,
            thresholds_seconds=self._thresholds_seconds,
            advisory_min_gap_seconds=self._advisory_min_gap_seconds,
        )
        if not silent_tasks:
            self._last_silent_count[thread_id] = 0
            await _emit_heartbeat(0)
            logger.debug("Heartbeat tick: no silent tasks, thread=%s", thread_id)
            return

        # --- Build known agents set ---
        known_agents: set[str] = set()
        if hasattr(router, "_registry"):
            try:
                known_agents = set(router._registry.all().keys())  # noqa: SLF001
            except Exception:
                pass

        # --- Build assignee last-message map from router log ---
        last_sent: dict[str, str] = {}
        if hasattr(router, "get_recent_envelopes"):
            try:
                for env in router.get_recent_envelopes(300):
                    sender = env.get("sender", "")
                    ts = env.get("timestamp", "")
                    if sender and ts and ts > last_sent.get(sender, ""):
                        last_sent[sender] = ts
            except Exception:
                pass

        # --- Orphan tasks: query ALL active tasks independently (bypass silent debounce) ---
        _ORPHAN_GAP_SECONDS = 86400
        all_active = await store.list_active_tasks(thread_id)
        orphaned_to_notify: list = []
        for task in all_active:
            if task.assignee in known_agents:
                continue
            last_ts = await store.get_last_orphan_advisory_ts(task.id)
            if last_ts is None:
                orphaned_to_notify.append(task)
            else:
                try:
                    elapsed = (
                        datetime.fromisoformat(now_iso) - datetime.fromisoformat(last_ts)
                    ).total_seconds()
                    if elapsed >= _ORPHAN_GAP_SECONDS:
                        orphaned_to_notify.append(task)
                except Exception:
                    orphaned_to_notify.append(task)

        # --- Genuinely silent tasks: assignee exists but no recent activity ---
        final_silent = []
        for t in silent_tasks:
            if t.assignee not in known_agents:
                continue
            task_updated_dt = _parse_ts(t.updated_at)
            assignee_last_dt = _parse_ts(last_sent.get(t.assignee))
            # Keep as silent when there is no assignee message, or assignee message
            # is not newer than task.updated_at.
            if assignee_last_dt is None or task_updated_dt is None or assignee_last_dt <= task_updated_dt:
                final_silent.append(t)

        total_advisory_count = len(orphaned_to_notify) + len(final_silent)
        self._last_silent_count[thread_id] = total_advisory_count

        if not orphaned_to_notify and not final_silent:
            await _emit_heartbeat(total_advisory_count)
            logger.debug("Heartbeat tick: all tasks filtered, thread=%s", thread_id)
            return

        async def _send_advisory(text: str, mark_silent: list, mark_orphan: list) -> None:
            if not hasattr(router, "record_system_advisory"):
                return
            env_dict = router.record_system_advisory(
                to_agent="orchestrator",
                text=text,
                metadata={"thread_id": thread_id, "project_dir": project_dir},
            )
            if env_dict is not None and self._broadcaster is not None:
                try:
                    await self._broadcaster(
                        thread_id, {"type": "envelope_recorded", "envelope": env_dict}
                    )
                except Exception:
                    pass
            for task in mark_silent:
                await store.mark_advisory_sent(task.id, now_iso)
            for task in mark_orphan:
                await store.mark_orphan_advisory_sent(task.id, now_iso)

        if orphaned_to_notify:
            lines = ["⚠️ 【孤儿任务警告】以下任务的负责人已不在团队，需重新分配或取消："]
            for t in orphaned_to_notify:
                lines.append(
                    f"- {t.id}《{t.title}》原负责人={t.assignee} | priority={t.priority}"
                )
            await _send_advisory("\n".join(lines), mark_silent=[], mark_orphan=orphaned_to_notify)

        if final_silent:
            lines = ["【系统提醒】以下任务长时间无进展，请优先协调："]
            for t in final_silent:
                lines.append(
                    f"- {t.id} | {t.title} | assignee={t.assignee} | "
                    f"priority={t.priority} | last_update={t.updated_at}"
                )
            await _send_advisory("\n".join(lines), mark_silent=final_silent, mark_orphan=[])

        await _emit_heartbeat(total_advisory_count)
        logger.info(
            "Heartbeat: thread=%s orphaned=%d silent=%d",
            thread_id, len(orphaned_to_notify), len(final_silent),
        )
