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
import stat
import sys
import textwrap
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
import json_repair
from pydantic import BaseModel
import yaml

load_dotenv(Path(__file__).parent / ".env")

# Windows console defaults can cause CJK logs to be mojibake.
# Force UTF-8 output so agent names and Chinese messages render correctly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from core.registry import AgentRegistry
from core.router import MessageRouter
from core.session_store import SessionStore
from core.conversation_store import ConversationStore
from core.checkpoint_store import CheckpointStore
from core import knowledge_base as kb_mod
from core import skill_store
from core import summarizer as summarizer_mod
from core import tool_registry
from core.skill_watcher import SkillWatcher
from core.tools.categories.platform_runtime import dismiss_member, recruit_fixed
from core.tools.categories import files_runtime, shell_runtime, team_runtime
from core.heartbeat import HeartbeatScheduler
from core.red_actions import check_confirm
from core.question_store import QuestionStore
from core.task_store import TaskStore
from core.member_protocol import get_tools_for_role

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agent-platform")

PROJECTS_ROOT = Path(__file__).parent / "projects"
TRASH_ROOT = PROJECTS_ROOT / ".trash"
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
_task_stores: dict[str, TaskStore] = {}
_question_stores: dict[str, QuestionStore] = {}
_heartbeat_scheduler: HeartbeatScheduler | None = None
_skill_watcher: SkillWatcher | None = None


def _project_dir(name: str) -> Path:
    return PROJECTS_ROOT / name


def _find_trashed_project(name: str) -> Path | None:
    """Return path to trashed project dir for *name*, or None.

    Searches for exact match and timestamp-suffixed variants (name_<epoch>).
    """
    TRASH_ROOT.mkdir(parents=True, exist_ok=True)
    candidate = TRASH_ROOT / name
    if candidate.is_dir():
        return candidate
    prefix = name + "_"
    for entry in TRASH_ROOT.iterdir():
        if entry.is_dir() and entry.name.startswith(prefix):
            suffix = entry.name[len(prefix):]
            if suffix.isdigit():
                return entry
    return None


def _trash_project_dir(name: str) -> Path:
    """Return a non-colliding path inside .trash for *name*.

    If .trash/{name} already exists, append _{int(time.time())}.
    """
    TRASH_ROOT.mkdir(parents=True, exist_ok=True)
    target = TRASH_ROOT / name
    if not target.exists():
        return target
    suffix = int(time.time())
    return TRASH_ROOT / f"{name}_{suffix}"


def _activate(name: str) -> None:
    """Switch to a different project (hot-reload registry)."""
    global _current_project, _registry, _session_store, _conv_store, _checkpoint_store, _routers, _task_stores, _question_stores, _skill_watcher
    pdir = _project_dir(name)
    if not pdir.exists():
        raise FileNotFoundError(f"Project directory not found: {pdir}")
    if _registry is not None:
        _registry.stop_watching()
    if _skill_watcher is not None:
        _skill_watcher.stop()
        _skill_watcher = None
    _routers.clear()
    _task_stores.clear()
    _question_stores.clear()
    _registry = AgentRegistry(pdir)
    tool_registry.startup_consistency_check()
    _session_store = SessionStore(pdir / "sessions")
    _conv_store = ConversationStore(pdir / "memory" / "platform.db")
    _checkpoint_store = CheckpointStore(pdir / "memory" / "platform.db", pdir)
    _registry.start_watching()
    _start_skill_watcher()
    team_runtime.set_broadcaster(_ws_broadcast)
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
            broadcaster=_ws_broadcast,
            tool_executor=_agent_tool_executor,
        )
        team_runtime.set_router(thread_id, _routers[thread_id], project_dir=str(pdir))
    return _routers[thread_id]


def _normalize_agent_filename(name: str) -> str:
    return str(name or "").strip().replace(" ", "_").replace("-", "_")


def _force_remove_tree(path: Path, retries: int = 3, delay_seconds: float = 0.2) -> None:
    """
    Remove a directory tree robustly on Windows.
    - Clears readonly bit on failure.
    - Retries a few times for transient file locks.
    Raises the last exception if still not removable.
    """
    import shutil

    def _on_rm_exc(func, p, exc_info):  # type: ignore[no-untyped-def]
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    last_err: Exception | None = None
    for i in range(retries):
        try:
            shutil.rmtree(path, onexc=_on_rm_exc)
            if not path.exists():
                return
            last_err = OSError(f"Project directory still exists after remove: {path}")
        except Exception as exc:
            last_err = exc
        if i < retries - 1:
            time.sleep(delay_seconds)
    if last_err is not None:
        raise last_err


def _sync_registry_after_member_change(*, project_dir: Path, action: str, name: str) -> None:
    """
    Keep registry in sync immediately after recruit/dismiss so UI does not
    depend on watchdog timing.
    """
    if _registry is None:
        return
    safe_name = _normalize_agent_filename(name)
    if not safe_name:
        return
    yaml_path = project_dir / "agents" / f"{safe_name}.yaml"
    try:
        if action == "recruit" and yaml_path.exists():
            _registry._load_file(yaml_path)  # noqa: SLF001 - intentional sync hook
        elif action == "dismiss":
            _registry._unload_file(yaml_path)  # noqa: SLF001 - intentional sync hook
    except Exception:
        logger.exception("Failed to sync registry after %s: %s", action, safe_name)


async def _get_task_store(thread_id: str) -> TaskStore:
    _ = thread_id
    pdir = _project_dir(_current_project)
    key = str(pdir.resolve())
    store = _task_stores.get(key)
    if store is None:
        store = TaskStore(db_path=pdir / "memory" / "tasks.db", project=_current_project)
        await store.init_db()
        _task_stores[key] = store
    return store


