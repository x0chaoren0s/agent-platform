"""
Shared Knowledge Base (Layer 3 in the four-tier memory architecture).

Uses SQLite FTS5 for full-text search.  All agents in a project can read
and write to this shared KB.  asyncio.Lock protects concurrent writes.

Schema
------
kb_entries(id, title, content, tags, author, created_at, updated_at)

Tools exposed as plain async functions (called by router / platform tools):
    kb_write(project_dir, title, content, tags, author) -> str
    kb_search(project_dir, query, limit) -> str
    kb_list(project_dir, limit) -> str
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_DB_FILE = "knowledge_base.db"

# One asyncio.Lock per db path to protect concurrent writes
_write_locks: dict[str, asyncio.Lock] = {}


def _get_lock(db_path: Path) -> asyncio.Lock:
    key = str(db_path)
    if key not in _write_locks:
        _write_locks[key] = asyncio.Lock()
    return _write_locks[key]


def _db_path(project_dir: str | Path) -> Path:
    p = Path(project_dir) / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p / _DB_FILE


async def _ensure_table(db: aiosqlite.Connection) -> None:
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL,
            content     TEXT    NOT NULL,
            tags        TEXT    DEFAULT '[]',
            author      TEXT    DEFAULT '',
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        )
        """
    )
    # FTS5 virtual table for full-text search
    await db.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts
        USING fts5(title, content, tags, content='kb_entries', content_rowid='id')
        """
    )
    # Triggers to keep FTS in sync
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS kb_ai AFTER INSERT ON kb_entries BEGIN
            INSERT INTO kb_fts(rowid, title, content, tags)
            VALUES (new.id, new.title, new.content, new.tags);
        END
        """
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS kb_au AFTER UPDATE ON kb_entries BEGIN
            INSERT INTO kb_fts(kb_fts, rowid, title, content, tags)
            VALUES ('delete', old.id, old.title, old.content, old.tags);
            INSERT INTO kb_fts(rowid, title, content, tags)
            VALUES (new.id, new.title, new.content, new.tags);
        END
        """
    )
    await db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS kb_ad AFTER DELETE ON kb_entries BEGIN
            INSERT INTO kb_fts(kb_fts, rowid, title, content, tags)
            VALUES ('delete', old.id, old.title, old.content, old.tags);
        END
        """
    )
    await db.commit()


# ------------------------------------------------------------------
# Public async tool functions
# ------------------------------------------------------------------


async def kb_write(
    project_dir: str | Path,
    title: str,
    content: str,
    tags: list[str] | None = None,
    author: str = "system",
) -> str:
    """
    Write (upsert by title) an entry to the shared knowledge base.
    Returns a confirmation string.
    """
    db_p = _db_path(project_dir)
    lock = _get_lock(db_p)
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    now = datetime.now(timezone.utc).isoformat()

    async with lock:
        async with aiosqlite.connect(db_p) as db:
            await _ensure_table(db)
            async with db.execute(
                "SELECT id FROM kb_entries WHERE title = ?", (title,)
            ) as cur:
                row = await cur.fetchone()
            if row:
                await db.execute(
                    "UPDATE kb_entries SET content=?, tags=?, author=?, updated_at=? WHERE id=?",
                    (content, tags_json, author, now, row[0]),
                )
            else:
                await db.execute(
                    "INSERT INTO kb_entries(title, content, tags, author, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                    (title, content, tags_json, author, now, now),
                )
            await db.commit()

    action = "更新" if row else "新增"
    logger.info("KB %s entry '%s' by %s", action, title, author)
    return f"已{action}知识条目「{title}」。"


async def kb_search(
    project_dir: str | Path,
    query: str,
    limit: int = 5,
) -> str:
    """
    Full-text search the knowledge base.  Returns formatted results.
    """
    db_p = _db_path(project_dir)
    if not db_p.exists():
        return "（知识库尚未建立）"
    try:
        async with aiosqlite.connect(db_p) as db:
            await _ensure_table(db)
            async with db.execute(
                """
                SELECT e.title, e.content, e.tags, e.author, e.updated_at
                FROM kb_fts
                JOIN kb_entries e ON kb_fts.rowid = e.id
                WHERE kb_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ) as cur:
                rows = await cur.fetchall()
    except Exception:
        logger.exception("KB search failed for query '%s'", query)
        return "搜索失败，请稍后重试。"

    if not rows:
        return f"未找到关于「{query}」的知识条目。"

    lines = [f"搜索「{query}」共找到 {len(rows)} 条结果：\n"]
    for i, (title, content, tags_json, author, updated_at) in enumerate(rows, 1):
        tags = json.loads(tags_json or "[]")
        tag_str = " ".join(f"#{t}" for t in tags) if tags else ""
        excerpt = content[:200] + ("..." if len(content) > 200 else "")
        lines.append(
            f"{i}. **{title}** {tag_str}\n"
            f"   {excerpt}\n"
            f"   _(by {author}, updated {updated_at[:10]})_"
        )
    return "\n".join(lines)


async def kb_list(
    project_dir: str | Path,
    limit: int = 20,
) -> str:
    """
    List recent knowledge base entries (title + tags only).
    """
    db_p = _db_path(project_dir)
    if not db_p.exists():
        return "（知识库尚未建立）"
    try:
        async with aiosqlite.connect(db_p) as db:
            await _ensure_table(db)
            async with db.execute(
                """
                SELECT title, tags, author, updated_at
                FROM kb_entries
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
    except Exception:
        logger.exception("KB list failed")
        return "列出失败，请稍后重试。"

    if not rows:
        return "（知识库为空）"

    lines = [f"知识库条目（最近 {len(rows)} 条）：\n"]
    for title, tags_json, author, updated_at in rows:
        tags = json.loads(tags_json or "[]")
        tag_str = " ".join(f"#{t}" for t in tags) if tags else ""
        lines.append(f"- **{title}** {tag_str}  _(by {author}, {updated_at[:10]})_")
    return "\n".join(lines)
