"""Explicit conversation checkpoints.

Each checkpoint captures:
  - The chat log (chat_log/<thread_id>.json)
  - Agent session files (sessions/<thread_id>_<agent>.json) — if present
  - A snapshot of context.md at the time of creation

Stored in the same platform.db database as conversation metadata.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


class CheckpointStore:
    """
    Saves and restores named snapshots of a conversation's private state.

    Schema (checkpoints table):
        id          TEXT PRIMARY KEY       — UUID or slug
        thread_id   TEXT NOT NULL
        project     TEXT NOT NULL
        note        TEXT NOT NULL          — user-supplied label
        created_at  DATETIME
        chat_log    TEXT                   — JSON-encoded list[Envelope-dict]
        sessions    TEXT                   — JSON-encoded dict{agent: session_dict}
        context_md  TEXT                   — context.md content at save time
    """

    def __init__(self, db_path: str | Path, project_dir: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._project_dir = Path(project_dir)

    # ── schema ────────────────────────────────────────────────────────────────

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id          TEXT PRIMARY KEY,
                    thread_id   TEXT NOT NULL,
                    project     TEXT NOT NULL,
                    note        TEXT NOT NULL DEFAULT '',
                    created_at  DATETIME NOT NULL,
                    chat_log    TEXT NOT NULL DEFAULT '[]',
                    sessions    TEXT NOT NULL DEFAULT '{}',
                    context_md  TEXT NOT NULL DEFAULT ''
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_chk_thread ON checkpoints(thread_id)"
            )
            await db.commit()
        logger.debug("CheckpointStore initialized at %s", self._db_path)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _chat_log_path(self, thread_id: str) -> Path:
        return self._project_dir / "chat_log" / f"{thread_id}.json"

    def _sessions_dir(self, thread_id: str) -> Path:
        return self._project_dir / "sessions"

    def _context_md_path(self) -> Path:
        return self._project_dir / "context.md"

    def _read_chat_log(self, thread_id: str) -> list:
        p = self._chat_log_path(thread_id)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _read_sessions(self, thread_id: str) -> dict:
        sdir = self._sessions_dir(thread_id)
        result: dict[str, object] = {}
        if sdir.is_dir():
            for f in sdir.glob(f"{thread_id}_*.json"):
                agent_name = f.stem[len(thread_id) + 1:]
                try:
                    result[agent_name] = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return result

    def _read_context_md(self) -> str:
        p = self._context_md_path()
        return p.read_text(encoding="utf-8") if p.exists() else ""

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create(
        self,
        thread_id: str,
        project: str,
        note: str,
        checkpoint_id: str,
        anchor_message_id: str | None = None,
    ) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        chat_log_obj = self._read_chat_log(thread_id)
        if anchor_message_id:
            # Keep messages up to and including the anchor message.
            # If anchor is not found, fall back to full snapshot.
            for i, msg in enumerate(chat_log_obj):
                if msg.get("id") == anchor_message_id:
                    chat_log_obj = chat_log_obj[: i + 1]
                    break
        chat_log = json.dumps(chat_log_obj, ensure_ascii=False)
        sessions = json.dumps(self._read_sessions(thread_id), ensure_ascii=False)
        context_md = self._read_context_md()

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO checkpoints (id, thread_id, project, note, created_at, chat_log, sessions, context_md)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (checkpoint_id, thread_id, project, note, now, chat_log, sessions, context_md),
            )
            await db.commit()

        logger.info(
            "Checkpoint created id=%s thread=%s note=%r anchor=%s msgs=%d",
            checkpoint_id, thread_id, note, anchor_message_id, len(chat_log_obj)
        )
        return {
            "id": checkpoint_id,
            "thread_id": thread_id,
            "project": project,
            "note": note,
            "created_at": now,
            "anchor_message_id": anchor_message_id,
            "message_count": len(chat_log_obj),
        }

    async def list_by_thread(self, thread_id: str) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT id, thread_id, project, note, created_at
                   FROM checkpoints WHERE thread_id = ?
                   ORDER BY created_at DESC""",
                (thread_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get(self, checkpoint_id: str) -> dict | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def delete(self, checkpoint_id: str) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM checkpoints WHERE id = ?", (checkpoint_id,))
            await db.commit()
            changed = db.total_changes > 0
        return changed

    async def restore(self, checkpoint_id: str, restore_context: bool = False) -> dict | None:
        """
        Overwrite the conversation's private state with the snapshot.

        Returns the checkpoint metadata on success, None if not found.
        If restore_context is True, also overwrites context.md.
        """
        row = await self.get(checkpoint_id)
        if not row:
            return None

        thread_id = row["thread_id"]

        # Restore chat log
        chat_log_path = self._chat_log_path(thread_id)
        chat_log_path.parent.mkdir(parents=True, exist_ok=True)
        chat_log_path.write_text(row["chat_log"], encoding="utf-8")

        # Restore session files
        sessions_data: dict = json.loads(row["sessions"])
        sdir = self._sessions_dir(thread_id)
        # Remove existing session files for this thread
        if sdir.is_dir():
            for f in sdir.glob(f"{thread_id}_*.json"):
                f.unlink(missing_ok=True)
        if sessions_data:
            sdir.mkdir(parents=True, exist_ok=True)
            for agent_name, session_obj in sessions_data.items():
                sfile = sdir / f"{thread_id}_{agent_name}.json"
                sfile.write_text(json.dumps(session_obj, ensure_ascii=False, indent=2), encoding="utf-8")

        # Optionally restore context.md
        if restore_context and row["context_md"]:
            ctx_path = self._context_md_path()
            ctx_path.parent.mkdir(parents=True, exist_ok=True)
            ctx_path.write_text(row["context_md"], encoding="utf-8")

        logger.info(
            "Checkpoint restored id=%s thread=%s restore_context=%s",
            checkpoint_id, thread_id, restore_context,
        )
        return {
            "id": row["id"],
            "thread_id": thread_id,
            "project": row["project"],
            "note": row["note"],
            "created_at": row["created_at"],
            "context_diff": row["context_md"] if not restore_context else None,
        }