async def _get_task_store_by_project_dir(project_dir_str: str) -> TaskStore:
    pdir = Path(project_dir_str)
    key = str(pdir.resolve())
    store = _task_stores.get(key)
    if store is None:
        store = TaskStore(db_path=pdir / "memory" / "tasks.db", project=pdir.name)
        await store.init_db()
        _task_stores[key] = store
    return store


async def _get_question_store(thread_id: str) -> QuestionStore:
    _ = thread_id
    pdir = _project_dir(_current_project)
    key = str(pdir.resolve())
    store = _question_stores.get(key)
    if store is None:
        store = QuestionStore(db_path=pdir / "memory" / "tasks.db", project=_current_project)
        await store.init_db()
        _question_stores[key] = store
    return store


# ---------------------------------------------------------------------------
# Skill file change detection
# ---------------------------------------------------------------------------


def _on_skill_changed(skill_name: str, event_type: str) -> None:
    """Called by SkillWatcher when a SKILL.md is modified or deleted.

    Injects a system advisory into every active router where an agent has
    this skill mounted, and broadcasts a ``skill_updated`` event to the UI.
    """
    if _registry is None:
        return

    affected = _registry.get_agent_names_with_skill(skill_name)
    if not affected:
        logger.debug("Skill '%s' %s but no agent uses it", skill_name, event_type)
        return

    if event_type == "deleted":
        message = (
            f"【技能失效提醒】技能「{skill_name}」已被删除。"
            f"如果你的当前任务依赖此技能，请通知 orchestrator 调整方案。"
        )
        title = "skill_deleted"
    else:
        message = (
            f"【技能更新提醒】技能「{skill_name}」已被修改。"
            f"如果你之前使用过该技能，建议重新调用 load_skill('{skill_name}') 获取最新版本。"
        )
        title = "skill_updated"

    for tid, router in _routers.items():
        for agent_name in affected:
            router.record_system_advisory(
                to_agent=agent_name,
                text=message,
                metadata={"skill_event": True, "skill_name": skill_name, "event_type": event_type},
            )
        asyncio.create_task(_ws_broadcast(tid, {
            "type": title,
            "skill_name": skill_name,
            "event_type": event_type,
            "affected_agents": affected,
        }))

    logger.info(
        "Skill '%s' %s — notified %d agents across %d threads",
        skill_name, event_type, len(affected), len(_routers),
    )


