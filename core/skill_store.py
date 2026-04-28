"""Project-local skill loading for member agents.

Skill layout (MVP):
projects/<project>/skills/<skill_name>/SKILL.md
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _skills_dir(project_dir: Path) -> Path:
    return project_dir / "skills"


def _global_skill_roots() -> list[Path]:
    roots: list[Path] = []
    # Optional override: multiple paths separated by os.pathsep.
    extra = os.environ.get("AGENT_PLATFORM_SKILL_ROOTS", "").strip()
    if extra:
        for item in extra.split(os.pathsep):
            p = Path(item).expanduser()
            if p.exists() and p.is_dir():
                roots.append(p)
    home = Path.home()
    for p in [home / ".claude" / "skills", home / ".cursor" / "skills"]:
        if p.exists() and p.is_dir():
            roots.append(p)
    # Stable de-dup preserving order.
    out: list[Path] = []
    seen: set[str] = set()
    for p in roots:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _skill_file_candidates(project_dir: Path, skill_name: str) -> list[Path]:
    local = _skills_dir(project_dir) / skill_name / "SKILL.md"
    candidates = [local]
    for root in _global_skill_roots():
        candidates.append(root / skill_name / "SKILL.md")
    return candidates


def _parse_skill_md(path: Path) -> tuple[dict[str, Any], str] | None:
    """Parse SKILL.md frontmatter and body.

    Expected format:
    ---
    name: xxx
    description: xxx
    ---
    <body markdown>
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Failed reading skill file: %s", path)
        return None

    # Be tolerant of UTF-8 BOM (common on Windows/PowerShell).
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    _, frontmatter_text, body = parts
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except Exception:
        logger.exception("Failed parsing frontmatter: %s", path)
        return None
    if not isinstance(frontmatter, dict):
        return None
    return frontmatter, body.strip()


def read_skill(project_dir: str | Path, skill_name: str) -> tuple[dict[str, Any], str] | None:
    pdir = Path(project_dir)
    for skill_file in _skill_file_candidates(pdir, skill_name):
        if skill_file.exists():
            return _parse_skill_md(skill_file)
    return None


def read_agent_skills(project_dir: str | Path, agent_name: str) -> list[str]:
    pdir = Path(project_dir)
    yaml_path = pdir / "agents" / f"{agent_name}.yaml"
    if not yaml_path.exists():
        return []
    try:
        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("Failed reading agent yaml: %s", yaml_path)
        return []
    raw = cfg.get("skills", [])
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def build_skill_index(project_dir: str | Path, agent_skills: list[str]) -> str:
    if not agent_skills:
        return ""
    pdir = Path(project_dir)
    lines = [
        "【可用 Skills】",
        "你被挂载了以下 Skill。当任务匹配 description 时，先调 load_skill(name) 加载完整 SOP 再执行。",
        "",
    ]
    added = 0
    for skill_name in agent_skills:
        parsed = read_skill(pdir, skill_name)
        if parsed is None:
            logger.warning("Skill not found or invalid: %s", skill_name)
            continue
        frontmatter, _ = parsed
        name = str(frontmatter.get("name", "")).strip()
        desc = str(frontmatter.get("description", "")).strip()
        if not name or not desc:
            logger.warning("Skill missing name/description: %s", skill_name)
            continue
        lines.append(f"- name: {name}")
        lines.append(f"  description: {desc}")
        added += 1
    if added == 0:
        return ""
    return "\n".join(lines)


def load_for_agent(project_dir: str | Path, agent_name: str, skill_name: str) -> str:
    allowed = read_agent_skills(project_dir, agent_name)
    if skill_name not in allowed:
        allowed_text = ", ".join(allowed) if allowed else "无"
        return f"错误：未给 {agent_name} 挂载 skill: {skill_name}。已挂载: {allowed_text}"

    pdir = Path(project_dir)
    parsed = read_skill(pdir, skill_name)
    if parsed is None:
        roots = [str(_skills_dir(pdir))] + [str(p) for p in _global_skill_roots()]
        return (
            f"错误：未找到可解析的 SKILL.md：{skill_name}。\n"
            f"已搜索目录：{', '.join(roots)}"
        )
    _, body = parsed
    return body
