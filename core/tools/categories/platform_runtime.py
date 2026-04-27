"""
Platform tools injected into the orchestrator agent.
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import textwrap
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)
_SAFE_NAME_RE = re.compile(r"^[\w][\w\-]{0,39}$", re.UNICODE)


def _agents_dir(project_dir: Path) -> Path:
    return project_dir / "agents"


def _validate_name(name: str) -> str:
    name = name.strip().replace(" ", "_").replace("-", "_")
    if not _SAFE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid agent name '{name}'. "
            "名称需以字母/汉字开头，只能包含字母、数字、汉字、下划线，最长 40 字符。"
        )
    return name


def _clear_agent_memory(project_dir: Path, agent_name: str) -> None:
    sess_dir = project_dir / "sessions" / agent_name
    if sess_dir.exists():
        shutil.rmtree(sess_dir, ignore_errors=True)
    db_path = project_dir / "memory" / "long_term.db"
    if not db_path.exists():
        return
    table = f'history_{agent_name.replace("-", "_")}'
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to clear memory for agent '%s'", agent_name)


def list_team(project_dir: str) -> str:
    agents_path = _agents_dir(Path(project_dir))
    if not agents_path.exists():
        return "（团队目录不存在）"
    result = []
    for yaml_file in sorted(agents_path.glob("*.yaml")):
        try:
            with yaml_file.open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            result.append(
                {
                    "name": cfg.get("name", yaml_file.stem),
                    "role": cfg.get("role", "member"),
                    "description": cfg.get("description", ""),
                    "capabilities": cfg.get("capabilities", []),
                }
            )
        except Exception:
            logger.exception("Failed to read %s", yaml_file)
    if not result:
        return "（当前没有任何团队成员）"
    return yaml.dump(result, allow_unicode=True, sort_keys=False)


def recruit_fixed(
    project_dir: str,
    name: str,
    description: str,
    capabilities: list[str],
    instructions: str,
    role: str = "member",
) -> str:
    try:
        name = _validate_name(name)
    except ValueError as e:
        return f"错误：{e}"
    agents_path = _agents_dir(Path(project_dir))
    agents_path.mkdir(parents=True, exist_ok=True)
    yaml_path = agents_path / f"{name}.yaml"
    if yaml_path.exists():
        return f"错误：成员 '{name}' 已存在，如需修改请先解雇再重新招募。"
    cfg: dict[str, Any] = {
        "name": name,
        "description": description,
        "role": role,
        "capabilities": capabilities,
        "instructions": instructions,
        "max_history": 80,
    }
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
    logger.info("Recruited fixed agent '%s' → %s", name, yaml_path)
    return f"成功招募固定成员 '{name}'，YAML 已写入 {yaml_path.name}。"


def dismiss_member(project_dir: str, name: str) -> str:
    try:
        name = _validate_name(name)
    except ValueError as e:
        return f"错误：{e}"
    project_path = Path(project_dir)
    agents_path = _agents_dir(project_path)
    yaml_path = agents_path / f"{name}.yaml"
    if not yaml_path.exists():
        return f"错误：找不到成员 '{name}' 的配置文件。"
    try:
        with yaml_path.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if cfg.get("role") == "orchestrator":
            return "错误：不允许解雇 orchestrator 自身。"
    except Exception:
        pass
    yaml_path.unlink()
    _clear_agent_memory(project_path, name)
    logger.info("Dismissed agent '%s'", name)
    return f"已解雇成员 '{name}'，配置文件及历史记忆已清理。"


def recruit_temp(
    project_dir: str,
    name: str,
    description: str,
    capabilities: list[str],
    instructions: str,
    task: str,
) -> str:
    try:
        name = _validate_name(name)
    except ValueError as e:
        return f"错误：{e}"
    agents_path = _agents_dir(Path(project_dir))
    agents_path.mkdir(parents=True, exist_ok=True)
    yaml_path = agents_path / f"{name}.yaml"
    if yaml_path.exists():
        return f"复用现有临时工 '{name}'。"
    full_instructions = textwrap.dedent(f"""
        {instructions.strip()}

        【当前任务】
        {task.strip()}

        你是一名临时招募的专家，完成上述任务后即可告知任务已完成。
    """).strip()
    cfg: dict[str, Any] = {
        "name": name,
        "description": description,
        "role": "member",
        "capabilities": capabilities,
        "instructions": full_instructions,
        "is_temp": True,
        "max_history": 20,
    }
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
    logger.info("Recruited temp agent '%s' → %s", name, yaml_path)
    return f"RECRUIT_TEMP_DONE:{name}"


def update_project_context(project_dir: str, content: str) -> str:
    ctx_path = Path(project_dir) / "context.md"
    ctx_path.write_text(content.strip() + "\n", encoding="utf-8")
    logger.info("Updated project context: %s", ctx_path)
    return f"项目背景已更新（{len(content)} 字符）。"

