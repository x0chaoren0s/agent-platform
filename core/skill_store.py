"""Project-local skill loading for member agents.

Skill layout (MVP):
projects/<project>/skills/<skill_name>/SKILL.md
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _skills_dir(project_dir: Path) -> Path:
    return project_dir / "skills"


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
    skill_file = _skills_dir(pdir) / skill_name / "SKILL.md"
    if not skill_file.exists():
        return None
    return _parse_skill_md(skill_file)


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
    skill_dir = _skills_dir(pdir)
    if not skill_dir.exists():
        return f"错误：skill 目录不存在: {skill_dir}"
    skill_file = skill_dir / skill_name / "SKILL.md"
    if not skill_file.exists():
        return f"错误：SKILL.md 不存在于 {skill_name} 目录"
    parsed = _parse_skill_md(skill_file)
    if parsed is None:
        return f"错误：SKILL.md frontmatter 解析失败: {skill_name}"
    _, body = parsed
    return body
