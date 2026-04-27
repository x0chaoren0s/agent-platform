from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ToolRole = Literal["orchestrator", "member", "temp"]


@dataclass(frozen=True)
class ToolContext:
    project_dir: Path
    thread_id: str
    caller_agent: str
    server: Any | None = None


class BaseTool(ABC):
    # Minimal required metadata
    name: str = ""
    roles: frozenset[ToolRole] = frozenset()
    is_red: bool = False
    desc: str = ""
    args_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}

    # Optional metadata
    signature: str = ""
    examples: list[dict[str, Any]] = []

    def validate_args(self, args: dict[str, Any]) -> None:
        _ = args

    async def precheck(self, args: dict[str, Any], ctx: ToolContext) -> None:
        _ = args, ctx

    @abstractmethod
    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        raise NotImplementedError

    async def post_success(self, result: str, args: dict[str, Any], ctx: ToolContext) -> None:
        _ = result, args, ctx

