"""Question persistence for ask_user workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_questions (
    id            TEXT PRIMARY KEY,
    project       TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    asker         TEXT NOT NULL,
    related_task  TEXT,
    question      TEXT NOT NULL,
    options_json  TEXT,
    urgency       TEXT NOT NULL DEFAULT 'normal',
    status        TEXT NOT NULL DEFAULT 'pending',
    answer        TEXT,
    asked_at      TEXT NOT NULL,
    answered_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_q_thread_status ON user_questions(thread_id, status);
"""


@dataclass
class UserQuestion:
    id: str
    project: str
    thread_id: str
    asker: str
    question: str
    options: list[dict[str, str]] | None = None
    related_task: str | None = None
    urgency: str = "normal"
    status: str = "pending"
    answer: str | None = None
    asked_at: str = ""
    answered_at: str | None = None


class QuestionStore:
    def __init__(self, db_path: Path, project: str):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._project = project

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def create(self, q: UserQuestion) -> UserQuestion:
        now = datetime.now().isoformat(timespec="seconds")
        q_id = await self._next_question_id()
        options_json = json.dumps(q.options, ensure_ascii=False) if q.options is not None else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO user_questions(
                    id, project, thread_id, asker, related_task, question, options_json,
                    urgency, status, answer, asked_at, answered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    q_id,
                    self._project,
                    q.thread_id,
                    q.asker,
                    q.related_task,
                    q.question,
                    options_json,
                    q.urgency,
                    "pending",
                    None,
                    now,
                    None,
                ),
            )
            await db.commit()
        created = await self.get(q_id)
        if not created:
            raise RuntimeError(f"failed to create question: {q_id}")
        return created

    async def get(self, q_id: str) -> UserQuestion | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM user_questions WHERE id = ?",
                (q_id,),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_question(row)

    async def list_pending(self, thread_id: str) -> list[UserQuestion]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM user_questions
                 WHERE thread_id = ? AND status = 'pending'
                 ORDER BY asked_at ASC
                """,
                (thread_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_question(row) for row in rows]

    async def answer(self, q_id: str, *, answer: str) -> UserQuestion | None:
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                UPDATE user_questions
                   SET status = 'answered',
                       answer = ?,
                       answered_at = ?
                 WHERE id = ?
                   AND status = 'pending'
                """,
                (answer, now, q_id),
            )
            if db.total_changes <= 0:
                return None
            await db.commit()
        return await self.get(q_id)

    async def cancel(self, q_id: str, reason: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                UPDATE user_questions
                   SET status = 'cancelled',
                       answer = COALESCE(answer, ?),
                       answered_at = COALESCE(answered_at, ?)
                 WHERE id = ? AND status = 'pending'
                """,
                (reason, now, q_id),
            )
            await db.commit()

    async def _next_question_id(self) -> str:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT id FROM user_questions ORDER BY id DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
        if not row or not row[0]:
            return "q-0001"
        seq = int(str(row[0]).split("-")[-1])
        return f"q-{seq + 1:04d}"

    def _row_to_question(self, row: aiosqlite.Row) -> UserQuestion:
        options = json.loads(row["options_json"]) if row["options_json"] else None
        return UserQuestion(
            id=row["id"],
            project=row["project"],
            thread_id=row["thread_id"],
            asker=row["asker"],
            question=row["question"],
            options=options,
            related_task=row["related_task"],
            urgency=row["urgency"],
            status=row["status"],
            answer=row["answer"],
            asked_at=row["asked_at"],
            answered_at=row["answered_at"],
        )
