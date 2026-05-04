"""LLM-based conversation summarizer for context compression and memory consolidation.

Two main functions:
  - summarize_envelopes(): compress a list of Envelope dicts into a structured summary
  - consolidate_to_context(): append a conversation summary to context.md (team memory)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)
_LAST_NOTICE: str = ""


def _set_last_notice(msg: str) -> None:
    global _LAST_NOTICE
    _LAST_NOTICE = msg


def pop_last_notice() -> str:
    global _LAST_NOTICE
    msg = _LAST_NOTICE
    _LAST_NOTICE = ""
    return msg

_SUMMARIZE_SYSTEM = """\
你是一个专业的对话分析助手。请对提供的对话历史进行精炼压缩，提取关键信息。
输出格式（严格遵守）：

## 关键决策
- [决策1]
- [决策2]（若无则写"无"）

## 任务进展
- [任务/结论1]
- [任务/结论2]（若无则写"无"）

## 待办/未解决
- [待办1]（若无则写"无"）

输出必须简洁，每条不超过50字。不要添加其他内容。
"""

_CONSOLIDATE_SYSTEM = """\
你是一个专业的项目管理助手。请根据提供的对话历史，提炼出值得长期保存到项目背景文档中的关键信息。
关注：重要决策、项目方向变化、团队共识、已确定的约束条件。
输出一段简洁的 markdown 文本（不超过200字），直接作为项目背景的补充。
不要输出对话原文，只输出提炼后的结论。
"""


def _build_llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=os.environ.get("ARK_API_KEY", ""),
        base_url=os.environ.get("ARK_BASE_URL", "https://api.deepseek.com"),
    )


def _model() -> str:
    return os.environ.get("ARK_MODEL", "deepseek-v4-flash")


def _envelopes_to_text(envelopes: list[dict]) -> str:
    lines = []
    for env in envelopes:
        ts = (env.get("timestamp") or "")[:19]
        sender = env.get("sender", "?")
        to = ", ".join(env.get("to") or [])
        content = (env.get("content") or "").strip()
        if content:
            lines.append(f"[{ts}] {sender} → {to}:\n{content}")
    return "\n\n".join(lines)


async def summarize_envelopes(envelopes: list[dict]) -> str:
    """Compress a list of Envelope dicts into a structured markdown summary."""
    if not envelopes:
        return ""
    text = _envelopes_to_text(envelopes)
    if not text.strip():
        return ""
    try:
        client = _build_llm_client()
        resp = await client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _SUMMARIZE_SYSTEM},
                {"role": "user", "content": f"以下是需要压缩的对话历史：\n\n{text}"},
            ],
            temperature=0.3,
            max_tokens=600,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        err_text = str(exc).lower()
        if any(kw in err_text for kw in ("insufficient balance", "error code: 402", "balance", "429", "rate limit")):
            logger.warning("summarize_envelopes skipped: API unavailable (%s)", err_text[:100])
            _set_last_notice("摘要模型不可用，已跳过对话压缩。")
            return ""
        logger.exception("summarize_envelopes failed")
        return ""


async def consolidate_to_context(
    recent_envelopes: list[dict],
    context_path: Path,
) -> str:
    """
    Append a distilled summary of recent_envelopes to context.md.
    Returns the generated summary text, or empty string on failure.
    """
    if not recent_envelopes:
        return ""
    text = _envelopes_to_text(recent_envelopes)
    if not text.strip():
        return ""
    try:
        client = _build_llm_client()
        resp = await client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _CONSOLIDATE_SYSTEM},
                {"role": "user", "content": f"请从以下对话中提炼项目背景补充信息：\n\n{text}"},
            ],
            temperature=0.3,
            max_tokens=400,
        )
        summary = (resp.choices[0].message.content or "").strip()
        if not summary:
            return ""

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        section = f"\n\n---\n## 对话摘要 [{timestamp}]\n{summary}\n"

        context_path.parent.mkdir(parents=True, exist_ok=True)
        with open(context_path, "a", encoding="utf-8") as f:
            f.write(section)

        logger.info("Consolidated conversation summary to %s", context_path)
        return summary
    except Exception as exc:
        err_text = str(exc).lower()
        if any(kw in err_text for kw in ("insufficient balance", "error code: 402", "balance")):
            logger.warning("consolidate_to_context skipped: API unavailable (%s)", err_text[:100])
            _set_last_notice("摘要模型不可用，已跳过背景沉淀到 context.md。")
            return ""
        logger.exception("consolidate_to_context failed")
        return ""


_AUTO_NAME_SYSTEM = """\
你是一个对话命名助手。根据以下对话内容，用 6-15 个字概括对话主题。
严格只输出名称本身，禁止输出任何其他内容。不要解释、不要前缀、不要引号、不要标点。