def _start_skill_watcher() -> None:
    """Start watching system-level + project-level skill directories."""
    global _skill_watcher
    pdir = _project_dir(_current_project)
    roots: list[Path] = []
    sys_dir = skill_store._system_skills_dir()
    if sys_dir.exists():
        roots.append(sys_dir)
    proj_dir = skill_store._skills_dir(pdir)
    if proj_dir.exists():
        roots.append(proj_dir)
    roots.extend(skill_store._global_skill_roots())
    _skill_watcher = SkillWatcher(roots, _on_skill_changed)
    _skill_watcher.start()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _heartbeat_scheduler
    _activate(_current_project)
    fc_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not fc_key:
        logger.warning(
            "FIRECRAWL_API_KEY not set — web_search/web_read will return errors. "
            "Configure it in .env to enable web tools."
        )
    else:
        try:
            from firecrawl import FirecrawlApp  # noqa: F401
            logger.info("Firecrawl SDK available; web tools enabled.")
        except Exception:
            logger.warning(
                "firecrawl-py package not installed; web_search/web_read will return errors. "
                "Run: pip install firecrawl-py"
            )
    if _conv_store:
        await _conv_store.init_db()
    if _checkpoint_store:
        await _checkpoint_store.init_db()
    if _conv_store:
        _heartbeat_scheduler = HeartbeatScheduler(
            interval_seconds=2,
            thresholds_seconds={"high": 12, "normal": 8, "low": 60},
            advisory_min_gap_seconds=30,
            conversation_store=_conv_store,
            thread_ids_provider=lambda: list(_routers.keys()),
            project_dir_provider=team_runtime.get_project_dir,
            router_provider=lambda tid: _routers.get(tid),
            task_store_provider=_get_task_store_by_project_dir,
            broadcaster=_ws_broadcast,
        )
        await _heartbeat_scheduler.start()
    yield
    if _heartbeat_scheduler:
        await _heartbeat_scheduler.stop()
        _heartbeat_scheduler = None
    if _registry:
        _registry.stop_watching()
    if _skill_watcher:
        _skill_watcher.stop()
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
      - recruit_fixed 时，args.instructions 必须是单段短文本（建议 <= 300 字），不要写超长多段说明，避免 tool_call JSON 过长导致解析失败。

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

      【招募固定成员流程】（两步走，缺一不可）
      第一步：向用户说明招募理由后，立即调用 ask_user 工具（必须包含 confirm marker）：
      ```tool_call
      {"tool": "ask_user", "args": {"question": "建议招募固定成员「研究员」，专职网络搜索 [[confirm:recruit:研究员]]", "options": [{"id": "yes", "label": "同意"}, {"id": "no", "label": "拒绝"}], "urgency": "high"}}
      ```
      第二步：用户点击"同意"后，再调用 recruit_fixed。
      - 严禁用纯文本写"回复'同意'或'拒绝'"代替 ask_user 工具调用。
      - 严禁在自己的回复中编写【工具结果】内容——工具结果由系统自动注入，你只需等待。

      【任务派发协议】（必读）
      - 当用户请求复杂任务时，必须使用 assign_task 派发，不要只口头通知。
      - 有依赖关系时使用 depends_on；上游 submit_deliverable 后，下游会自动唤醒。
      - depends_on 只能填写已存在的 task-id（形如 task-0001），不能填写成员名。
      - 成员能力不足时，先 recruit_fixed 再 assign_task。
      - 收到 give_up/失败通知后，评估重派、拆解或请用户决策。
      - 若用户请求“前N条/再来N条/额外N条”这类数量型搜索任务，在 brief 里必须明确写入“web_search 需传 limit=N”。
      - 【固定成员优先原则】需要 web_search / web_read 的搜索或调研任务，应优先 assign_task 给具备该能力的固定成员（如数据分析师），由其执行并 submit_deliverable 落盘；只有当团队中无任何成员具备所需能力时，才 recruit_temp 作为兜底。
      - 【禁止提前整合】上游任务状态仍为 in_progress 时，严禁根据中途收到的零散消息提前整合结论或撰写最终报告；必须等到上游成员的【任务交付】通知到达（即对方 submit_deliverable 后系统推送的通知）后，再开始下游整合。
      - 【数据来源约束】整合报告时只允许引用已落盘的 submit_deliverable 内容；禁止凭训练记忆或推断补充交付物中未出现的数据（如定价数字、版本号等），如数据不足应向上游追加搜索任务。
      - 【定价任务 brief 要求】向下游分配涉及价格/版本的调研任务时，brief 中必须注明："对于价格、版本号等具体数值，必须通过 web_read 读取官方定价页原文核实，不得仅凭搜索摘要使用"。

      【内部通信协议】
      - 可使用 send_message 协调成员；普通成员发消息也会自动 CC orchestrator。

      【提问协议】
      - 需要用户决策时使用 ask_user，并提供 2-3 个 options。
      - 信息缺失时优先 ask_user，不要猜测。

      【新增工具示例】（必须按此格式调用）
      ```tool_call
      {"tool": "assign_task", "args": {"assignee": "调研员", "title": "完成星火万物 AIGC 岗位调研", "brief": "聚焦公司背景、产研流程、面试真题", "deadline": "2026-04-26 12:00", "deliverable_kind": "markdown"}}
      ```
      ```tool_call
      {"tool": "assign_task", "args": {"assignee": "面试教练", "title": "基于调研产出面试要点", "brief": "结合上游调研写 5 条核心要点", "depends_on": ["task-0001"], "deliverable_kind": "markdown"}}
      ```
      ```tool_call
      {"tool": "list_tasks", "args": {"scope": "all"}}
      ```
      ```tool_call
      {"tool": "send_message", "args": {"to": ["内容顾问"], "content": "我把简历相关章节加重了，你直接接", "related_task": "task-0002"}}
      ```
      ```tool_call
      {"tool": "ask_user", "args": {"question": "您简历更想突出哪个方向？", "options": [{"id": "tech", "label": "技术深度"}, {"id": "product", "label": "产品视角"}], "urgency": "high"}}
      ```

      【红色操作协议】（不可逆动作，必须先确认）
      - 以下工具属于红色操作：dismiss_member、recruit_fixed、update_project_context、create_skill、update_skill。
      - 调用上述工具前，必须先 ask_user 且 question 文本中包含确认标记（marker），并等待用户回答 "yes"。
      - 若未满足该条件，服务器会拒绝执行。
      - marker 规范：
        - dismiss_member: [[confirm:dismiss:成员名]]
        - recruit_fixed: [[confirm:recruit:成员名]]
        - update_project_context: [[confirm:context:rewrite]]
        - create_skill: [[confirm:create_skill:技能名]]
        - update_skill: [[confirm:update_skill:技能名]]
      - 若工具结果返回“缺少 confirm / 必须先 ask_user”之类错误，下一步必须先发 ask_user，不得重复直接调用红色工具。

      ```tool_call
      {"tool": "ask_user", "args": {"question": "请确认是否解雇成员A [[confirm:dismiss:成员A]]", "options": [{"id": "yes", "label": "确认"}, {"id": "no", "label": "取消"}], "urgency": "high"}}
      ```
      ```tool_call
      {"tool": "ask_user", "args": {"question": "请确认是否新增固定成员B [[confirm:recruit:成员B]]", "options": [{"id": "yes", "label": "确认"}, {"id": "no", "label": "取消"}], "urgency": "high"}}
      ```
      ```tool_call
      {"tool": "ask_user", "args": {"question": "请确认是否覆盖项目背景 [[confirm:context:rewrite]]", "options": [{"id": "yes", "label": "确认"}, {"id": "no", "label": "取消"}], "urgency": "high"}}
	      ```

	      ```tool_call
	      {"tool": "ask_user", "args": {"question": "建议创建 Skill「数据调研模板」scope=project [[confirm:create_skill:数据调研模板]]", "options": [{"id": "yes", "label": "同意创建"}, {"id": "no", "label": "暂不创建"}], "urgency": "high"}}
	      ```
	      ```tool_call
	      {"tool": "ask_user", "args": {"question": "建议更新 Skill「web-research」scope=project [[confirm:update_skill:web-research]]", "options": [{"id": "yes", "label": "同意更新"}, {"id": "no", "label": "暂不更新"}], "urgency": "high"}}
	      ```
      ```

      【协调升级路径（必须按顺序尝试，禁止跳步）】
      当成员沉默、表现异常或任务受阻时，按下面顺序依次尝试，前一步无效再升级：
      1. send_message：先发一条简短消息询问是否遇到困难、需要什么支持。
      2. update_task：在任务上记录 progress_note，标记你已介入协调。
      3. ask_user：若两步无果，向用户发起决策问题（如等待/替换成员/调整任务）。
      4. dismiss_member：仅在用户明确选择后执行，且必须先走红色操作协议拿到 confirm marker。

      【硬性约束 - 违反视为错误】
      1. 派发任务时必须一条一条调用 assign_task 工具，禁止用 markdown 列表"列任务"代替；
         每个成员一个 assign_task，不允许 1 条文本描述 5 个任务。
      2. 需要用户决策时必须调用 ask_user 工具弹出选项卡，禁止在正文里写 "A. ... B. ..."；
         选项必须放在 ask_user 的 options 字段里。
      3. 禁止用 update_project_context 替代 assign_task；
         update_project_context 仅用于更新跨对话的项目背景，不是任务派发渠道。

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
        if p.is_dir() and p.name != ".trash" and (p / "agents").exists()
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
        if (pdir / "agents").exists():
            raise HTTPException(status_code=409, detail=f"项目 '{name}' 已存在")
        # Partial-delete leftover: fall through to scaffold (cleans up by recreating agents/)

    # Check trash for a previously deleted project with this name
    trashed = _find_trashed_project(name)
    if trashed is not None:
        return {
            "ok": False,
            "conflict": "trash_restore",
            "trashed_name": name,
            "message": f"回收站中存在同名项目 '{name}'，请选择恢复旧团队或创建全新团队。",
        }

    _scaffold_project(name)
    _activate(name)
    return {"ok": True, "name": name, "agents": _registry.list_info()}



@app.get("/api/projects/trash")
def list_trashed_projects():
    """List all trashed projects."""
    TRASH_ROOT.mkdir(parents=True, exist_ok=True)
    trashed = sorted(
        p.name for p in TRASH_ROOT.iterdir()
        if p.is_dir()
    )
    return {"ok": True, "trash": trashed}


@app.post("/api/projects/{name}/activate")
def activate_project(name: str):
    pdir = _project_dir(name)
    if not pdir.exists():
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    _activate(name)
    return {"ok": True, "name": name, "agents": _registry.list_info()}


@app.delete("/api/projects/{name}")
def delete_project(name: str):
    """Soft-delete: move project directory to .trash so it can be restored later."""
    global _current_project, _registry, _session_store, _routers

    pdir = _project_dir(name)
    if not pdir.exists():
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")

    is_active = (name == _current_project)

    # Deactivate if active project
    if is_active and _registry is not None:
        _registry.stop_watching()
        _registry = None
        _session_store = None
        _routers.clear()
        _current_project = ""

    # Move to trash instead of hard-delete
    target = _trash_project_dir(name)
    pdir.rename(target)
    logger.info("Moved project '%s' to trash as '%s'", name, target.name)

    # Find remaining projects (exclude .trash)
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    remaining = sorted(
        p.name for p in PROJECTS_ROOT.iterdir()
        if p.is_dir() and p.name != ".trash" and (p / "agents").exists()
    )

    # Auto-activate first remaining project if we deleted the active one
    if is_active and remaining:
        _activate(remaining[0])
        return {
            "ok": True,
            "deleted": name,
            "trashed_to": target.name,
            "switched_to": _current_project,
            "projects": remaining,
            "agents": _registry.list_info() if _registry else [],
        }

    return {
        "ok": True,
        "deleted": name,
        "trashed_to": target.name,
        "switched_to": _current_project,
        "projects": remaining,
        "agents": _registry.list_info() if _registry else [],
    }


@app.post("/api/projects/{name}/restore")
def restore_project(name: str):
    """Restore a trashed project back to active projects."""
    trashed = _find_trashed_project(name)
    if trashed is None:
        raise HTTPException(status_code=404, detail=f"回收站中不存在项目 '{name}'")

    target = _project_dir(name)
    if target.exists():
        raise HTTPException(status_code=409, detail=f"项目 '{name}' 已存在，无法恢复")

    trashed.rename(target)
    _activate(name)
    logger.info("Restored project '%s' from trash", name)

    return {
        "ok": True,
        "name": name,
        "agents": _registry.list_info(),
    }


@app.post("/api/projects/{name}/discard-trash")
def discard_trashed_project(name: str):
    """Permanently delete a trashed project."""
    trashed = _find_trashed_project(name)
    if trashed is None:
        raise HTTPException(status_code=404, detail=f"回收站中不存在项目 '{name}'")

    _force_remove_tree(trashed)
    logger.info("Permanently deleted trashed project '%s'", name)

    return {"ok": True, "deleted": name}



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


async def _ws_broadcast(thread_id: str, event: dict[str, Any]) -> None:
    """Broadcast event to a specific thread websocket if connected."""
    ws = _active_websockets.get(thread_id)
    if ws is None:
        return
    try:
        await ws.send_text(json.dumps(event, ensure_ascii=False))
    except Exception:
        _active_websockets.pop(thread_id, None)


# ---------------------------------------------------------------------------
# Agent management (E2)
# ---------------------------------------------------------------------------

@app.get("/api/agents")
def list_agents():
    return {"project": _current_project, "agents": _registry.list_info()}


@app.get("/api/agents/{name}/effective_prompt")
def get_agent_effective_prompt(name: str):
    cfg = _registry.get_config(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"agent '{name}' not found")
    role = cfg.get("role", "member")
    is_temp = cfg.get("is_temp", False)
    # Normalize legacy / LLM-hallucinated role values so tool list is never empty.
    _known_roles = {"member", "orchestrator", "temp"}
    tool_role = role if role in _known_roles else "member"
    return {
        "name": name,
        "role": role,
        "is_temp": is_temp,
        "skills": cfg.get("skills", []),
        "raw_instructions": cfg.get("instructions", ""),
        "effective_instructions": cfg.get(
            "_effective_instructions",
            cfg.get("instructions", ""),
        ),
        "available_tools": get_tools_for_role(tool_role, is_temp),
    }


class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    role: str = "member"
    capabilities: list[str] = []
    instructions: str = "你是一个专业的助手，请尽力完成分配给你的任务。"
    max_history: int = 80
    skills: list[str] = []
    tools: list[str] = []


class UpdateAgentSkillsRequest(BaseModel):
    skills: list[str] = []


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
        skills=req.skills or None,
        tools=req.tools or None,
    )
    if result.startswith("错误"):
        raise HTTPException(status_code=400, detail=result)
    _sync_registry_after_member_change(
        project_dir=pdir,
        action="recruit",
        name=req.name,
    )
    return {"ok": True, "message": result, "agents": _registry.list_info()}


@app.get("/api/skills")
def list_skills():
    """List all skills available to the active project (project-local + system-level)."""
    pdir = _project_dir(_current_project)
    skills: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    # 1. Project-local skills (take priority, can override system skills with same id)
    skills_dir = pdir / "skills"
    if skills_dir.exists():
        for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
            skill_id = skill_file.parent.name
            parsed = skill_store.read_skill(str(pdir), skill_id)
            if parsed is None:
                skills.append({"id": skill_id, "name": skill_id, "description": "", "scope": "project"})
            else:
                frontmatter, _ = parsed
                skills.append(
                    {
                        "id": skill_id,
                        "name": str(frontmatter.get("name", "")).strip() or skill_id,
                        "description": str(frontmatter.get("description", "")).strip(),
                        "scope": "project",
                    }
                )
            seen_ids.add(skill_id)

    # 2. System-level skills (agent-platform/skills/)
    _add_skills_from_roots(skills, seen_ids, [skill_store._system_skills_dir()])

    # 3. Extra global roots (AGENT_PLATFORM_SKILL_ROOTS)
    _add_skills_from_roots(skills, seen_ids, skill_store._global_skill_roots())

    return {"project": _current_project, "skills": skills}


def _add_skills_from_roots(
    skills: list[dict[str, str]], seen_ids: set[str], roots: list[Path]
) -> None:
    for root in roots:
        if not root.exists():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            skill_id = skill_file.parent.name
            if skill_id in seen_ids:
                continue
            parsed = skill_store._parse_skill_md(skill_file)
            if parsed is None:
                skills.append({"id": skill_id, "name": skill_id, "description": "", "scope": "system"})
            else:
                frontmatter, _ = parsed
                skills.append(
                    {
                        "id": skill_id,
                        "name": str(frontmatter.get("name", "")).strip() or skill_id,
                        "description": str(frontmatter.get("description", "")).strip(),
                        "scope": "system",
                    }
                )
            seen_ids.add(skill_id)


@app.put("/api/agents/{name}/skills")
def update_agent_skills(name: str, req: UpdateAgentSkillsRequest):
    """Hot-plug skills for an existing agent by updating its YAML."""
    pdir = _project_dir(_current_project)
    safe_name = _normalize_agent_filename(name)
    yaml_path = pdir / "agents" / f"{safe_name}.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"agent '{name}' not found")

    cfg = _registry.get_config(name) if _registry is not None else None
    if cfg is None and _registry is not None:
        cfg = _registry.get_config(safe_name)

    clean_skills = sorted({str(item).strip() for item in req.skills if str(item).strip()})
    available = {item["id"] for item in list_skills().get("skills", [])}
    missing = [skill for skill in clean_skills if skill not in available]
    if missing:
        raise HTTPException(status_code=400, detail=f"未知 skill: {', '.join(missing)}")

    try:
        raw_cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw_cfg, dict):
            raise HTTPException(status_code=400, detail=f"配置文件格式错误: {yaml_path.name}")
        raw_cfg["skills"] = clean_skills
        with yaml_path.open("w", encoding="utf-8") as f:
            yaml.dump(raw_cfg, f, allow_unicode=True, sort_keys=False)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新失败: {exc}") from exc

    if _registry is not None:
        try:
            _registry._load_file(yaml_path)  # noqa: SLF001 - immediate reload
        except Exception:
            logger.exception("Failed to hot-reload agent after skills update: %s", safe_name)

    return {
        "ok": True,
        "message": f"已更新 {safe_name} 的 skills",
        "name": safe_name,
        "skills": clean_skills,
    }


@app.delete("/api/agents/{name}")
def delete_agent(name: str):
    """Remove (dismiss) a fixed agent from the active project."""
    pdir = _project_dir(_current_project)
    result = dismiss_member(project_dir=str(pdir), name=name)
    if result.startswith("错误"):
        raise HTTPException(status_code=400, detail=result)
    _sync_registry_after_member_change(
        project_dir=pdir,
        action="dismiss",
        name=name,
    )
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

_TOOL_CALL_RE = re.compile(r"```tool_call\s*\n(.*?)(?:\n```|$)", re.DOTALL)


async def _agent_tool_executor(thread_id: str, agent_name: str, reply_text: str) -> list[dict[str, Any]]:
    pdir = _project_dir(_current_project)
    return await _process_tool_calls(
        reply_text,
        pdir,
        thread_id=thread_id,
        caller_agent=agent_name,
    )


async def _process_tool_calls(
    text: str,
    project_dir: Path,
    thread_id: str,
    caller_agent: str,
) -> list[dict[str, Any]]:
    """
    Parse ```tool_call ... ``` blocks from orchestrator output and execute them.
    Returns list of {"tool": name, "result": str} dicts.
    """
    results = []
    for m in _TOOL_CALL_RE.finditer(text):
        try:
            payload = json_repair.loads((m.group(1) or "").strip())
        except Exception as exc:
            logger.warning("Failed to parse tool_call block: %s", m.group(0))
            snippet = m.group(1).strip().replace("\n", " ")
            if len(snippet) > 180:
                snippet = snippet[:180] + "..."
            results.append(
                {
                    "tool": "tool_call_parse_error",
                    "result": f"工具调用解析失败：{type(exc).__name__}: {exc}。片段：{snippet}",
                }
            )
            continue
        if isinstance(payload, list):
            payloads: list[Any] = payload
        elif isinstance(payload, dict):
            payloads = [payload]
        else:
            payloads = []

        if not payloads:
            snippet = m.group(1).strip().replace("\n", " ")
            if len(snippet) > 180:
                snippet = snippet[:180] + "..."
            results.append(
                {
                    "tool": "tool_call_parse_error",
                    "result": f"工具调用解析失败：tool_call 既不是 JSON 对象也不是对象数组。片段：{snippet}",
                }
            )
            continue

        for item in payloads:
            if not isinstance(item, dict):
                results.append(
                    {
                        "tool": "tool_call_parse_error",
                        "result": f"工具调用解析失败：数组元素不是 JSON 对象（type={type(item).__name__}）。",
                    }
                )
                continue

            tool_name: str = str(item.get("tool", "")).strip()
            args_raw = item.get("args", {})
            args: dict[str, Any] = args_raw if isinstance(args_raw, dict) else {}
            if not tool_name:
                results.append(
                    {
                        "tool": "tool_call_parse_error",
                        "result": "工具调用解析失败：缺少 tool 字段。",
                    }
                )
                continue

            result = await _execute_tool(
                tool_name,
                args,
                project_dir,
                thread_id=thread_id,
                caller_agent=caller_agent,
            )
            results.append({"tool": tool_name, "result": result})
            logger.info("Tool call executed: %s → %s", tool_name, result[:80])

            # If a temp agent was recruited (or reused), register it and dispatch task
            if tool_name == "recruit_temp":
                temp_name = ""
                if result.startswith("RECRUIT_TEMP_DONE:"):
                    temp_name = result.split(":", 1)[1].strip()
                elif "复用现有临时工" in result:
                    # e.g. "复用现有临时工 '搜索助手'。"
                    m2 = re.search(r"'(.+?)'", result)
                    if m2:
                        temp_name = m2.group(1).strip()
                if temp_name:
                    # Force-load YAML into registry immediately (watchdog has latency)
                    temp_yaml = project_dir / "agents" / f"{temp_name}.yaml"
                    if _registry is not None and temp_yaml.exists():
                        try:
                            _registry._load_file(temp_yaml)  # noqa: SLF001
                        except Exception:
                            logger.exception("Failed to force-load temp agent YAML: %s", temp_name)
                    for r in _routers.values():
                        r.register_temp_agent(temp_name)
                    task_content = str(args.get("task", "")).strip()
                    if task_content:
                        await _get_router(thread_id).dispatch_internal(
                            sender=caller_agent,
                            to=[temp_name],
                            cc=[],
                            content=task_content,
                        )
    return results


async def _execute_tool(
    tool_name: str,
    args: dict[str, Any],
    project_dir: Path,
    *,
    thread_id: str,
    caller_agent: str,
) -> str:
    spec = tool_registry.get_tool_spec(tool_name)
    if spec is None:
        return f"未知工具：{tool_name}"

    if spec.is_red:
        qstore = await _get_question_store(thread_id)
        allowed, reason = await check_confirm(
            qstore,
            thread_id=thread_id,
            tool_name=tool_name,
            args=args,
            max_age_seconds=60,
        )
        if not allowed:
            return reason

    ctx = tool_registry.ToolExecContext(
        project_dir=project_dir,
        thread_id=thread_id,
        caller_agent=caller_agent,
    )
    try:
        result = await tool_registry.execute_tool(tool_name, args, ctx)
    except TypeError as exc:
        if tool_name in {"web_search", "web_read"}:
            return f"工具调用参数错误（{tool_name}）：{exc}"
        if tool_name in team_runtime.TEAM_TOOL_DISPATCH or tool_name in files_runtime.FILES_TOOL_DISPATCH or tool_name in shell_runtime.SHELL_TOOL_DISPATCH:
            return (
                f"工具调用参数错误（{tool_name}）：{exc}。请检查必填字段并重试。\n"
                "建议立即调用 list_tasks(scope='mine') 查看当前任务实际状态，避免基于错误假设继续推进。"
            )
        return f"工具调用参数错误（{tool_name}）：{exc}"
    except Exception as exc:
        if tool_name in {"web_search", "web_read"}:
            logger.exception("%s execution error", tool_name)
            return f"工具调用异常（{tool_name}）：{exc}"
        if tool_name in team_runtime.TEAM_TOOL_DISPATCH or tool_name in files_runtime.FILES_TOOL_DISPATCH or tool_name in shell_runtime.SHELL_TOOL_DISPATCH:
            return (
                f"工具调用异常（{tool_name}）：{exc}\n"
                "建议立即调用 list_tasks(scope='mine') 查看当前任务实际状态，避免基于错误假设继续推进。"
            )
        return f"工具调用异常（{tool_name}）：{exc}"

    if tool_name == "recruit_fixed":
        if not result.startswith("错误："):
            _sync_registry_after_member_change(
                project_dir=project_dir,
                action="recruit",
                name=str(args.get("name", "")),
            )
        return result
    elif tool_name == "create_skill":
        if not result.startswith("错误：") and _registry is not None:
            mount_to = args.get("mount_to", [])
            if isinstance(mount_to, list):
                for agent_name in mount_to:
                    agent_name = str(agent_name).strip()
                    if not agent_name:
                        continue
                    yaml_path = project_dir / "agents" / f"{agent_name}.yaml"
                    if yaml_path.exists():
                        try:
                            _registry._load_file(yaml_path)
                        except Exception:
                            logger.exception("Failed to hot-reload agent after skill mount: %s", agent_name)
        return result
    elif tool_name == "dismiss_member":
        if not result.startswith("错误："):
            dismissed_name = str(args.get("name", ""))
            _sync_registry_after_member_change(
                project_dir=project_dir,
                action="dismiss",
                name=dismissed_name,
            )
            # Auto-reassign active tasks to orchestrator
            if dismissed_name and _registry is not None:
                orchestrator_name = _registry.get_orchestrator_name() or "orchestrator"
                try:
                    store = await _get_task_store(thread_id)
                    transferred = await store.reassign_member_tasks(
                        thread_id, dismissed_name, orchestrator_name, "platform"
                    )
                    if transferred:
                        task_lines = "\n".join(
                            f"- {t.id}《{t.title}》(priority={t.priority})"
                            for t in transferred
                        )
                        advisory_text = (
                            f"成员 {dismissed_name} 已离职，以下 {len(transferred)} 个任务已自动转交给你，"
                            f"请重新评估并安排：\n{task_lines}"
                        )
                        router = _routers.get(thread_id)
                        if router is not None:
                            env_dict = router.record_system_advisory(
                                to_agent=orchestrator_name,
                                text=advisory_text,
                                metadata={"auto_reassign": True},
                            )
                            if env_dict:
                                asyncio.create_task(_ws_broadcast(
                                    thread_id,
                                    {"type": "envelope_recorded", "envelope": env_dict},
                                ))
                except Exception:
                    logger.exception("Failed to auto-reassign tasks after dismiss")
        return result
    elif tool_name == "update_project_context":
        # Broadcast context update to all active connections
        asyncio.create_task(_broadcast_to_project({
            "type": "context_updated",
            "project": _current_project,
        }))
        return result
    return result


# ---------------------------------------------------------------------------
# Message log
# ---------------------------------------------------------------------------

@app.get("/api/log")
def get_log(thread_id: str = "default"):
    return {"messages": _get_router(thread_id).get_global_log()}


class AnswerQuestionRequest(BaseModel):
    answer: str


@app.post("/api/threads/{thread_id}/pause")
async def api_pause_thread(thread_id: str):
    if _conv_store is None:
        return {"ok": False, "error": "conversation store not initialized"}
    updated = await _conv_store.set_paused(thread_id, True)
    if not updated:
        return {"ok": False, "error": f"thread not found: {thread_id}"}
    await _ws_broadcast(thread_id, {"type": "thread_paused", "is_paused": True})
    return {"ok": True, "is_paused": True}


@app.post("/api/threads/{thread_id}/resume")
async def api_resume_thread(thread_id: str):
    if _conv_store is None:
        return {"ok": False, "error": "conversation store not initialized"}
    updated = await _conv_store.set_paused(thread_id, False)
    if not updated:
        return {"ok": False, "error": f"thread not found: {thread_id}"}
    await _ws_broadcast(thread_id, {"type": "thread_paused", "is_paused": False})
    return {"ok": True, "is_paused": False}


@app.get("/api/threads/{thread_id}/status")
async def api_thread_status(thread_id: str):
    is_paused = False
    if _conv_store is not None:
        is_paused = await _conv_store.is_paused(thread_id)
    silent_count = 0
    if _heartbeat_scheduler is not None:
        silent_count = _heartbeat_scheduler.get_last_silent_count(thread_id)
    return {"ok": True, "is_paused": is_paused, "silent_task_count": silent_count}


@app.get("/api/threads/{thread_id}/tasks")
async def api_list_tasks(
    thread_id: str,
    status: str | None = None,
    assignee: str | None = None,
    scope: str | None = None,
):
    store = await _get_task_store(thread_id)
    tasks = await store.list(thread_id=thread_id, status=status, assignee=assignee)
    if scope == "blocked":
        tasks = [t for t in tasks if t.status == "blocked_on_user"]
    elif scope == "downstream":
        tasks = [t for t in tasks if bool(t.depends_on)]
    return {"ok": True, "data": [t.__dict__ for t in tasks]}


@app.get("/api/threads/{thread_id}/tasks/{task_id}")
async def api_get_task(thread_id: str, task_id: str):
    _ = thread_id
    store = await _get_task_store(thread_id)
    task = await store.get(task_id)
    if not task:
        return {"ok": False, "error": f"task not found: {task_id}"}
    history = await store.history(task_id)
    data = task.__dict__.copy()
    data["history"] = [h.__dict__ for h in history]
    return {"ok": True, "data": data}


@app.get("/api/threads/{thread_id}/questions")
async def api_list_questions(thread_id: str, status: str = "pending"):
    qstore = await _get_question_store(thread_id)
    if status == "pending":
        rows = await qstore.list_pending(thread_id)
    else:
        rows = []
    return {"ok": True, "data": [q.__dict__ for q in rows]}


@app.post("/api/threads/{thread_id}/questions/{q_id}/answer")
async def api_answer_question(thread_id: str, q_id: str, body: AnswerQuestionRequest):
    qstore = await _get_question_store(thread_id)
    q = await qstore.answer(q_id, answer=body.answer)
    if q is None:
        return {"ok": False, "error": f"question not found or already answered: {q_id}"}
    await _ws_broadcast(thread_id, {"type": "user_answer_received", "question_id": q_id, "answer": body.answer})
    router = _get_router(thread_id)

    async for event in router._dispatch_inner(
        sender="user",
        to=[q.asker],
        cc=[],
        content=f"【用户回答】问题 {q_id}: {body.answer}",
        metadata={"type": "user_answer", "question_id": q_id, "related_task": q.related_task},
    ):
        await _ws_broadcast(thread_id, event)
        if event.get("type") == "tool_results":
            results = event.get("results", [])
            tool_names = {item.get("tool", "") for item in results}
            if {"recruit_fixed", "dismiss_member"} & tool_names:
                await asyncio.sleep(0.35)
                await _ws_broadcast(thread_id, {"type": "agents_updated", "agents": _registry.list_info()})
    return {"ok": True, "data": q.__dict__}


@app.get("/api/threads/{thread_id}/workspace/{path:path}")
async def api_get_workspace_file(thread_id: str, path: str):
    _ = thread_id
    pdir = _project_dir(_current_project)
    workspace_root = (pdir / "workspace").resolve()
    file_path = (workspace_root / path).resolve()
    if not file_path.is_relative_to(workspace_root) or not file_path.exists():
        return {"ok": False, "error": "file not found"}
    return FileResponse(file_path)


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
    events: list[dict] = []
    tool_results: list[dict] = []
    async for event in router.dispatch(sender=req.sender, to=req.to, cc=req.cc, content=req.content):
        events.append(event)
        if event.get("type") == "tool_results":
            tool_results.extend(event.get("results", []))
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
            default_name = f"对话 {thread_id}"
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
                msg = json_repair.loads(raw)
            except Exception:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            action = msg.get("action")
            if action == "answer_question":
                q_id = str(msg.get("question_id", "")).strip()
                answer = str(msg.get("answer", "")).strip()
                if not q_id or not answer:
                    await websocket.send_text(json.dumps({"type": "error", "message": "question_id/answer 不能为空"}))
                    continue
                qstore = await _get_question_store(thread_id)
                q = await qstore.answer(q_id, answer=answer)
                if q is None:
                    await websocket.send_text(json.dumps({"type": "error", "message": f"问题不存在或已处理：{q_id}"}))
                    continue
                await _ws_broadcast(
                    thread_id,
                    {"type": "user_answer_received", "question_id": q_id, "answer": answer},
                )
                await router.dispatch_internal(
                    sender="user",
                    to=[q.asker],
                    cc=[],
                    content=f"【用户回答】问题 {q_id}: {answer}",
                    metadata={"type": "user_answer", "question_id": q_id, "related_task": q.related_task},
                )
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
                try:
                    await websocket.send_text(json.dumps(event, ensure_ascii=False))
                except RuntimeError as send_err:
                    if "close message has been sent" in str(send_err):
                        logger.info("WebSocket already closing thread_id=%s", thread_id)
                        break
                    raise

                # Update last_active once per user turn (on first envelope recorded)
                if not _touched_this_turn and event.get("type") == "envelope_recorded":
                    _touched_this_turn = True
                    if _conv_store:
                        asyncio.create_task(_conv_store.touch(thread_id))

                # Collect reply text for tool_call parsing
                if event.get("type") == "text_delta":
                    agent_name = event.get("agent", "")
                    full_reply_by_agent.setdefault(agent_name, []).append(event.get("delta", ""))

                elif event.get("type") == "agent_done":
                    agent_name = event.get("agent", "")
                    full_reply_by_agent.pop(agent_name, None)

                elif event.get("type") == "tool_results":
                    results = event.get("results", [])
                    tool_names = {item.get("tool", "") for item in results}
                    if {"recruit_fixed", "dismiss_member"} & tool_names:
                        await asyncio.sleep(0.35)
                        try:
                            await websocket.send_text(json.dumps({
                                "type": "agents_updated",
                                "agents": _registry.list_info(),
                            }, ensure_ascii=False))
                        except RuntimeError as send_err:
                            if "close message has been sent" in str(send_err):
                                logger.info("WebSocket already closing thread_id=%s", thread_id)
                                break
                            raise

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected thread_id=%s", thread_id)
        _active_websockets.pop(thread_id, None)
        # Fire-and-forget: rolling summary + memory consolidation
        asyncio.create_task(_on_conversation_disconnect(router, pdir))
    except Exception as exc:
        if isinstance(exc, RuntimeError) and "close message has been sent" in str(exc):
            logger.info("WebSocket closed during send thread_id=%s", thread_id)
            _active_websockets.pop(thread_id, None)
            asyncio.create_task(_on_conversation_disconnect(router, pdir))
            return
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
        notice = summarizer_mod.pop_last_notice()
        if notice:
            await _broadcast_to_project({
                "type": "error",
                "agent": "platform",
                "message": notice,
            })
    except Exception:
        logger.exception("_on_conversation_disconnect background task failed")


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

@app.get("/")
def serve_ui():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools_probe():
    # Chrome/DevTools may probe this path; return empty response to avoid noisy 404 logs.
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=False)
