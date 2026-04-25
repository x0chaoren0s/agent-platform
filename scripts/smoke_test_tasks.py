from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiosqlite

from core.conversation_store import ConversationStore
from core.question_store import QuestionStore, UserQuestion
from core.task_store import Task, TaskStore


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="task-smoke-") as tmp:
        root = Path(tmp)
        db_path = root / "memory" / "tasks.db"
        store = TaskStore(db_path=db_path, project="smoke")
        qstore = QuestionStore(db_path=db_path, project="smoke")
        cstore = ConversationStore(db_path=root / "memory" / "platform.db")
        await store.init_db()
        await qstore.init_db()
        await cstore.init_db()

        thread_id = "smoke-thread"

        task_a = await store.create(
            Task(
                id="",
                project="smoke",
                thread_id=thread_id,
                title="A task",
                brief="upstream",
                assignee="alice",
                created_by="orchestrator",
            )
        )
        assert task_a.id == "task-0001"
        assert task_a.status == "ready"

        task_b = await store.create(
            Task(
                id="",
                project="smoke",
                thread_id=thread_id,
                title="B task",
                brief="downstream",
                assignee="bob",
                created_by="orchestrator",
                depends_on=[task_a.id],
            )
        )
        assert task_b.id == "task-0002"
        assert task_b.status == "pending"

        pending_bob_before = await store.list_pending_by_assignee(thread_id, "bob")
        assert [t.id for t in pending_bob_before] == [task_b.id]

        deliverable_rel = store.write_deliverable_file("reports/task-a.md", "hello")
        task_a_done = await store.submit_deliverable(
            task_a.id,
            path=deliverable_rel,
            summary="finished A",
            actor="alice",
        )
        assert task_a_done is not None
        assert task_a_done.status == "done"

        ready_downstream = await store.find_ready_downstream(task_a.id)
        assert [t.id for t in ready_downstream] == [task_b.id]
        assert ready_downstream[0].status == "ready"

        pending_bob_after = await store.list_pending_by_assignee(thread_id, "bob")
        assert pending_bob_after == []

        q = await qstore.create(
            UserQuestion(
                id="",
                project="smoke",
                thread_id=thread_id,
                asker="alice",
                question="pick one",
                options=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            )
        )
        assert q.id == "q-0001"
        pending_questions = await qstore.list_pending(thread_id)
        assert [item.id for item in pending_questions] == [q.id]

        answered = await qstore.answer(q.id, answer="a")
        assert answered is not None
        assert answered.status == "answered"
        pending_questions_after = await qstore.list_pending(thread_id)
        assert pending_questions_after == []

        # Validate task silence detection and advisory debounce.
        task_c = await store.create(
            Task(
                id="",
                project="smoke",
                thread_id=thread_id,
                title="C high task",
                brief="urgent",
                assignee="alice",
                created_by="orchestrator",
                priority="high",
            )
        )
        task_d = await store.create(
            Task(
                id="",
                project="smoke",
                thread_id=thread_id,
                title="D normal task",
                brief="routine",
                assignee="bob",
                created_by="orchestrator",
                priority="normal",
            )
        )
        old_iso = (datetime.now() - timedelta(minutes=20)).isoformat(timespec="seconds")
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (old_iso, task_c.id))
            await db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (old_iso, task_d.id))
            await db.commit()

        now_iso = datetime.now().isoformat(timespec="seconds")
        silent_first = await store.list_silent_tasks(
            thread_id=thread_id,
            now_iso=now_iso,
            thresholds_seconds={"high": 600, "normal": 1800, "low": 7200},
            advisory_min_gap_seconds=600,
        )
        silent_ids = {item.id for item in silent_first}
        assert task_c.id in silent_ids
        assert task_d.id not in silent_ids

        await store.mark_advisory_sent(task_c.id, now_iso)
        last_advisory = await store.get_last_advisory_ts(task_c.id)
        assert last_advisory is not None

        silent_second = await store.list_silent_tasks(
            thread_id=thread_id,
            now_iso=datetime.now().isoformat(timespec="seconds"),
            thresholds_seconds={"high": 600, "normal": 1800, "low": 7200},
            advisory_min_gap_seconds=600,
        )
        assert task_c.id not in {item.id for item in silent_second}

        # Validate conversation pause flags.
        await cstore.create(thread_id=thread_id, project="smoke", name="smoke")
        assert await cstore.is_paused(thread_id) is False
        changed = await cstore.set_paused(thread_id, True)
        assert changed is True
        assert await cstore.is_paused(thread_id) is True
        changed = await cstore.set_paused(thread_id, False)
        assert changed is True
        assert await cstore.is_paused(thread_id) is False

        print("SMOKE_OK")
        print(f"task_a={task_a.id}, task_b={task_b.id}, question={q.id}")


if __name__ == "__main__":
    asyncio.run(main())
