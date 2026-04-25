"""Task persistence for team collaboration MVP-Plus."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    project          TEXT NOT NULL,
    thread_id        TEXT NOT NULL,
    title            TEXT NOT NULL,
    brief            TEXT NOT NULL,
    assignee         TEXT NOT NULL,
    created_by       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    priority         TEXT NOT NULL DEFAULT 'normal',
    deadline         TEXT,
    deliverable_kind TEXT NOT NULL DEFAULT 'markdown',
    deliverable_path TEXT,
    deliverable_summary TEXT,
    retries          INTEGER NOT NULL DEFAULT 0,
    max_retries      INTEGER NOT NULL DEFAULT 2,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    closed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_thread_status ON tasks(thread_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(thread_id, assignee);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id    TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_context_refs (
    task_id TEXT NOT NULL,
    ref     TEXT NOT NULL,
    PRIMARY KEY (task_id, ref),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    ts         TEXT NOT NULL,
    event      TEXT NOT NULL,
    actor      TEXT NOT NULL,
    note       TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_history_task ON task_history(task_id, ts);
"""


@dataclass
class Task:
    id: str
    project: str
    thread_id: str
    title: str
    brief: str
    assignee: str
    created_by: str
    status: str = "pending"
    priority: str = "normal"
    deadline: str | None = None
    deliverable_kind: str = "markdown"
    deliverable_path: str | None = None
    deliverable_summary: str | None = None
    depends_on: list[str] = field(default_factory=list)
    context_refs: list[str] = field(default_factory=list)
    retries: int = 0
    max_retries: int = 2
    created_at: str = ""
    updated_at: str = ""
    closed_at: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TaskHistoryEntry:
    ts: str
    event: str
    actor: str
    note: str | None = None


