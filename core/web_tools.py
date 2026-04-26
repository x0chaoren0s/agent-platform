"""Web research tools backed by Firecrawl.

Two atomic tools exposed to all member agents:
- web_search(query, limit?) -> markdown list of {title, url, snippet}
- web_read(url) -> page content as markdown

Side effect: every URL touched is recorded in a per-(thread_id, agent_name)
in-memory bucket; submit_deliverable later drains and appends them as a
References section in the deliverable file.
"""

from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

# Process-wide URL history per (thread_id, agent_name).
# Lost on restart — that's acceptable for MVP.
_URL_HISTORY: dict[tuple[str, str], list[dict[str, str]]] = {}
_URL_HISTORY_LOCK = Lock()
_MAX_HISTORY_PER_AGENT = 50

_SEARCH_LIMIT_DEFAULT = 5
_SEARCH_LIMIT_MAX = 10
_SEARCH_SNIPPET_MAX = 200
_READ_TEXT_MAX = 8000


def _get_app() -> Any | None:
    """Lazy-load Firecrawl client. Returns None if API key missing or SDK not installed."""
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from firecrawl import FirecrawlApp
        return FirecrawlApp(api_key=api_key)
    except Exception:
        logger.exception("Failed to initialize Firecrawl client")
        return None


def _record_url(thread_id: str, agent_name: str, url: str, title: str = "") -> None:
    if not thread_id or not agent_name or not url:
        return
    key = (thread_id, agent_name)
    with _URL_HISTORY_LOCK:
        bucket = _URL_HISTORY.setdefault(key, [])
        if any(item["url"] == url for item in bucket):
            return
        bucket.append({"url": url, "title": title or ""})
        if len(bucket) > _MAX_HISTORY_PER_AGENT:
            del bucket[: len(bucket) - _MAX_HISTORY_PER_AGENT]


def consume_url_history(thread_id: str, agent_name: str) -> list[dict[str, str]]:
    """Pop and return all URLs accumulated by an agent in this thread."""
    key = (thread_id, agent_name)
    with _URL_HISTORY_LOCK:
        return _URL_HISTORY.pop(key, [])


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"…（已截断，原长度 {len(text)} 字符）"


async def web_search(
    *,
    thread_id: str,
    caller_agent: str,
    query: str,
    limit: int | None = None,
) -> str:
    """Search the web via Firecrawl. Returns markdown-formatted result list."""
    if not query or not str(query).strip():
        return "错误：web_search 必须提供 query。"
    app = _get_app()
    if app is None:
        return "错误：FIRECRAWL_API_KEY 未配置或 firecrawl-py 包未安装。请联系平台管理员。"
    n = max(1, min(int(limit or _SEARCH_LIMIT_DEFAULT), _SEARCH_LIMIT_MAX))
    try:
        result = app.search(query=str(query), limit=n)
    except Exception as exc:
        logger.exception("web_search failed: %s", exc)
        return f"错误：web_search 调用失败：{exc}"

    items: list[dict] = []
    if isinstance(result, dict):
        items = result.get("data") or result.get("web") or []
    elif isinstance(result, list):
        items = result
    if not items:
        return f"web_search('{query}') 无结果。"

    lines = [f"web_search('{query}') 共 {len(items)} 条结果："]
    for i, item in enumerate(items, 1):
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("description") or item.get("snippet") or "").strip()
        snippet = _truncate(snippet, _SEARCH_SNIPPET_MAX)
        lines.append(f"\n{i}. [{title or url}]({url})\n   {snippet}")
        _record_url(thread_id, caller_agent, url, title)
    return "\n".join(lines)


async def web_read(
    *,
    thread_id: str,
    caller_agent: str,
    url: str,
) -> str:
    """Fetch page content as markdown via Firecrawl scrape_url."""
    if not url or not str(url).strip():
        return "错误：web_read 必须提供 url。"
    app = _get_app()
    if app is None:
        return "错误：FIRECRAWL_API_KEY 未配置或 firecrawl-py 包未安装。"
    try:
        result = app.scrape_url(url=str(url), params={"formats": ["markdown"]})
    except Exception as exc:
        logger.exception("web_read failed: %s", exc)
        return f"错误：web_read 调用失败：{exc}"

    markdown = ""
    title = ""
    if isinstance(result, dict):
        data = result.get("data") if "data" in result else result
        if isinstance(data, dict):
            markdown = str(data.get("markdown") or data.get("content") or "")
            meta = data.get("metadata") or {}
            if isinstance(meta, dict):
                title = str(meta.get("title") or meta.get("og:title") or "")
    if not markdown:
        return f"web_read('{url}') 拿到空内容。"
    _record_url(thread_id, caller_agent, str(url), title)
    return _truncate(markdown, _READ_TEXT_MAX)
