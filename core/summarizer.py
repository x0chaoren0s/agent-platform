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
        base_url=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
    )


def _model() -> str:
    return os.environ.get("ARK_MODEL", "doubao-seed-2-0-pro-260215")


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
    except Exception:
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
    except Exception:
        logger.exception("consolidate_to_context failed")
        return ""