class TaskStore:
    def __init__(self, db_path: Path, project: str):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._project = project
        self._workspace_root = self._db_path.parent.parent / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def create(self, task: Task) -> Task:
        now = datetime.now().isoformat(timespec="seconds")
        task_id = await self._next_task_id(task.thread_id)
        status = "ready" if not task.depends_on else "pending"
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute(
                """
                INSERT INTO tasks (
                    id, project, thread_id, title, brief, assignee, created_by,
                    status, priority, deadline, deliverable_kind, deliverable_path,
                    deliverable_summary, retries, max_retries, created_at, updated_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    self._project,
                    task.thread_id,
                    task.title,
                    task.brief,
                    task.assignee,
                    task.created_by,
                    status,
                    task.priority,
                    task.deadline,
                    task.deliverable_kind,
                    task.deliverable_path,
                    task.deliverable_summary,
                    task.retries,
                    task.max_retries,
                    now,
                    now,
                    None,
                ),
            )
            for dep in task.depends_on:
                await db.execute(
                    "INSERT OR IGNORE INTO task_dependencies(task_id, depends_on) VALUES (?, ?)",
                    (task_id, dep),
                )
            for ref in task.context_refs:
                await db.execute(
                    "INSERT OR IGNORE INTO task_context_refs(task_id, ref) VALUES (?, ?)",
                    (task_id, ref),
                )
            await db.execute(
                "INSERT INTO task_history(task_id, ts, event, actor, note) VALUES (?, ?, ?, ?, ?)",
                (task_id, now, "created", task.created_by, task.brief),
            )
            await db.commit()
        created = await self.get(task_id)
        if not created:
            raise RuntimeError(f"failed to create task: {task_id}")
        return created

    async def get(self, task_id: str) -> Task | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            task = self._row_to_task(row)
            task.depends_on = await self._load_depends_on(db, task_id)
            task.context_refs = await self._load_context_refs(db, task_id)
        return task

    async def list(
        self,
        *,
        thread_id: str,
        status: str | None = None,
        assignee: str | None = None,
    ) -> list[Task]:
        clauses = ["thread_id = ?"]
        params: list[Any] = [thread_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if assignee:
            clauses.append("assignee = ?")
            params.append(assignee)
        where = " AND ".join(clauses)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM tasks WHERE {where} ORDER BY created_at ASC", tuple(params)
            ) as cur:
                rows = await cur.fetchall()
            tasks = [self._row_to_task(row) for row in rows]
            for task in tasks:
                task.depends_on = await self._load_depends_on(db, task.id)
                task.context_refs = await self._load_context_refs(db, task.id)
        return tasks

    async def update_status(
        self,
        task_id: str,
        *,
        new_status: str,
        actor: str,
        note: str | None = None,
    ) -> Task | None:
        now = datetime.now().isoformat(timespec="seconds")
        closed_at = now if new_status in {"done", "failed", "cancelled"} else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute(
                """
                UPDATE tasks
                   SET status = ?, updated_at = ?, closed_at = COALESCE(?, closed_at)
                 WHERE id = ?
                """,
                (new_status, now, closed_at, task_id),
            )
            if db.total_changes <= 0:
                return None
            event = "progress"
            if new_status == "blocked_on_user":
                event = "blocked"
            elif new_status == "ready":
                event = "unblocked"
            elif new_status == "in_progress":
                event = "started"
            elif new_status == "cancelled":
                event = "cancelled"
            elif new_status == "failed":
                event = "failed"
            elif new_status == "done":
                event = "accepted"
            await db.execute(
                "INSERT INTO task_history(task_id, ts, event, actor, note) VALUES (?, ?, ?, ?, ?)",
                (task_id, now, event, actor, note),
            )
            await db.commit()
        return await self.get(task_id)

    async def update_progress(self, task_id: str, *, note: str, actor: str) -> Task | None:
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?",
                (now, task_id),
            )
            if db.total_changes <= 0:
                return None
            await db.execute(
                "INSERT INTO task_history(task_id, ts, event, actor, note) VALUES (?, ?, ?, ?, ?)",
                (task_id, now, "progress", actor, note),
            )
            await db.commit()
        return await self.get(task_id)

    async def submit_deliverable(
        self,
        task_id: str,
        *,
        path: str,
        summary: str,
        actor: str,
    ) -> Task | None:
        deliverable_path = self._normalize_workspace_path(path)
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute(
                """
                UPDATE tasks
                   SET status = 'done',
                       deliverable_path = ?,
                       deliverable_summary = ?,
                       updated_at = ?,
                       closed_at = ?
                 WHERE id = ?
                """,
                (deliverable_path, summary, now, now, task_id),
            )
            if db.total_changes <= 0:
                return None
            await db.execute(
                "INSERT INTO task_history(task_id, ts, event, actor, note) VALUES (?, ?, ?, ?, ?)",
                (task_id, now, "delivered", actor, summary),
            )
            await db.commit()
        return await self.get(task_id)

    async def find_ready_downstream(self, completed_task_id: str) -> list[Task]:
        now = datetime.now().isoformat(timespec="seconds")
        completed = await self.get(completed_task_id)
        if not completed:
            return []
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT t.*
                  FROM tasks t
                  JOIN task_dependencies d ON t.id = d.task_id
                 WHERE d.depends_on = ?
                   AND t.thread_id = ?
                   AND t.status = 'pending'
                """,
                (completed_task_id, completed.thread_id),
            ) as cur:
                candidates = await cur.fetchall()
            ready_ids: list[str] = []
            for row in candidates:
                task_id = row["id"]
                async with db.execute(
                    """
                    SELECT COUNT(*) AS left_count
                      FROM task_dependencies d
                      JOIN tasks up ON up.id = d.depends_on
                     WHERE d.task_id = ?
                       AND up.status != 'done'
                    """,
                    (task_id,),
                ) as dcur:
                    dep_row = await dcur.fetchone()
                if dep_row and dep_row["left_count"] == 0:
                    ready_ids.append(task_id)
            for task_id in ready_ids:
                await db.execute(
                    "UPDATE tasks SET status = 'ready', updated_at = ? WHERE id = ?",
                    (now, task_id),
                )
                await db.execute(
                    "INSERT INTO task_history(task_id, ts, event, actor, note) VALUES (?, ?, ?, ?, ?)",
                    (task_id, now, "unblocked", "system", f"dependency done: {completed_task_id}"),
                )
            await db.commit()
        ready_tasks: list[Task] = []
        for task_id in ready_ids:
            task = await self.get(task_id)
            if task:
                ready_tasks.append(task)
        return ready_tasks

    async def list_pending_by_assignee(self, thread_id: str, assignee: str) -> list[Task]:
        return await self.list(thread_id=thread_id, status="pending", assignee=assignee)

    async def history(self, task_id: str) -> list[TaskHistoryEntry]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT ts, event, actor, note FROM task_history WHERE task_id = ? ORDER BY ts ASC, id ASC",
                (task_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [TaskHistoryEntry(**dict(row)) for row in rows]

    def write_deliverable_file(self, relative_path: str, content: str) -> str:
        normalized = self._normalize_workspace_path(relative_path)
        file_path = self._workspace_root / normalized
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return normalized

    def _normalize_workspace_path(self, path: str) -> str:
        candidate = (self._workspace_root / path).resolve()
        root = self._workspace_root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("deliverable path must stay inside workspace/")
        return str(candidate.relative_to(root)).replace("\\", "/")

    async def _next_task_id(self, thread_id: str) -> str:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT id FROM tasks WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
                (thread_id,),
            ) as cur:
                row = await cur.fetchone()
        if not row or not row[0]:
            return "task-0001"
        current = row[0]
        seq = int(current.split("-")[-1])
        return f"task-{seq + 1:04d}"

    async def _load_depends_on(self, db: aiosqlite.Connection, task_id: str) -> list[str]:
        async with db.execute(
            "SELECT depends_on FROM task_dependencies WHERE task_id = ? ORDER BY depends_on ASC",
            (task_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [row[0] for row in rows]

    async def _load_context_refs(self, db: aiosqlite.Connection, task_id: str) -> list[str]:
        async with db.execute(
            "SELECT ref FROM task_context_refs WHERE task_id = ? ORDER BY ref ASC",
            (task_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [row[0] for row in rows]

    def _row_to_task(self, row: aiosqlite.Row) -> Task:
        return Task(
            id=row["id"],
            project=row["project"],
            thread_id=row["thread_id"],
            title=row["title"],
            brief=row["brief"],
            assignee=row["assignee"],
            created_by=row["created_by"],
            status=row["status"],
            priority=row["priority"],
            deadline=row["deadline"],
            deliverable_kind=row["deliverable_kind"],
            deliverable_path=row["deliverable_path"],
            deliverable_summary=row["deliverable_summary"],
            retries=row["retries"],
            max_retries=row["max_retries"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            closed_at=row["closed_at"],
        )
