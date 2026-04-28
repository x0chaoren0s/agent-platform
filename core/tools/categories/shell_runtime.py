"""Shell command execution tool — cwd is constrained to project_dir."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 120
_MAX_OUTPUT_CHARS = 32_000


def _safe_cwd(project_dir: str, cwd: str | None) -> Path:
    base = Path(project_dir).resolve()
    if not cwd:
        workspace = base / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace
    target = (base / cwd.lstrip("/\\")).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"cwd 必须在项目目录内，收到：{cwd}")
    target.mkdir(parents=True, exist_ok=True)
    return target


async def run_shell(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    command: str,
    cwd: str | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    _ = thread_id, caller_agent
    if not command or not command.strip():
        return "错误：command 不能为空。"

    try:
        work_dir = _safe_cwd(project_dir, cwd)
    except ValueError as exc:
        return f"错误：{exc}"

    timeout_s = min(max(1, int(timeout)), _MAX_TIMEOUT)

    if sys.platform == "win32":
        shell_args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        shell_args = ["/bin/sh", "-c", command]

    try:
        proc = await asyncio.create_subprocess_exec(
            *shell_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work_dir),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"错误：命令执行超时（>{timeout_s}s）。"
    except FileNotFoundError as exc:
        return f"错误：找不到 shell 可执行文件：{exc}"
    except Exception as exc:
        return f"错误：无法启动命令：{exc}"

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    exit_code = proc.returncode

    parts: list[str] = []
    if stdout:
        truncated = len(stdout) > _MAX_OUTPUT_CHARS
        parts.append("[stdout]\n" + stdout[:_MAX_OUTPUT_CHARS] + (" …(已截断)" if truncated else ""))
    if stderr:
        truncated = len(stderr) > _MAX_OUTPUT_CHARS
        parts.append("[stderr]\n" + stderr[:_MAX_OUTPUT_CHARS] + (" …(已截断)" if truncated else ""))
    parts.append(f"[exit_code] {exit_code}")
    return "\n".join(parts)


SHELL_TOOL_DISPATCH: dict[str, object] = {
    "run_shell": run_shell,
}