示例输出格式：
星火万物岗位调研
漫画翻译流程优化
"""


def _pick_naming_envelopes(envelopes: list[dict]) -> list[dict]:
    """Select representative envelopes for naming: skip system/platform, keep user+agent.

    Strategy: take first 3 + last 5 meaningful envelopes so both the initial topic
    and recent context are captured, even if the conversation has grown large.
    """
    # Filter: keep only user/agent messages, drop system/platform/tool dumps
    meaningful: list[dict] = []
    for env in envelopes:
        sender = str(env.get("sender", "")).strip()
        if sender in ("", "system", "platform"):
            continue
        content = str(env.get("content", "")).strip()
        if not content:
            continue
        meaningful.append(env)

    if not meaningful:
        return []

    if len(meaningful) <= 8:
        return meaningful

    # Take first 3 + last 5 to cover the full conversation arc
    selected = meaningful[:3] + meaningful[-5:]
    # Deduplicate by id (in case first 3 and last 5 overlap)
    seen: set[str] = set()
    result: list[dict] = []
    for env in selected:
        eid = env.get("id", "")
        if eid not in seen:
            seen.add(eid)
            result.append(env)
    return result


def _envelopes_to_naming_text(envelopes: list[dict]) -> str:
    """Convert envelopes to compact naming text, truncating each message."""
    lines: list[str] = []
    for env in envelopes:
        ts = (env.get("timestamp") or "")[:19]
        sender = env.get("sender", "?")
        content = (env.get("content") or "").strip()
        if not content:
            continue
        # Cap each message to avoid tool-call dumps overwhelming the context
        if len(content) > 200:
            content = content[:200] + "…"
        lines.append(f"[{ts}] {sender}:\n{content}")
    return "\n\n".join(lines)


async def auto_name_conversation(envelopes: list[dict]) -> str | None:
    """Generate a concise Chinese name for a conversation based on its content.

    Returns the generated name string, or None on failure.
    """
    import re as _re

    if not envelopes:
        return None
    selected = _pick_naming_envelopes(envelopes)
    if not selected:
        return None
    text = _envelopes_to_naming_text(selected)
    if not text.strip():
        return None
    try:
        name = None
        for attempt in range(3):
            client = _build_llm_client()
            resp = await client.chat.completions.create(
                model=_model(),
                messages=[
                    {"role": "system", "content": _AUTO_NAME_SYSTEM},
                    {"role": "user", "content": f"对话内容：\n\n{text}"},
                ],
                temperature=0.3,
                max_tokens=100,
            )
            raw = (resp.choices[0].message.content or "").strip()
            logger.debug("auto_name attempt %d raw output: %r", attempt + 1, raw)
            if raw:
                name = raw.strip().strip('"').strip("'").strip("。").strip("，")
                # Remove common model-generated prefixes
                name = _re.sub(r'^(好的|对话名称|主题|名称|建议命名为)[：:]?\s*', '', name)
                name = name.strip()
                # Take first line if multi-line
                name = name.split("\n")[0].strip()
                if name and 2 <= len(name) <= 30:
                    break
                name = None
            logger.debug("auto_name attempt %d: empty or invalid, retrying", attempt + 1)

        if not name:
            return None
        # Truncate overly long names
        if len(name) > 15:
            name = name[:15]
        return name
    except Exception as exc:
        err_text = str(exc).lower()
        if any(kw in err_text for kw in ("insufficient balance", "error code: 402", "balance")):
            logger.warning("auto_name_conversation skipped: API unavailable (%s)", err_text[:100])
            return None
        logger.exception("auto_name_conversation failed")
        return None
