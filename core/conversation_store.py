"""SQLite-backed conversation metadata store.

Tracks all conversations (thread_ids) per project with name, timestamps.
Database: projects/{project}/memory/platform.db
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_TABLE = "conversations"


class ConversationStore:
    """
    Manages conversation metadata in a per-project SQLite database.

    Schema:
        thread_id   TEXT PRIMARY KEY
        project     TEXT NOT NULL
        name        TEXT NOT NULL
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        last_active DATETIME DEFAULT CURRENT_TIMESTAMP
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ready = False
        self._init_lock = asyncio.Lock()

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            await self.init_db()
            self._ready = True

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    thread_id   TEXT PRIMARY KEY,
                    project     TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_active DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_conv_project ON {_TABLE}(project)"
            )
            await db.commit()
        logger.debug("ConversationStore initialized at %s", self._db_path)

    async def create(self, thread_id: str, project: str, name: str) -> dict:
        await self._ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"""
                INSERT OR IGNORE INTO {_TABLE} (thread_id, project, name, created_at, last_active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, project, name, now, now),
            )
            await db.commit()
        return {"thread_id": thread_id, "project": project, "name": name,
                "created_at": now, "last_active": now}

    async def get(self, thread_id: str) -> dict | None:
        await self._ensure_ready()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM {_TABLE} WHERE thread_id = ?", (thread_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_by_project(self, project: str) -> list[dict]:
        await self._ensure_ready()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM {_TABLE} WHERE project = ? ORDER BY last_active DESC",
                (project,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def rename(self, thread_id: str, new_name: str) -> bool:
        await self._ensure_ready()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE {_TABLE} SET name = ? WHERE thread_id = ?",
                (new_name, thread_id),
            )
            await db.commit()
            changes = db.total_changes
        return changes > 0

    async def delete(self, thread_id: str) -> bool:
        await self._ensure_ready()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"DELETE FROM {_TABLE} WHERE thread_id = ?", (thread_id,)
            )
            await db.commit()
            changes = db.total_changes
        return changes > 0

    async def touch(self, thread_id: str) -> None:
        await self._ensure_ready()
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE {_TABLE} SET last_active = ? WHERE thread_id = ?",
                (now, thread_id),
            )
            await db.commit()
