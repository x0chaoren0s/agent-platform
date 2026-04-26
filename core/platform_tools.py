"""
Platform tools injected into the orchestrator agent.

These tools let the orchestrator manage the team by calling Python functions
that create/delete YAML files, update context.md, etc.

All tools return plain text (the LLM reads the result as tool output).
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

# Allow Unicode word characters (CJK, letters, digits, underscore) and hyphens
_SAFE_NAME_RE = re.compile(r"^[\w][\w\-]{0,39}$", re.UNICODE)


def _agents_dir(project_dir: Path) -> Path:
    return project_dir / "agents"


def _validate_name(name: str) -> str:
    """Normalise and validate an agent name. Returns clean name or raises ValueError."""
    name = name.strip().replace(" ", "_").replace("-", "_")
    if not _SAFE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid agent name '{name}'. "
            "名称需以字母/汉字开头，只能包含字母、数字、汉字、下划线，最长 40 字符。"
        )
    return name


def _clear_agent_memory(project_dir: Path, agent_name: str) -> None:
    """
    Remove persisted memory for one agent so same-name rehire starts clean.
    """
    # 1) Remove short-term sessions: sessions/{agent_name}/*.json
    sess_dir = project_dir / "sessions" / agent_name
    if sess_dir.exists():
        shutil.rmtree(sess_dir, ignore_errors=True)

    # 2) Remove long-term history table: memory/long_term.db::history_{agent_name}
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


# ------------------------------------------------------------------
# Tool functions (called by the orchestrator via function-call)
# ------------------------------------------------------------------


def list_team(project_dir: str) -> str:
    """
    List all fixed agents in the current project.

    Returns a YAML-formatted table of agent names, roles, and capabilities.
    """
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
    """
    Create a new fixed team member by writing a YAML file.

    Parameters
    ----------
    project_dir : str
        Absolute path to the project directory.
    name : str
        Agent name (snake_case, a-z0-9_).
    description : str
        Short description shown in the UI.
    capabilities : list[str]
        List of capability tags (snake_case).
    instructions : str
        Full system prompt / instructions for the agent.
    role : str
        "member" or "orchestrator".
    """
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
    """
    Remove a fixed team member by deleting its YAML file.

    Parameters
    ----------
    project_dir : str
        Absolute path to the project directory.
    name : str
        Agent name to remove.
    """
    try:
        name = _validate_name(name)
    except ValueError as e:
        return f"错误：{e}"

    project_path = Path(project_dir)
    agents_path = _agents_dir(project_path)
    yaml_path = agents_path / f"{name}.yaml"
    if not yaml_path.exists():
        return f"错误：找不到成员 '{name}' 的配置文件。"

    # Safety guard — do not allow dismissing the orchestrator itself
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
    """
    Create a temporary agent YAML (marked is_temp: true).

    The caller (router) is responsible for registering it and cleaning up after use.
    Returns the agent name so the router can route the task to it.
    """
    try:
        name = _validate_name(name)
    except ValueError as e:
        return f"错误：{e}"

    agents_path = _agents_dir(Path(project_dir))
    agents_path.mkdir(parents=True, exist_ok=True)
    yaml_path = agents_path / f"{name}.yaml"

    # If a temp agent with same name exists, reuse it
    if yaml_path.exists():
        return f"复用现有临时工 '{name}'。"

    # Append the specific task to instructions
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
    """
    Overwrite or create the project's context.md file.

    context.md is injected into every agent's system prompt by
    ProjectContextProvider, providing shared project background.
    """
    ctx_path = Path(project_dir) / "context.md"
    ctx_path.write_text(content.strip() + "\n", encoding="utf-8")
    logger.info("Updated project context: %s", ctx_path)
    return f"项目背景已更新（{len(content)} 字符）。"


# ------------------------------------------------------------------
# Tool registry for easy injection into MAF agents
# ------------------------------------------------------------------

PLATFORM_TOOL_FUNCTIONS = [
    list_team,
    recruit_fixed,
    dismiss_member,
    recruit_temp,
    update_project_context,
]
