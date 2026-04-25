from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.question_store import QuestionStore, UserQuestion
from core.task_store import Task, TaskStore


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="task-smoke-") as tmp:
        root = Path(tmp)
        db_path = root / "memory" / "tasks.db"
        store = TaskStore(db_path=db_path, project="smoke")
        qstore = QuestionStore(db_path=db_path, project="smoke")
        await store.init_db()
        await qstore.init_db()

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

        print("SMOKE_OK")
        print(f"task_a={task_a.id}, task_b={task_b.id}, question={q.id}")


if __name__ == "__main__":
    asyncio.run(main())
