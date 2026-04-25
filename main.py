"""
Agent Platform - Main server entry point.

Endpoints:
  GET  /                              → web/index.html
  GET  /api/projects                  → list all projects
  POST /api/projects                  → create a new project
  POST /api/projects/{name}/activate  → switch active project
  GET  /api/agents                    → list agents in active project
  POST /api/agents                    → create a new fixed agent (YAML)
  DELETE /api/agents/{name}           → dismiss (remove) an agent
  GET  /api/log                       → global message log
  GET  /api/kb                        → list/search shared knowledge base
  POST /api/kb                        → write a KB entry
  WS   /ws/{thread_id}                → real-time bidirectional chat
  POST /api/chat                      → REST fallback (non-streaming)
  POST /api/approve_proposal          → approve an orchestrator team proposal
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

from core.registry import AgentRegistry
from core.router import MessageRouter
from core.session_store import SessionStore
from core.conversation_store import ConversationStore
from core.checkpoint_store import CheckpointStore
from core import knowledge_base as kb_mod
from core import summarizer as summarizer_mod
from core.platform_tools import (
    list_team,
    recruit_fixed,
    dismiss_member,
    recruit_temp,
    update_project_context,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agent-platform")

PROJECTS_ROOT = Path(__file__).parent / "projects"
WEB_DIR = Path(__file__).parent / "web"

# ---------------------------------------------------------------------------
# Mutable global state (single-process)
# ---------------------------------------------------------------------------
_current_project: str = os.environ.get("AGENT_PROJECT", "manga")
_registry: AgentRegistry | None = None
_session_store: SessionStore | None = None
_conv_store: ConversationStore | None = None
_checkpoint_store: CheckpointStore | None = None
_routers: dict[str, MessageRouter] = {}
_active_websockets: dict[str, WebSocket] = {}  # thread_id → active websocket


def _project_dir(name: str) -> Path:
    return PROJECTS_ROOT / name


def _activate(name: str) -> None:
    """Switch to a different project (hot-reload registry)."""
    global _current_project, _registry, _session_store, _conv_store, _checkpoint_store, _routers
    pdir = _project_dir(name)
    if not pdir.exists():
        raise FileNotFoundError(f"Project directory not found: {pdir}")
    if _registry is not None:
        _registry.stop_watching()
    _routers.clear()
    _registry = AgentRegistry(pdir)
    _session_store = SessionStore(pdir / "sessions")
    _conv_store = ConversationStore(pdir / "memory" / "platform.db")
    _checkpoint_store = CheckpointStore(pdir / "memory" / "platform.db", pdir)
    _registry.start_watching()
    _current_project = name
    logger.info("Activated project '%s'  agents=%s", name, list(_registry.all().keys()))


def _get_router(thread_id: str) -> MessageRouter:
    if thread_id not in _routers:
        pdir = _project_dir(_current_project)
        log_path = pdir / "chat_log" / f"{thread_id}.json"
        _routers[thread_id] = MessageRouter(
            registry=_registry,
            session_store=_session_store,
            thread_id=thread_id,
            log_path=log_path,
        )
    return _routers[thread_id]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    _activate(_current_project)
    if _conv_store:
        await _conv_store.init_db()
    if _checkpoint_store:
        await _checkpoint_store.init_db()
    yield
    if _registry:
        _registry.stop_watching()
    logger.info("Agent platform stopped.")


app = FastAPI(title="Agent Platform", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Project management
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r'^[\w\-]{1,40}$', re.UNICODE)

# Orchestrator YAML template — injected into every new project
_ORCHESTRATOR_YAML_TMPL = textwrap.dedent("""\
    name: orchestrator
    description: "团队指挥官，负责团队组建与任务协调"
    role: orchestrator
    capabilities:
      - team_management
      - task_coordination
      - recruitment
    instructions: |
      你是本项目的 Orchestrator（指挥官），是用户和整个智能体团队的核心桥梁。

      【你的核心职责】
      1. 在项目初始阶段，与用户对话，了解项目需求，提出团队组建方案（成员列表、角色、能力）。
      2. 当用户批准方案后，使用 recruit_fixed 工具逐一招募固定成员。
      3. 在项目进行中，监控任务分发。若有成员上报【需要协助:capability:description】，判断：
         - 该能力是否值得新增固定成员 → 若是，向用户提交招募申请（格式见下）
         - 仅一次性任务 → 使用 recruit_temp 工具招募临时工完成任务
      4. 支持用户随时要求调整团队：新增/解雇成员、更新项目背景。

      【团队管理工具】（以 JSON 格式调用，系统会自动执行）
      当你需要执行团队操作时，在回复末尾输出如下 JSON 代码块（系统解析执行）：

      ```tool_call
      {"tool": "recruit_fixed", "args": {"name": "agent_name", "description": "...", "capabilities": ["cap1"], "instructions": "完整的系统提示词"}}
      ```

      ```tool_call
      {"tool": "dismiss_member", "args": {"name": "agent_name"}}
      ```

      ```tool_call
      {"tool": "recruit_temp", "args": {"name": "temp_name", "description": "...", "capabilities": ["cap1"], "instructions": "...", "task": "具体任务内容"}}
      ```

      ```tool_call
      {"tool": "update_project_context", "args": {"content": "项目背景全文"}}
      ```

      ```tool_call
      {"tool": "list_team", "args": {}}
      ```

      【招募固定成员申请格式】（发给用户审批）
      当你认为需要新增固定成员时，先向用户提交申请，格式：
      > 【招募申请】建议招募 **{成员名}**
      > 角色：{描述}
      > 能力：{能力列表}
      > 理由：{说明}
      > 回复"同意"或"拒绝"

      【注意事项】
      - 用中文与用户交流
      - 每次招募/解雇后，自动调用 list_team 展示最新团队状态
      - 任何工具调用结果会由系统以【工具结果】消息形式反馈给你
    max_history: 100
    """)


def _scaffold_project(name: str) -> Path:
    """Create project directory structure with orchestrator as the initial agent."""
    pdir = _project_dir(name)
    for sub in ("agents", "memory", "sessions"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)
    orchestrator_yaml = pdir / "agents" / "orchestrator.yaml"
    if not orchestrator_yaml.exists():
        orchestrator_yaml.write_text(_ORCHESTRATOR_YAML_TMPL, encoding="utf-8")
    return pdir


@app.get("/api/projects")
def list_projects():
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    projects = sorted(
        p.name for p in PROJECTS_ROOT.iterdir()
        if p.is_dir() and (p / "agents").exists()
    )
    return {"current": _current_project, "projects": projects}


class CreateProjectRequest(BaseModel):
    name: str


@app.post("/api/projects")
def create_project(req: CreateProjectRequest):
    name = req.name.strip()
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="项目名只能包含字母、数字、下划线和连字符，长度 1-40")
    pdir = _project_dir(name)
    if pdir.exists():
        raise HTTPException(status_code=409, detail=f"项目 '{name}' 已存在")
    _scaffold_project(name)
    _activate(name)
    return {"ok": True, "name": name, "agents": _registry.list_info()}


@app.post("/api/projects/{name}/activate")
def activate_project(name: str):
    pdir = _project_dir(name)
    if not pdir.exists():
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    _activate(name)
    return {"ok": True, "name": name, "agents": _registry.list_info()}


@app.delete("/api/projects/{name}")
def delete_project(name: str):
    """Delete a project directory and all its contents."""
    import shutil
    global _current_project, _registry, _session_store, _routers

    pdir = _project_dir(name)
    if not pdir.exists():
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")

    is_active = (name == _current_project)

    # Stop watching if active project
    if is_active and _registry is not None:
        _registry.stop_watching()
        _registry = None
        _session_store = None
        _routers.clear()
        _current_project = ""

    shutil.rmtree(pdir, ignore_errors=True)
    logger.info("Deleted project '%s'", name)

    # Find remaining projects
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    remaining = sorted(
        p.name for p in PROJECTS_ROOT.iterdir()
        if p.is_dir() and (p / "agents").exists()
    )

    # Auto-activate first remaining project if we deleted the active one
    if is_active and remaining:
        _activate(remaining[0])
        return {
            "ok": True,
            "deleted": name,
            "switched_to": _current_project,
            "projects": remaining,
            "agents": _registry.list_info() if _registry else [],
        }

    return {
        "ok": True,
        "deleted": name,
        "switched_to": _current_project,
        "projects": remaining,
        "agents": _registry.list_info() if _registry else [],
    }


# ---------------------------------------------------------------------------
# Broadcast helper
# ---------------------------------------------------------------------------

async def _broadcast_to_project(event: dict[str, Any]) -> None:
    """Send a JSON event to all active WebSocket connections for the current project."""
    msg = json.dumps(event, ensure_ascii=False)
    dead: list[str] = []
    for tid, ws in list(_active_websockets.items()):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(tid)
    for tid in dead:
        _active_websockets.pop(tid, None)


# ---------------------------------------------------------------------------
# Agent management (E2)
# ---------------------------------------------------------------------------

@app.get("/api/agents")
def list_agents():
    return {"project": _current_project, "agents": _registry.list_info()}


class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    role: str = "member"
    capabilities: list[str] = []
    instructions: str = "你是一个专业的助手，请尽力完成分配给你的任务。"
    max_history: int = 80


@app.post("/api/agents")
def create_agent(req: CreateAgentRequest):
    """Manually create a new fixed agent in the active project."""
    pdir = _project_dir(_current_project)
    result = recruit_fixed(
        project_dir=str(pdir),
        name=req.name,
        description=req.description,
        capabilities=req.capabilities,
        instructions=req.instructions,
        role=req.role,
    )
    if result.startswith("错误"):
        raise HTTPException(status_code=400, detail=result)
    # Registry will auto-reload via watchdog; return updated list after brief delay
    import time; time.sleep(0.3)
    return {"ok": True, "message": result, "agents": _registry.list_info()}


@app.delete("/api/agents/{name}")
def delete_agent(name: str):
    """Remove (dismiss) a fixed agent from the active project."""
    pdir = _project_dir(_current_project)
    result = dismiss_member(project_dir=str(pdir), name=name)
    if result.startswith("错误"):
        raise HTTPException(status_code=400, detail=result)
    import time; time.sleep(0.3)
    return {"ok": True, "message": result, "agents": _registry.list_info()}


# ---------------------------------------------------------------------------
# Shared Knowledge Base (E2 + C1)
# ---------------------------------------------------------------------------

class KbWriteRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = []
    author: str = "user"


@app.get("/api/kb")
async def list_kb(q: str = "", limit: int = 20):
    pdir = _project_dir(_current_project)
    if q:
        result = await kb_mod.kb_search(pdir, q, limit=limit)
    else:
        result = await kb_mod.kb_list(pdir, limit=limit)
    return {"result": result}


@app.post("/api/kb")
async def write_kb(req: KbWriteRequest):
    pdir = _project_dir(_current_project)
    result = await kb_mod.kb_write(
        project_dir=pdir,
        title=req.title,
        content=req.content,
        tags=req.tags,
        author=req.author,
    )
    return {"ok": True, "message": result}


# ---------------------------------------------------------------------------
# Conversation management
# ---------------------------------------------------------------------------

@app.get("/api/conversations")
async def list_conversations():
    """List all conversations for the active project."""
    if _conv_store is None:
        raise HTTPException(status_code=503, detail="ConversationStore not initialized")
    convs = await _conv_store.list_by_project(_current_project)
    return {"project": _current_project, "conversations": convs}


class CreateConversationRequest(BaseModel):
    name: str = ""


@app.post("/api/conversations")
async def create_conversation(req: CreateConversationRequest):
    """Create a new conversation (generates a new thread_id)."""
    import random, string
    if _conv_store is None:
        raise HTTPException(status_code=503, detail="ConversationStore not initialized")
    tid = "main-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    name = req.name.strip() or f"对话 {tid}"
    conv = await _conv_store.create(tid, _current_project, name)
    return {"ok": True, "conversation": conv}


class RenameConversationRequest(BaseModel):
    name: str


@app.patch("/api/conversations/{thread_id}/name")
async def rename_conversation(thread_id: str, req: RenameConversationRequest):
    """Rename a conversation."""
    if _conv_store is None:
        raise HTTPException(status_code=503, detail="ConversationStore not initialized")
    new_name = req.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    ok = await _conv_store.rename(thread_id, new_name)
    if not ok:
        raise HTTPException(status_code=404, detail="对话不存在")
    return {"ok": True, "thread_id": thread_id, "name": new_name}


@app.delete("/api/conversations/{thread_id}")
async def delete_conversation(thread_id: str):
    """Hard-delete a conversation: chat_log, session files, SQLite history rows, metadata."""
    import aiosqlite as _aiosqlite
    if _conv_store is None:
        raise HTTPException(status_code=503, detail="ConversationStore not initialized")

    pdir = _project_dir(_current_project)

    # 1. Remove from router cache
    _routers.pop(thread_id, None)
    _active_websockets.pop(thread_id, None)

    # 2. Delete chat_log file
    log_file = pdir / "chat_log" / f"{thread_id}.json"
    if log_file.exists():
        log_file.unlink()

    # 3. Delete session files for all agents
    sessions_dir = pdir / "sessions"
    if sessions_dir.exists():
        for agent_dir in sessions_dir.iterdir():
            if agent_dir.is_dir():
                sess_file = agent_dir / f"{thread_id}.json"
                if sess_file.exists():
                    sess_file.unlink()

    # 4. Delete SQLite history rows in long_term.db
    db_path = pdir / "memory" / "long_term.db"
    if db_path.exists():
        try:
            async with _aiosqlite.connect(db_path) as db:
                # Discover history tables and delete matching session_id rows
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'history_%'"
                ) as cur:
                    tables = [row[0] async for row in cur]
                for tbl in tables:
                    await db.execute(f'DELETE FROM "{tbl}" WHERE session_id = ?', (thread_id,))
                await db.commit()
        except Exception:
            logger.exception("Failed to clean SQLite history for thread_id=%s", thread_id)

    # 5. Delete metadata from platform.db
    await _conv_store.delete(thread_id)

    logger.info("Hard-deleted conversation thread_id=%s from project=%s", thread_id, _current_project)
    return {"ok": True, "deleted": thread_id}


@app.get("/api/conversations/{thread_id}")
async def get_conversation(thread_id: str):
    """Get metadata for a single conversation."""
    if _conv_store is None:
        raise HTTPException(status_code=503, detail="ConversationStore not initialized")
    conv = await _conv_store.get(thread_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conv


# ---------------------------------------------------------------------------
# Checkpoint API
# ---------------------------------------------------------------------------

@app.post("/api/checkpoints")
async def create_checkpoint(body: dict):
    """Create a checkpoint for a conversation."""
    if _checkpoint_store is None:
        raise HTTPException(status_code=503, detail="CheckpointStore not initialized")
    await _checkpoint_store.init_db()
    thread_id = body.get("thread_id", "").strip()
    note = body.get("note", "").strip() or "检查点"
    anchor_message_id = (body.get("anchor_message_id", "") or "").strip() or None
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id 必填")
    import uuid as _uuid
    checkpoint_id = f"chk-{_uuid.uuid4().hex[:12]}"
    result = await _checkpoint_store.create(
        thread_id=thread_id,
        project=_current_project,
        note=note,
        checkpoint_id=checkpoint_id,
        anchor_message_id=anchor_message_id,
    )
    return result


@app.get("/api/checkpoints")
async def list_checkpoints(thread_id: str):
    """List all checkpoints for a conversation."""
    if _checkpoint_store is None:
        raise HTTPException(status_code=503, detail="CheckpointStore not initialized")
    await _checkpoint_store.init_db()
    return await _checkpoint_store.list_by_thread(thread_id)


@app.delete("/api/checkpoints/{checkpoint_id}")
async def delete_checkpoint(checkpoint_id: str):
    """Delete a checkpoint."""
    if _checkpoint_store is None:
        raise HTTPException(status_code=503, detail="CheckpointStore not initialized")
    await _checkpoint_store.init_db()
    ok = await _checkpoint_store.delete(checkpoint_id)
    if not ok:
        raise HTTPException(status_code=404, detail="检查点不存在")
    return {"ok": True}


@app.post("/api/checkpoints/{checkpoint_id}/restore")
async def restore_checkpoint(checkpoint_id: str, body: dict | None = None):
    """
    Restore a conversation to a checkpoint.

    Optional body: { "restore_context": true }  — also restores context.md
    """
    if _checkpoint_store is None:
        raise HTTPException(status_code=503, detail="CheckpointStore not initialized")
    await _checkpoint_store.init_db()

    restore_ctx = bool((body or {}).get("restore_context", False))
    row = await _checkpoint_store.restore(checkpoint_id, restore_context=restore_ctx)
    if row is None:
        raise HTTPException(status_code=404, detail="检查点不存在")

    thread_id = row["thread_id"]

    # Clear the in-memory router so next session loads fresh from disk
    if thread_id in _routers:
        del _routers[thread_id]

    # Build context diff for the prompt hint (if not restoring context.md)
    context_diff_hint: str | None = None
    if not restore_ctx and row.get("context_diff"):
        ctx_path = _project_dir(_current_project) / "context.md"
        current_ctx = ctx_path.read_text(encoding="utf-8") if ctx_path.exists() else ""
        if current_ctx != row["context_diff"]:
            context_diff_hint = (
                "⚠️ 注意：已回滚到检查点，但团队共享的 context.md 未恢复。"
                "当前 context.md 可能包含检查点之后的更新。请谨慎继续操作。"
            )

    # Broadcast checkpoint_restored to all connections on this thread
    event = {
        "type": "checkpoint_restored",
        "checkpoint_id": checkpoint_id,
        "thread_id": thread_id,
        "note": row["note"],
        "created_at": row["created_at"],
        "context_hint": context_diff_hint,
    }
    await _broadcast_to_project(event)

    return {**row, "context_hint": context_diff_hint}


# ---------------------------------------------------------------------------
# Orchestrator tool-call parser (for WS and REST)
# ---------------------------------------------------------------------------

_TOOL_CALL_RE = re.compile(r"```tool_call\s*\n(.*?)\n```", re.DOTALL)


async def _process_tool_calls(
    text: str,
    project_dir: Path,
) -> list[dict[str, Any]]:
    """
    Parse ```tool_call ... ``` blocks from orchestrator output and execute them.
    Returns list of {"tool": name, "result": str} dicts.
    """
    results = []
    for m in _TOOL_CALL_RE.finditer(text):
        try:
            payload = json.loads(m.group(1))
            tool_name: str = payload.get("tool", "")
            args: dict = payload.get("args", {})
        except Exception:
            logger.warning("Failed to parse tool_call block: %s", m.group(0))
            continue

        result = await _execute_tool(tool_name, args, project_dir)
        results.append({"tool": tool_name, "result": result})
        logger.info("Tool call executed: %s → %s", tool_name, result[:80])

        # If a temp agent was recruited, register it in active routers
        if tool_name == "recruit_temp" and result.startswith("RECRUIT_TEMP_DONE:"):
            temp_name = result.split(":", 1)[1]
            for router in _routers.values():
                router.register_temp_agent(temp_name)
            # Registry will pick it up via watchdog shortly
    return results


async def _execute_tool(
    tool_name: str,
    args: dict[str, Any],
    project_dir: Path,
) -> str:
    pdir_str = str(project_dir)
    if tool_name == "list_team":
        return list_team(pdir_str)
    elif tool_name == "recruit_fixed":
        return recruit_fixed(project_dir=pdir_str, **args)
    elif tool_name == "dismiss_member":
        return dismiss_member(project_dir=pdir_str, **args)
    elif tool_name == "recruit_temp":
        return recruit_temp(project_dir=pdir_str, **args)
    elif tool_name == "update_project_context":
        result = update_project_context(project_dir=pdir_str, **args)
        # Broadcast context update to all active connections
        asyncio.create_task(_broadcast_to_project({
            "type": "context_updated",
            "project": _current_project,
        }))
        return result
    elif tool_name == "kb_write":
        return await kb_mod.kb_write(project_dir=project_dir, **args)
    elif tool_name == "kb_search":
        return await kb_mod.kb_search(project_dir=project_dir, **args)
    else:
        return f"未知工具：{tool_name}"


# ---------------------------------------------------------------------------
# Message log
# ---------------------------------------------------------------------------

@app.get("/api/log")
def get_log(thread_id: str = "default"):
    return {"messages": _get_router(thread_id).get_global_log()}


# ---------------------------------------------------------------------------
# REST chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    thread_id: str = "default"
    sender: str = "user"
    to: list[str]
    cc: list[str] = []
    content: str


@app.post("/api/chat")
async def post_chat(req: ChatRequest):
    router = _get_router(req.thread_id)
    pdir = _project_dir(_current_project)
    events: list[dict] = []
    tool_results: list[dict] = []
    async for event in router.dispatch(sender=req.sender, to=req.to, cc=req.cc, content=req.content):
        events.append(event)
        if event.get("type") == "agent_done":
            content = event.get("envelope", {}).get("content", "")
            tr = await _process_tool_calls(content, pdir)
            tool_results.extend(tr)
    return {"events": events, "tool_results": tool_results}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/{thread_id}")
async def websocket_chat(websocket: WebSocket, thread_id: str):
    await websocket.accept()
    logger.info("WebSocket connected thread_id=%s", thread_id)
    router = _get_router(thread_id)

    # Register connection + ensure conversation metadata exists
    _active_websockets[thread_id] = websocket
    if _conv_store:
        await _conv_store.init_db()
    if _checkpoint_store:
        await _checkpoint_store.init_db()
        existing = await _conv_store.get(thread_id)
        if existing is None:
            from datetime import datetime as _dt
            default_name = f"对话 {_dt.now().strftime('%m-%d %H:%M')}"
            await _conv_store.create(thread_id, _current_project, default_name)
        # Do NOT touch() on connect — only update last_active when a message is actually sent

    await websocket.send_text(json.dumps({
        "type": "init",
        "project": _current_project,
        "agents": _registry.list_info(),
        "log": router.get_global_log(),
    }, ensure_ascii=False))

    pdir = _project_dir(_current_project)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            sender  = msg.get("sender", "user")
            to      = msg.get("to", [])
            cc      = msg.get("cc", [])
            content = msg.get("content", "")
            images  = msg.get("images", [])   # list of data URIs

            if not content.strip() and not images:
                continue
            if not to:
                await websocket.send_text(json.dumps({"type": "error", "message": "消息必须指定至少一个收件人"}))
                continue

            full_reply_by_agent: dict[str, list[str]] = {}

            _touched_this_turn = False
            async for event in router.dispatch(sender=sender, to=to, cc=cc, content=content, images=images):
                await websocket.send_text(json.dumps(event, ensure_ascii=False))

                # Update last_active once per user turn (on first envelope recorded)
                if not _touched_this_turn and event.get("type") == "envelope_recorded":
                    _touched_this_turn = True
                    if _conv_store:
                        asyncio.create_task(_conv_store.touch(thread_id))

                # Collect reply text for tool_call parsing
                if event.get("type") == "text_delta":
                    agent_name = event.get("agent", "")
                    full_reply_by_agent.setdefault(agent_name, []).append(event.get("delta", ""))

                # After agent finishes, process any tool_call blocks
                elif event.get("type") == "agent_done":
                    agent_name = event.get("agent", "")
                    reply_chunks = full_reply_by_agent.pop(agent_name, [])
                    reply_text = "".join(reply_chunks)
                    tool_results = await _process_tool_calls(reply_text, pdir)
                    for tr in tool_results:
                        # Feed tool result back as a system message in the log
                        feedback_event = {
                            "type": "tool_result",
                            "tool": tr["tool"],
                            "result": tr["result"],
                            "triggered_by": agent_name,
                        }
                        await websocket.send_text(json.dumps(feedback_event, ensure_ascii=False))

                    if tool_results:
                        # Send updated agent list so UI refreshes
                        await websocket.send_text(json.dumps({
                            "type": "agents_updated",
                            "agents": _registry.list_info(),
                        }, ensure_ascii=False))

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected thread_id=%s", thread_id)
        _active_websockets.pop(thread_id, None)
        # Fire-and-forget: rolling summary + memory consolidation
        asyncio.create_task(_on_conversation_disconnect(router, pdir))
    except Exception as exc:
        logger.exception("WebSocket error thread_id=%s", thread_id)
        _active_websockets.pop(thread_id, None)
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
        except Exception:
            pass


async def _on_conversation_disconnect(router: MessageRouter, pdir: Path) -> None:
    """Background task: rolling summary + consolidate key decisions to context.md."""
    try:
        # Rolling summary: compress old messages in-memory if log is large
        if router.needs_summarization():
            logger.info("Triggering rolling summary for thread=%s", router._thread_id)
            await router.do_rolling_summary()

        # Memory consolidation: append conversation highlights to context.md
        recent = router.get_recent_envelopes(50)
        if recent:
            context_path = pdir / "context.md"
            summary = await summarizer_mod.consolidate_to_context(recent, context_path)
            if summary:
                # Notify all other active connections about the context update
                await _broadcast_to_project({
                    "type": "context_updated",
                    "project": _current_project,
                })
    except Exception:
        logger.exception("_on_conversation_disconnect background task failed")


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

@app.get("/")
def serve_ui():
    return FileResponse(WEB_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=False)
