"""Workspace file tools: list, read, write, grep, glob (paths confined to project_dir)."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Awaitable, Callable

from core.red_actions import check_confirm

from . import team_runtime as team_rt

_LIST_FILES_MAX_ROWS = 200
_READ_FILE_MAX_BYTES = 262_144  # 256 KiB
_WRITE_FILE_MAX_BYTES = 2_000_000
_GREP_MAX_MATCHES_DEFAULT = 80
_GREP_MAX_MATCHES_CAP = 200
_GREP_MAX_FILES_SCANNED = 400
_GREP_PER_FILE_MAX_BYTES = 400_000
_GLOB_MAX_RESULTS = 300
_SKIP_DIR_NAMES = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", ".mypy_cache"}
)
_WRITE_DENY_TOP = frozenset({"memory", "sessions", "chat_log"})


def _safe_workspace_path(project_dir: str, raw_path: str | None) -> Path:
    base = Path(project_dir).resolve()
    rel = (raw_path or ".").strip() or "."
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except Exception:
        raise ValueError(f"路径越界：{rel}") from None
    return target


def _resolve_skill_fallback(raw_path: str) -> Path | None:
    """Try to resolve a path against system/global skill roots.

    Handles paths like:
      skills/lark-doc/references/xxx.md  →  look in agent-platform/skills/lark-doc/references/xxx.md
      ../lark-shared/SKILL.md            →  look in agent-platform/skills/lark-shared/SKILL.md
    """
    from core.skill_store import _system_skills_dir, _global_skill_roots

    rel = raw_path.strip().replace("\\", "/")
    # Strip skills/ or ../ prefix to get skill-relative path
    if rel.startswith("skills/"):
        sub = rel[len("skills/"):]
    elif rel.startswith("../"):
        sub = rel[3:]
    else:
        return None

    sys_dir = _system_skills_dir()
    for root in [sys_dir] + _global_skill_roots():
        if not root.exists():
            continue
        candidate = (root / sub).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _first_segment(rel: str) -> str:
    r = rel.strip().replace("\\", "/").strip("/")
    if not r:
        return ""
    return r.split("/", 1)[0]


def _in_workspace(rel: str) -> bool:
    r = rel.strip().replace("\\", "/")
    return r == "workspace" or r.startswith("workspace/")


async def list_files(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    path: str = ".",
    max_depth: int = 2,
    include_hidden: bool = False,
) -> str:
    _ = thread_id, caller_agent
    try:
        root = _safe_workspace_path(project_dir, path)
    except ValueError as exc:
        return f"错误：{exc}"
    if not root.exists():
        return f"错误：路径不存在：{path}"
    if not root.is_dir():
        return f"错误：不是目录：{path}"
    try:
        depth_limit = max(0, min(int(max_depth), 8))
    except Exception:
        depth_limit = 2
    base = Path(project_dir).resolve()
    rows: list[str] = []

    def _is_hidden(name: str) -> bool:
        return name.startswith(".")

    def _walk(cur: Path, depth: int) -> None:
        if len(rows) >= _LIST_FILES_MAX_ROWS:
            return
        try:
            entries = sorted(cur.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if not include_hidden and _is_hidden(entry.name):
                continue
            try:
                rel = entry.resolve().relative_to(base)
            except Exception:
                continue
            label = str(rel).replace(os.sep, "/")
            if entry.is_dir():
                rows.append(f"[D] {label}/")
                if depth < depth_limit:
                    _walk(entry, depth + 1)
            else:
                rows.append(f"[F] {label}")
            if len(rows) >= _LIST_FILES_MAX_ROWS:
                return

    _walk(root, 0)
    if not rows:
        return "（目录为空）"
    body = "\n".join(rows)
    if len(rows) >= _LIST_FILES_MAX_ROWS:
        body += f"\n...（仅展示前 {_LIST_FILES_MAX_ROWS} 条）"
    return body


async def read_file(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    _ = thread_id, caller_agent
    if not path or not str(path).strip():
        return "错误：read_file 必须提供 path。"
    target = None
    try:
        target = _safe_workspace_path(project_dir, path)
    except ValueError:
        pass  # Fall through to skill fallback
    if target is None or not target.exists():
        alt = _resolve_skill_fallback(path)
        if alt is not None:
            target = alt
    if target is None:
        return f"错误：路径越界：{path}"
    if not target.exists():
        return f"错误：文件不存在：{path}"
    if not target.is_file():
        return f"错误：不是普通文件：{path}"
    try:
        raw = target.read_bytes()
    except OSError as exc:
        return f"错误：无法读取文件：{exc}"
    truncated = len(raw) > _READ_FILE_MAX_BYTES
    if truncated:
        raw = raw[:_READ_FILE_MAX_BYTES]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    start = 1
    end = len(lines)
    if offset is not None:
        try:
            start = max(1, int(offset))
        except Exception:
            start = 1
    if limit is not None:
        try:
            lim = max(1, int(limit))
        except Exception:
            lim = len(lines)
        end = min(len(lines), start + lim - 1)
    else:
        end = len(lines)
    if lines and start > len(lines):
        return f"错误：offset={start} 超出文件行数 {len(lines)}。"
    out_lines: list[str] = []
    for i in range(start, end + 1):
        if 1 <= i <= len(lines):
            out_lines.append(f"{i}|{lines[i - 1]}")
    body = "\n".join(out_lines) if out_lines else "（所选范围内无行）"
    if truncated:
        body += f"\n\n…（已按字节截断至前 {_READ_FILE_MAX_BYTES} 字节）"
    return body


async def write_file(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    path: str,
    content: str,
) -> str:
    _ = caller_agent
    if not path or not str(path).strip():
        return "错误：write_file 必须提供 path。"
    if content is None:
        return "错误：write_file 必须提供 content。"
    data = str(content).encode("utf-8")
    if len(data) > _WRITE_FILE_MAX_BYTES:
        return f"错误：content 过长（{_WRITE_FILE_MAX_BYTES} 字节上限），请缩小或分段写入。"
    try:
        target = _safe_workspace_path(project_dir, path)
    except ValueError as exc:
        return f"错误：{exc}"
    base = Path(project_dir).resolve()
    try:
        rel_slash = str(target.resolve().relative_to(base)).replace(os.sep, "/")
    except Exception:
        return "错误：无法解析目标相对路径。"

    if _first_segment(rel_slash) in _WRITE_DENY_TOP:
        seg = _first_segment(rel_slash)
        return f"错误：禁止写入受保护区域（{seg}/），请使用 workspace/ 或专用流程。"

    if not _in_workspace(rel_slash):
        qstore = await team_rt._get_question_store(project_dir)
        allowed, reason = await check_confirm(
            qstore,
            thread_id=thread_id,
            tool_name="write_file",
            args={"path": rel_slash, "content": content},
        )
        if not allowed:
            return reason

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        return f"错误：写入失败：{exc}"
    return f"已写入 {len(data)} 字节 → {rel_slash}"


async def grep(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    pattern: str,
    path: str = ".",
    glob: str = "**/*",
    case_insensitive: bool = False,
    max_matches: int | None = None,
) -> str:
    _ = thread_id, caller_agent
    pat = (pattern or "").strip()
    if not pat:
        return "错误：grep 必须提供 pattern（Python 正则语法）。"
    try:
        lim = max(1, min(int(max_matches or _GREP_MAX_MATCHES_DEFAULT), _GREP_MAX_MATCHES_CAP))
    except Exception:
        lim = _GREP_MAX_MATCHES_DEFAULT
    flags = re.MULTILINE
    if case_insensitive:
        flags |= re.IGNORECASE
    try:
        rx = re.compile(pat, flags)
    except re.error as exc:
        return f"错误：正则无效：{exc}"
    try:
        root = _safe_workspace_path(project_dir, path)
    except ValueError as exc:
        return f"错误：{exc}"
    if not root.exists():
        return f"错误：路径不存在：{path}"
    if not root.is_dir():
        return f"错误：grep 的 path 必须是目录：{path}"

    base = Path(project_dir).resolve()
    glob_pat = (glob or "**/*").strip() or "**/*"
    matches: list[str] = []
    files_seen = 0

    def rel_pos(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(base)).replace(os.sep, "/")
        except Exception:
            return str(p)

    def file_matches(rel_slash: str, fname: str) -> bool:
        return fnmatch.fnmatch(rel_slash, glob_pat) or fnmatch.fnmatch(fname, glob_pat)

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dp = Path(dirpath)
        dirnames[:] = [d for d in sorted(dirnames) if d not in _SKIP_DIR_NAMES and not d.startswith(".")]
        for name in sorted(filenames):
            if len(matches) >= lim:
                break
            if name.startswith("."):
                continue
            fp = dp / name
            if files_seen >= _GREP_MAX_FILES_SCANNED:
                break
            try:
                rel_slash = str(fp.resolve().relative_to(base)).replace(os.sep, "/")
            except Exception:
                continue
            if not file_matches(rel_slash, name):
                continue
            if not fp.is_file():
                continue
            files_seen += 1
            try:
                sz = fp.stat().st_size
            except OSError:
                continue
            if sz > _GREP_PER_FILE_MAX_BYTES * 4:
                continue
            try:
                raw = fp.read_bytes()[:_GREP_PER_FILE_MAX_BYTES]
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if len(matches) >= lim:
                    break
                try:
                    if rx.search(line):
                        matches.append(f"{rel_pos(fp)}:{i}:{line}")
                except Exception:
                    continue
            if len(matches) >= lim:
                break
        if len(matches) >= lim or files_seen >= _GREP_MAX_FILES_SCANNED:
            break

    if not matches:
        return "（无匹配）"
    out = "\n".join(matches)
    if len(matches) >= lim:
        out += f"\n…（已达 max_matches={lim} 上限）"
    if files_seen >= _GREP_MAX_FILES_SCANNED:
        out += f"\n…（已扫描文件数达到上限 {_GREP_MAX_FILES_SCANNED}）"
    return out


async def glob_file_search(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    glob_pattern: str,
    target_directory: str = ".",
) -> str:
    _ = thread_id, caller_agent
    g = (glob_pattern or "").strip()
    if not g:
        return "错误：glob_file_search 必须提供 glob_pattern（如 **/*.md、*.yaml）。"
    try:
        root = _safe_workspace_path(project_dir, target_directory)
    except ValueError as exc:
        return f"错误：{exc}"
    if not root.exists():
        return f"错误：目录不存在：{target_directory}"
    if not root.is_dir():
        return f"错误：target_directory 必须是目录：{target_directory}"

    pat = g.replace("\\", "/")
    if not pat.startswith("**/") and not pat.startswith("/"):
        pat = "**/" + pat.lstrip("/")

    base = Path(project_dir).resolve()
    out: list[str] = []
    try:
        for p in root.glob(pat):
            if len(out) >= _GLOB_MAX_RESULTS:
                break
            if not p.is_file():
                continue
            try:
                rel_parts = p.resolve().relative_to(root).parts
            except Exception:
                continue
            if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in rel_parts):
                continue
            try:
                label = str(p.resolve().relative_to(base)).replace(os.sep, "/")
            except Exception:
                continue
            out.append(label)
    except ValueError as exc:
        return f"错误：glob 无效：{exc}"

    out = sorted(set(out), key=str.lower)
    if not out:
        return "（无匹配文件）"
    body = "\n".join(out)
    if len(out) >= _GLOB_MAX_RESULTS:
        body += f"\n…（仅返回前 {_GLOB_MAX_RESULTS} 条）"
    return body


FILES_TOOL_DISPATCH: dict[str, Callable[..., Awaitable[str]]] = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "grep": grep,
    "glob_file_search": glob_file_search,
}
