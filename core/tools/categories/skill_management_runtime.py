"""Runtime implementations for skill management tools."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from core.skill_proposals import SkillProposal, SkillProposalStore
from core import skill_store

logger = logging.getLogger(__name__)

_SKILL_PROPOSAL_STORES: dict[str, SkillProposalStore] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_skill_proposal_store(project_dir: str) -> SkillProposalStore:
    pdir = Path(project_dir)
    key = str(pdir.resolve())
    store = _SKILL_PROPOSAL_STORES.get(key)
    if store is None:
        store = SkillProposalStore(
            db_path=pdir / "memory" / "tasks.db",
            project=pdir.name,
        )
        await store.init_db()
        _SKILL_PROPOSAL_STORES[key] = store
    return store


def _validate_skill_content(content: str) -> tuple[dict[str, Any] | None, str | None]:
    """Validate SKILL.md content string has valid YAML frontmatter.

    Returns (frontmatter, None) on success or (None, error_message) on failure.
    """
    text = content.lstrip("﻿")
    if not text.startswith("---"):
        return None, "内容必须以 --- 开头的 YAML frontmatter 开头。"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, "未找到完整的 YAML frontmatter（缺少 closing ---）。"
    _, fm_text, body = parts
    if not body.strip():
        return None, "frontmatter 之后缺少正文内容。"
    try:
        fm = yaml.safe_load(fm_text) or {}
    except Exception as exc:
        return None, f"YAML frontmatter 解析失败：{exc}"
    if not isinstance(fm, dict):
        return None, "frontmatter 解析结果不是字典。"
    if not str(fm.get("name", "")).strip():
        return None, "frontmatter 缺少 name 字段。"
    if not str(fm.get("description", "")).strip():
        return None, "frontmatter 缺少 description 字段。"
    return fm, None


def _validate_bin_dependencies(frontmatter: dict[str, Any]) -> list[str]:
    """Check metadata.requires.bins; return list of missing executables."""
    missing: list[str] = []
    meta = frontmatter.get("metadata")
    if not isinstance(meta, dict):
        return missing
    requires = meta.get("requires")
    if not isinstance(requires, dict):
        return missing
    bins = requires.get("bins")
    if not isinstance(bins, list):
        return missing
    for bin_name in bins:
        name = str(bin_name).strip()
        if name and not shutil.which(name):
            missing.append(name)
    return missing


def _skill_target_dir(project_dir: Path, skill_name: str, scope: str) -> Path:
    if scope == "system":
        return skill_store._system_skills_dir() / skill_name
    return skill_store._skills_dir(project_dir) / skill_name


def _skill_backup_dir(project_dir: Path, skill_name: str, scope: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if scope == "system":
        archive = skill_store._system_skills_dir().parent / ".skill_archive"
    else:
        archive = project_dir / ".skill_archive"
    return archive / skill_name / ts


# ---------------------------------------------------------------------------
# Public API: list_skills
# ---------------------------------------------------------------------------


async def list_skills(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
) -> str:
    """List all available skills (system-level + project-level + extra roots)."""
    _ = thread_id, caller_agent
    pdir = Path(project_dir)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    # Project-level skills (highest priority)
    proj_dir = skill_store._skills_dir(pdir)
    if proj_dir.exists():
        for skill_file in sorted(proj_dir.glob("*/SKILL.md")):
            sid = skill_file.parent.name
            parsed = skill_store._parse_skill_md(skill_file)
            if parsed:
                fm, _ = parsed
                rows.append({
                    "id": sid,
                    "name": str(fm.get("name", "")) or sid,
                    "description": str(fm.get("description", "")),
                    "scope": "project",
                })
            else:
                rows.append({"id": sid, "name": sid, "description": "", "scope": "project"})
            seen.add(sid)

    # System-level skills
    sys_dir = skill_store._system_skills_dir()
    if sys_dir.exists():
        for skill_file in sorted(sys_dir.glob("*/SKILL.md")):
            sid = skill_file.parent.name
            if sid in seen:
                continue
            parsed = skill_store._parse_skill_md(skill_file)
            if parsed:
                fm, _ = parsed
                rows.append({
                    "id": sid,
                    "name": str(fm.get("name", "")) or sid,
                    "description": str(fm.get("description", "")),
                    "scope": "system",
                })
            else:
                rows.append({"id": sid, "name": sid, "description": "", "scope": "system"})
            seen.add(sid)

    # Extra global roots
    for root in skill_store._global_skill_roots():
        if not root.exists():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            sid = skill_file.parent.name
            if sid in seen:
                continue
            parsed = skill_store._parse_skill_md(skill_file)
            if parsed:
                fm, _ = parsed
                rows.append({
                    "id": sid,
                    "name": str(fm.get("name", "")) or sid,
                    "description": str(fm.get("description", "")),
                    "scope": str(root),
                })
            else:
                rows.append({"id": sid, "name": sid, "description": "", "scope": str(root)})
            seen.add(sid)

    if not rows:
        return "（当前没有可用 Skill）"
    return yaml.dump(rows, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# Public API: propose_skill
# ---------------------------------------------------------------------------


async def propose_skill(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    skill_name: str,
    description: str,
    content: str,
    extra_files: dict[str, str] | None = None,
    mount_to: list[str] | None = None,
    rationale: str | None = None,
    scope: str = "project",
) -> str:
    """Submit a skill proposal. Validates frontmatter, stores in DB."""
    name = (skill_name or "").strip()
    if not name:
        return "错误：skill_name 不能为空。"
    if not name.replace("-", "").replace("_", "").isalnum():
        return "错误：skill_name 只能包含字母、数字、下划线和连字符。"

    desc = (description or "").strip()
    if not desc:
        return "错误：description 不能为空。"

    # Validate content frontmatter
    fm, err = _validate_skill_content(content)
    if err:
        return f"错误：内容校验失败——{err}"

    # Check if skill already exists in the target scope
    pdir = Path(project_dir)
    target = _skill_target_dir(pdir, name, scope)
    if target.exists():
        return f"错误：目标路径已存在：{target}（skill '{name}' 已在 {scope} 级别存在）。"

    store = await _get_skill_proposal_store(project_dir)
    proposal = await store.create(
        SkillProposal(
            id="",
            project=pdir.name,
            proposer=caller_agent,
            thread_id=thread_id,
            skill_name=name,
            description=desc,
            content=content,
            extra_files=extra_files,
            mount_to=mount_to,
            rationale=rationale,
            scope=scope,
        )
    )

    logger.info(
        "Skill proposal created: %s (skill=%s, proposer=%s, scope=%s)",
        proposal.id, name, caller_agent, scope,
    )
    return (
        f"已提交 skill 提案 {proposal.id}（技能：{name}，范围：{scope}）\n\n"
        f"草案摘要：{desc}\n"
        f"请通知 orchestrator 审阅此提案。"
    )


# ---------------------------------------------------------------------------
# Public API: list_proposals
# ---------------------------------------------------------------------------


async def list_proposals(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    status: str | None = None,
) -> str:
    """List skill proposals, optionally filtered by status."""
    _ = thread_id, caller_agent
    store = await _get_skill_proposal_store(project_dir)
    proposals = await store.list_by_status(status)
    if not proposals:
        return "（当前没有 skill 提案）"
    rows = []
    for p in proposals:
        row = {
            "id": p.id,
            "skill_name": p.skill_name,
            "description": p.description[:120] + ("..." if len(p.description) > 120 else ""),
            "scope": p.scope,
            "status": p.status,
            "proposer": p.proposer,
            "created_at": p.created_at,
        }
        if p.mount_to:
            row["mount_to"] = p.mount_to
        if p.rationale:
            row["rationale"] = p.rationale[:200]
        if p.orch_feedback:
            row["orch_feedback"] = p.orch_feedback
        rows.append(row)
    return yaml.dump(rows, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# Public API: create_skill (RED operation - requires user confirm)
# ---------------------------------------------------------------------------


async def create_skill(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    proposal_id: str | None = None,
    skill_name: str = "",
    description: str = "",
    content: str = "",
    extra_files: dict[str, str] | None = None,
    mount_to: list[str] | None = None,
    scope: str = "project",
) -> str:
    """Create a new skill from a proposal or direct parameters.

    This is a RED operation — user confirmation is verified by the caller.
    """
    _ = caller_agent
    name = (skill_name or "").strip()

    # Merge proposal data if provided
    desc = description
    if proposal_id:
        store = await _get_skill_proposal_store(project_dir)
        proposal = await store.get(proposal_id)
        if proposal is None:
            return f"错误：提案 {proposal_id} 不存在。"
        if not name:
            name = proposal.skill_name
        desc = description or proposal.description
        content = content or proposal.content
        extra_files = extra_files or proposal.extra_files
        mount_to = mount_to or proposal.mount_to
        scope = scope or proposal.scope
    if not proposal_id and not name:
        return "错误：必须提供 skill_name 或 proposal_id。"

    desc = (desc or "").strip()
    if not desc:
        return "错误：description 不能为空。"

    # Validate content frontmatter
    fm, err = _validate_skill_content(content)
    if err:
        return f"错误：内容校验失败——{err}"

    # Check binary dependencies
    missing_bins = _validate_bin_dependencies(fm)
    warnings: list[str] = []
    if missing_bins:
        warnings.append(
            f"⚠️ 以下依赖工具在当前环境未找到：{', '.join(missing_bins)}。"
            f"请确保它们已安装且在 PATH 中，否则使用该技能的 agent 可能无法正常执行。"
        )

    # Determine target path
    pdir = Path(project_dir)
    tdir = _skill_target_dir(pdir, name, scope)
    if tdir.exists():
        return f"错误：目标路径已存在：{tdir}（skill '{name}' 已在 {scope} 级别存在）。"

    # Write SKILL.md
    tdir.mkdir(parents=True, exist_ok=True)
    skill_path = tdir / "SKILL.md"
    skill_path.write_text(content.strip() + "\n", encoding="utf-8")
    logger.info("Created skill SKILL.md: %s", skill_path)

    # Write extra files
    if extra_files:
        for rel_path, file_content in extra_files.items():
            clean_rel = rel_path.strip().replace("\\", "/")
            fp = (tdir / clean_rel).resolve()
            try:
                fp.relative_to(tdir.resolve())
            except ValueError:
                warnings.append(f"⚠️ 跳过越界额外文件：{rel_path}")
                continue
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(file_content.strip() + "\n", encoding="utf-8")
            logger.info("Created skill extra file: %s", fp)

    # Mount to agents
    mounted: list[str] = []
    mount_errors: list[str] = []
    if mount_to:
        agents_dir = pdir / "agents"
        for agent_name in mount_to:
            agent_name = agent_name.strip()
            if not agent_name:
                continue
            yaml_path = agents_dir / f"{agent_name}.yaml"
            if not yaml_path.exists():
                mount_errors.append(f"⚠️ agent '{agent_name}' 不存在，跳过挂载。")
                continue
            try:
                cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                if not isinstance(cfg, dict):
                    mount_errors.append(f"⚠️ agent '{agent_name}' 的 YAML 格式异常，跳过。")
                    continue
                existing = cfg.get("skills", [])
                if not isinstance(existing, list):
                    existing = []
                if name not in existing:
                    existing = [str(s).strip() for s in existing if str(s).strip()]
                    existing.append(name)
                    cfg["skills"] = sorted(existing)
                    with yaml_path.open("w", encoding="utf-8") as f:
                        yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
                    mounted.append(agent_name)
                    logger.info("Mounted skill '%s' to agent '%s'", name, agent_name)
            except Exception as exc:
                mount_errors.append(f"⚠️ 挂载到 agent '{agent_name}' 失败：{exc}")

    # Update proposal status if applicable
    if proposal_id:
        store = await _get_skill_proposal_store(project_dir)
        await store.update_status(proposal_id, "approved")

    # Build result message
    lines = [
        f"✅ Skill「{name}」已创建成功（{scope} 级别）",
        f"   路径：{skill_path}",
    ]
    if mounted:
        lines.append(f"   已挂载到：{', '.join(mounted)}")
    if mount_errors:
        lines.extend(mount_errors)
    if warnings:
        lines.extend(warnings)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API: update_skill (RED operation - requires user confirm)
# ---------------------------------------------------------------------------


async def update_skill(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    skill_name: str,
    description: str,
    content: str,
    extra_files: dict[str, str] | None = None,
) -> str:
    """Update an existing skill. Auto-backups old version before overwriting.

    This is a RED operation — user confirmation is verified by the caller.
    """
    _ = thread_id, caller_agent
    name = (skill_name or "").strip()
    if not name:
        return "错误：skill_name 不能为空。"

    desc = (description or "").strip()
    if not desc:
        return "错误：description 不能为空。"

    # Validate content frontmatter
    fm, err = _validate_skill_content(content)
    if err:
        return f"错误：内容校验失败——{err}"

    # Check binary dependencies
    missing_bins = _validate_bin_dependencies(fm)
    warnings: list[str] = []
    if missing_bins:
        warnings.append(
            f"⚠️ 以下依赖工具在当前环境未找到：{', '.join(missing_bins)}。"
            f"请确保它们已安装且在 PATH 中。"
        )

    # Locate existing skill: project overrides system
    pdir = Path(project_dir)
    existing_paths = [
        ("project", skill_store._skills_dir(pdir) / name),
        ("system", skill_store._system_skills_dir() / name),
    ]
    found_scope = None
    existing_dir: Path | None = None
    for scope_label, sp in existing_paths:
        if sp.exists() and (sp / "SKILL.md").exists():
            found_scope = scope_label
            existing_dir = sp
            break

    if existing_dir is None:
        return f"错误：skill '{name}' 不存在，无法更新。请先使用 create_skill 创建。"

    # Backup old version
    backup_dir = _skill_backup_dir(pdir, name, found_scope or "project")
    try:
        shutil.copytree(str(existing_dir), str(backup_dir), dirs_exist_ok=False)
        logger.info("Backed up skill '%s' → %s", name, backup_dir)
    except Exception as exc:
        logger.exception("Failed to backup skill '%s'", name)
        return f"错误：备份旧版失败：{exc}"

    # Write new SKILL.md
    skill_path = existing_dir / "SKILL.md"
    existing_dir.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(content.strip() + "\n", encoding="utf-8")
    logger.info("Updated skill SKILL.md: %s", skill_path)

    # Write extra files
    if extra_files:
        for rel_path, file_content in extra_files.items():
            clean_rel = rel_path.strip().replace("\\", "/")
            fp = (existing_dir / clean_rel).resolve()
            try:
                fp.relative_to(existing_dir.resolve())
            except ValueError:
                warnings.append(f"⚠️ 跳过越界额外文件：{rel_path}")
                continue
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(file_content.strip() + "\n", encoding="utf-8")
            logger.info("Updated skill extra file: %s", fp)

    lines = [
        f"✅ Skill「{name}」已更新（{found_scope} 级别）",
        f"   路径：{skill_path}",
        f"   旧版备份至：{backup_dir}",
    ]
    if warnings:
        lines.extend(warnings)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatch table (for direct import by tool classes)
# ---------------------------------------------------------------------------

SKILL_TOOL_DISPATCH: dict[str, Any] = {
    "list_skills": list_skills,
    "propose_skill": propose_skill,
    "list_proposals": list_proposals,
    "create_skill": create_skill,
    "update_skill": update_skill,
}
