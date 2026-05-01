"""Skill proposal persistence for propose_skill / create_skill workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_proposals (
    id              TEXT PRIMARY KEY,
    project         TEXT NOT NULL,
    proposer        TEXT NOT NULL,
    thread_id       TEXT NOT NULL,
    skill_name      TEXT NOT NULL,
    description     TEXT NOT NULL,
    content         TEXT NOT NULL,
    extra_files_json TEXT,
    mount_to_json   TEXT,
    rationale       TEXT,
    scope           TEXT NOT NULL DEFAULT 'project',
    status          TEXT NOT NULL DEFAULT 'pending',
    orch_feedback   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_sp_status ON skill_proposals(status);
CREATE INDEX IF NOT EXISTS idx_sp_project ON skill_proposals(project);
"""


@dataclass
class SkillProposal:
    id: str
    project: str
    proposer: str
    thread_id: str
    skill_name: str
    description: str
    content: str
    extra_files: dict[str, str] | None = None
    mount_to: list[str] | None = None
    rationale: str | None = None
    scope: str = "project"
    status: str = "pending"
    orch_feedback: str | None = None
    created_at: str = ""
    updated_at: str | None = None


class SkillProposalStore:
    def __init__(self, db_path: Path, project: str):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._project = project

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def create(self, p: SkillProposal) -> SkillProposal:
        now = datetime.now().isoformat(timespec="seconds")
        p_id = await self._next_proposal_id()
        extra_json = json.dumps(p.extra_files, ensure_ascii=False) if p.extra_files else None
        mount_json = json.dumps(p.mount_to, ensure_ascii=False) if p.mount_to else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO skill_proposals(
                    id, project, proposer, thread_id, skill_name, description, content,
                    extra_files_json, mount_to_json, rationale, scope, status,
                    orch_feedback, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p_id,
                    self._project,
                    p.proposer,
                    p.thread_id,
                    p.skill_name,
                    p.description,
                    p.content,
                    extra_json,
                    mount_json,
                    p.rationale,
                    p.scope,
                    "pending",
                    None,
                    now,
                    None,
                ),
            )
            await db.commit()
        created = await self.get(p_id)
        if not created:
            raise RuntimeError(f"failed to create proposal: {p_id}")
        return created

    async def get(self, proposal_id: str) -> SkillProposal | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM skill_proposals WHERE id = ?",
                (proposal_id,),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_proposal(row)

    async def list_by_status(self, status: str | None = None) -> list[SkillProposal]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if status:
                async with db.execute(
                    "SELECT * FROM skill_proposals WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with db.execute(
                    "SELECT * FROM skill_proposals ORDER BY created_at DESC"
                ) as cur:
                    rows = await cur.fetchall()
        return [self._row_to_proposal(row) for row in rows]

    async def update_status(
        self, proposal_id: str, new_status: str, feedback: str | None = None
    ) -> SkillProposal | None:
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                UPDATE skill_proposals
                   SET status = ?,
                       orch_feedback = COALESCE(?, orch_feedback),
                       updated_at = ?
                 WHERE id = ?
                """,
                (new_status, feedback, now, proposal_id),
            )
            if db.total_changes <= 0:
                return None
            await db.commit()
        return await self.get(proposal_id)

    async def _next_proposal_id(self) -> str:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT id FROM skill_proposals ORDER BY id DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
        if not row or not row[0]:
            return "sp-0001"
        seq = int(str(row[0]).split("-")[-1])
        return f"sp-{seq + 1:04d}"

    @staticmethod
    def _row_to_proposal(row: aiosqlite.Row) -> SkillProposal:
        extra = json.loads(row["extra_files_json"]) if row["extra_files_json"] else None
        mount = json.loads(row["mount_to_json"]) if row["mount_to_json"] else None
        return SkillProposal(
            id=row["id"],
            project=row["project"],
            proposer=row["proposer"],
            thread_id=row["thread_id"],
            skill_name=row["skill_name"],
            description=row["description"],
            content=row["content"],
            extra_files=extra,
            mount_to=mount,
            rationale=row["rationale"],
            scope=row["scope"],
            status=row["status"],
            orch_feedback=row["orch_feedback"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
