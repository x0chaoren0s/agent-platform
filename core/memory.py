"""SQLite-backed long-term history provider for MAF agents."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiosqlite

from agent_framework import HistoryProvider, Message
from agent_framework._types import Content

logger = logging.getLogger(__name__)


def _msg_to_row(msg: Message) -> str:
    """Serialize a Message to a JSON string for storage."""
    return json.dumps({"role": msg.role, "text": msg.text or ""})


def _row_to_msg(row_json: str) -> Message:
    """Deserialize a JSON string back to a Message."""
    try:
        data = json.loads(row_json)
        return Message(
            role=data["role"],
            contents=[Content(type="text", text=data.get("text", ""))],
        )
    except Exception:
        return Message(role="user", contents=[Content(type="text", text=row_json)])


class SQLiteHistoryProvider(HistoryProvider):
    """
    Persists conversation history in a SQLite database.
    Each agent has its own table keyed by session_id.
    """

    def __init__(self, db_path: str | Path, agent_id: str, max_messages: int = 100) -> None:
        super().__init__(source_id=f"sqlite-history-{agent_id}", load_messages=True)
        self._db_path = Path(db_path)
        self._agent_id = agent_id
        self._max_messages = max_messages
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._table = f"history_{self._agent_id.replace('-', '_')}"

    async def _ensure_table(self, db: aiosqlite.Connection) -> None:
        # Check if table exists with old schema (role+content columns) and migrate
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (self._table,)
        ) as cur:
            exists = await cur.fetchone()

        if exists:
            # Check columns
            async with db.execute(f'PRAGMA table_info("{self._table}")') as cur:
                cols = {row[1] async for row in cur}
            if "data" not in cols:
                # Old schema — drop and recreate (no valuable data yet)
                logger.info("Migrating table %s to new schema", self._table)
                await db.execute(f'DROP TABLE IF EXISTS "{self._table}"')
                await db.commit()

        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{self._table}" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{self._table}_session" ON "{self._table}"(session_id)'
        )
        await db.commit()

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        key = (state or {}).get(self.source_id, {}).get("history_key", session_id or "default")
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await self._ensure_table(db)
                async with db.execute(
                    f"""
                    SELECT data FROM (
                        SELECT id, data FROM "{self._table}"
                        WHERE session_id = ?
                        ORDER BY id DESC LIMIT ?
                    ) ORDER BY id ASC
                    """,
                    (key, self._max_messages),
                ) as cursor:
                    rows = await cursor.fetchall()
            return [_row_to_msg(row[0]) for row in rows]
        except Exception:
            logger.exception("Failed to load messages from SQLite for agent=%s", self._agent_id)
            return []

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not messages:
            return
        if state is not None:
            key = state.setdefault(self.source_id, {}).setdefault(
                "history_key", session_id or "default"
            )
        else:
            key = session_id or "default"
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await self._ensure_table(db)
                rows = [(key, _msg_to_row(msg)) for msg in messages]
                await db.executemany(
                    f'INSERT INTO "{self._table}" (session_id, data) VALUES (?, ?)', rows
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to save messages to SQLite for agent=%s", self._agent_id)
