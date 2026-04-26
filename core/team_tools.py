"""Team collaboration tools for MVP-Plus tasking and communication."""

from __future__ import annotations
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from . import web_tools
from .question_store import QuestionStore, UserQuestion
from .task_store import Task, TaskStore

_TASK_STORES: dict[str, TaskStore] = {}
_QUESTION_STORES: dict[str, QuestionStore] = {}
_ROUTERS: dict[str, Any] = {}
_THREAD_PROJECT_DIR: dict[str, str] = {}
_BROADCASTER: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [s for item in value if (s := str(item).strip())]
    text = str(value).strip()
    return [text] if text else []


def set_router(thread_id: str, router: Any, *, project_dir: str | None = None) -> None:
    _ROUTERS[thread_id] = router
    if project_dir:
        _THREAD_PROJECT_DIR[thread_id] = project_dir


def get_project_dir(thread_id: str) -> str | None:
    return _THREAD_PROJECT_DIR.get(thread_id)


def set_broadcaster(
    broadcaster: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
) -> None:
    global _BROADCASTER
    _BROADCASTER = broadcaster


def _project_name(project_dir: Path) -> str:
    return project_dir.name


async def _emit(thread_id: str, event: dict[str, Any]) -> None:
    if _BROADCASTER is not None:
        await _BROADCASTER(thread_id, event)


async def _get_task_store(project_dir: str) -> TaskStore:
    pdir = Path(project_dir)
    key = str(pdir.resolve())
    store = _TASK_STORES.get(key)
    if store is None:
        store = TaskStore(
            db_path=pdir / "memory" / "tasks.db",
            project=_project_name(pdir),
        )
        await store.init_db()
        _TASK_STORES[key] = store
    return store


async def _get_question_store(project_dir: str) -> QuestionStore:
    pdir = Path(project_dir)
    key = str(pdir.resolve())
    store = _QUESTION_STORES.get(key)
    if store is None:
        store = QuestionStore(
            db_path=pdir / "memory" / "tasks.db",
            project=_project_name(pdir),
        )
        await store.init_db()
        _QUESTION_STORES[key] = store
    return store


def _agent_names(project_dir: Path) -> set[str]:
    names: set[str] = set()
    for yaml_file in sorted((project_dir / "agents").glob("*.yaml")):
        try:
            with yaml_file.open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            name = str(cfg.get("name", "")).strip()
            if name:
                names.add(name)
        except Exception:
            continue
    return names


def _orchestrator_name(project_dir: Path) -> str:
    for yaml_file in sorted((project_dir / "agents").glob("*.yaml")):
        try:
            with yaml_file.open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            if cfg.get("role") == "orchestrator":
                return str(cfg.get("name", "orchestrator"))
        except Exception:
            continue
    return "orchestrator"


async def assign_task(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    assignee: str,
    title: str,
    brief: str,
    deadline: str | None = None,
    depends_on: list[str] | None = None,
    deliverable_kind: str = "markdown",
    context_refs: list[str] | None = None,
    priority: str = "normal",
    **kwargs: Any,  # 兼容性：忽略未知参数如 task
) -> str:
    if not title or not str(title).strip():
        return "错误：assign_task 必须包含 title 字段，请检查 args。"
    if not brief or not str(brief).strip():
        return "错误：assign_task 必须包含 brief 字段，请检查 args。"
    pdir = Path(project_dir)
    orchestrator = _orchestrator_name(pdir)
    if caller_agent != orchestrator:
        return f"错误：仅 orchestrator 可派发任务，当前调用者={caller_agent}"
    agents = _agent_names(pdir)
    if assignee not in agents:
        return f"错误：找不到 assignee '{assignee}'"
    store = await _get_task_store(project_dir)
    deps = _as_list(depends_on)
    context_refs = _as_list(context_refs)
    for dep in deps:
        dep_str = str(dep)
        if not dep_str.startswith("task-"):
            return (
                f"错误：depends_on 必须填写 task-id（形如 task-0001），"
                f"不能填写成员名或其它字符串：{dep_str}。"
            )
        if await store.get(dep) is None:
            return (
                f"错误：依赖任务不存在：{dep}。"
                "请先调用 list_tasks(scope='all') 确认上游任务 id。"
            )
    task = Task(
        id="",
        project=_project_name(pdir),
        thread_id=thread_id,
        title=title,
        brief=brief,
        assignee=assignee,
        created_by=caller_agent,
        priority=priority,
        deadline=deadline,
        deliverable_kind=deliverable_kind,
        depends_on=deps,
        context_refs=context_refs,
    )
    created = await store.create(task)
    await _emit(thread_id, {"type": "task_event", "event": "created", "task": created.__dict__})
    if created.status == "ready":
        router = _ROUTERS.get(thread_id)
        if router is not None and hasattr(router, "notify_assignee"):
            await router.notify_assignee(created.__dict__)
    return f"已派发任务 {created.id} 给 {assignee}"


async def update_task(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    task_id: str,
    status: str | None = None,
    progress_note: str | None = None,
    **kwargs: Any,
) -> str:
    if not task_id or not str(task_id).strip():
        return "错误：update_task 必须包含 task_id 字段，请检查 args。"
    if not status and not progress_note:
        return "错误：update_task 至少需要 status 或 progress_note 之一。"
    _ = thread_id
    store = await _get_task_store(project_dir)
    task = await store.get(task_id)
    if task is None:
        return f"错误：任务不存在：{task_id}"
    if caller_agent not in {task.assignee, task.created_by}:
        return f"错误：仅任务 owner 可更新，当前调用者={caller_agent}"
    if task.status in {"done", "failed", "cancelled"}:
        return f"错误：任务 {task_id} 已处于终态（{task.status}），不可再变更状态"
    if status and status not in {"in_progress", "blocked_on_user"}:
        return "错误：status 仅允许 in_progress 或 blocked_on_user"
    if status:
        task = await store.update_status(task_id, new_status=status, actor=caller_agent)
    if progress_note:
        task = await store.update_progress(task_id, note=progress_note, actor=caller_agent)
    if task is None:
        return f"错误：更新失败：{task_id}"
    await _emit(task.thread_id, {"type": "task_event", "event": "updated", "task": task.__dict__})
    return f"任务 {task_id} 已更新"


async def submit_deliverable(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    task_id: str,
    content: str | None = None,
    file_path: str | None = None,
    summary: str,
    references: list[str] | None = None,
    **kwargs: Any,
) -> str:
    if not task_id or not str(task_id).strip():
        return "错误：submit_deliverable 必须包含 task_id 字段，请检查 args。"
    if not summary or not str(summary).strip():
        return "错误：submit_deliverable 必须包含 summary 字段，请检查 args。"
    _ = thread_id
    store = await _get_task_store(project_dir)
    task = await store.get(task_id)
    if task is None:
        return f"错误：任务不存在：{task_id}"
    if caller_agent != task.assignee:
        return f"错误：仅 assignee 可交付，当前调用者={caller_agent}"
    if bool(content) == bool(file_path):
        return "错误：content 与 file_path 必须二选一"

    auto_refs = web_tools.consume_url_history(thread_id, caller_agent)
    explicit_refs = [str(r).strip() for r in (references or []) if str(r).strip()]

    def _extract_ref_url(raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        # Markdown link: [title](url)
        lpos = text.rfind("](")
        if lpos != -1 and text.endswith(")"):
            candidate = text[lpos + 2 : -1].strip()
            if candidate.startswith(("http://", "https://")):
                return candidate
        if text.startswith(("http://", "https://")):
            return text
        return ""

    def _norm_url(url: str) -> str:
        return (url or "").strip().rstrip("/").lower()

    ref_lines: list[str] = []
    seen_urls: set[str] = set()
    seen_raw_lines: set[str] = set()

    for item in auto_refs:
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not url:
            continue
        norm = _norm_url(url)
        if norm and norm in seen_urls:
            continue
        if norm:
            seen_urls.add(norm)
        ref_lines.append(f"- [{title or url}]({url})")

    for r in explicit_refs:
        url = _extract_ref_url(r)
        if url:
            norm = _norm_url(url)
            if norm and norm in seen_urls:
                continue
            if norm:
                seen_urls.add(norm)
            ref_lines.append(f"- {r}")
            continue
        # Non-URL text reference: dedupe exact normalized line only.
        raw_norm = r.strip().lower()
        if raw_norm in seen_raw_lines:
            continue
        seen_raw_lines.add(raw_norm)
        ref_lines.append(f"- {r}")
    refs_section = ""
    if ref_lines:
        refs_section = "\n\n## References\n" + "\n".join(ref_lines) + "\n"

    deliverable_path = file_path
    if content is not None:
        full_content = content + refs_section
        safe_title = "".join(c if c.isalnum() else "-" for c in task.title.lower()).strip("-")
        safe_title = safe_title or "deliverable"
        rel_path = f"{task_id}-{safe_title[:32]}.md"
        deliverable_path = store.write_deliverable_file(rel_path, full_content)
    assert deliverable_path is not None
    updated = await store.submit_deliverable(
        task_id,
        path=deliverable_path,
        summary=summary,
        actor=caller_agent,
    )
    if updated is None:
        return f"错误：交付失败：{task_id}"
    downstream = await store.find_ready_downstream(task_id)
    router = _ROUTERS.get(updated.thread_id)
    if router is not None and hasattr(router, "notify_assignee"):
        for task_item in downstream:
            await router.notify_assignee(task_item.__dict__)
    await _emit(updated.thread_id, {"type": "task_event", "event": "delivered", "task": updated.__dict__})
    for task_item in downstream:
        await _emit(task_item.thread_id, {"type": "task_event", "event": "ready", "task": task_item.__dict__})

    # Auto-notify task creator so they always get a chat bubble, regardless of
    # whether the assignee explicitly calls send_message afterward.
    if router is not None and updated.created_by and updated.created_by != caller_agent:
        notify_content = (
            f"【任务交付】{task_id}「{updated.title}」已完成并提交。\n"
            f"摘要：{summary}\n"
            f"交付文件：workspace/{deliverable_path}"
        )
        await router.dispatch_internal(
            sender=caller_agent,
            to=[updated.created_by],
            cc=[],
            content=notify_content,
            metadata={"type": "task_delivery_notice", "task_id": task_id},
        )

    ready_text = ", ".join(t.id for t in downstream) if downstream else "无"
    refs_hint = f"，引用 {len(ref_lines)} 条" if ref_lines else ""
    return f"已交付 {task_id}：workspace/{deliverable_path}（ready_downstream={ready_text}{refs_hint}）"


async def list_tasks(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    scope: str = "mine",
    status: str | None = None,
) -> str:
    store = await _get_task_store(project_dir)
    assignee = caller_agent if scope == "mine" else None
    tasks = await store.list(thread_id=thread_id, status=status, assignee=assignee)
    if scope == "blocked":
        tasks = [t for t in tasks if t.status == "blocked_on_user"]
    if scope == "downstream":
        tasks = [t for t in tasks if bool(t.depends_on)]
    if not tasks:
        return "（无任务）"
    lines = [
        "| id | title | assignee | status | priority | depends_on |",
        "|---|---|---|---|---|---|",
    ]
    for t in tasks:
        deps = ",".join(t.depends_on) if t.depends_on else "-"
        lines.append(
            f"| {t.id} | {t.title} | {t.assignee} | {t.status} | {t.priority} | {deps} |"
        )
    return "\n".join(lines)


async def send_message(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    to: list[str],
    content: str,
    cc: list[str] | None = None,
    related_task: str | None = None,
) -> str:
    to_list = _as_list(to)
    cc_list = _as_list(cc)
    clean_to = sorted({name for name in to_list if name and name != caller_agent})
    if not clean_to:
        return "错误：to 不能为空"
    orchestrator = _orchestrator_name(Path(project_dir))
    final_cc = sorted(set(cc_list))
    if caller_agent != orchestrator and orchestrator not in final_cc:
        final_cc.append(orchestrator)
    router = _ROUTERS.get(thread_id)
    if router is not None and hasattr(router, "check_flood"):
        if router.check_flood(sender=caller_agent, to=clean_to):
            if orchestrator not in final_cc:
                final_cc.append(orchestrator)
            return f"错误：消息发送过于频繁，请稍后再试（已强制 CC {orchestrator}）"
    metadata = {"related_task": related_task} if related_task else {}
    if router is not None and hasattr(router, "dispatch_internal"):
        await router.dispatch_internal(
            sender=caller_agent,
            to=clean_to,
            cc=final_cc,
            content=content,
            metadata=metadata,
        )
    else:
        await _emit(
            thread_id,
            {
                "type": "internal_message",
                "sender": caller_agent,
                "to": clean_to,
                "cc": final_cc,
                "content": content,
                "metadata": metadata,
            },
        )
    return f"已发送给 {', '.join(clean_to)}（CC: {', '.join(final_cc) if final_cc else '无'}）"


async def ask_user(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    question: str,
    options: list[dict[str, str]] | None = None,
    related_task: str | None = None,
    urgency: str = "normal",
) -> str:
    qstore = await _get_question_store(project_dir)
    tstore = await _get_task_store(project_dir)
    if related_task:
        task = await tstore.get(related_task)
        if task:
            await tstore.update_status(
                related_task,
                new_status="blocked_on_user",
                actor=caller_agent,
                note="waiting for user answer",
            )
    created = await qstore.create(
        UserQuestion(
            id="",
            project=Path(project_dir).name,
            thread_id=thread_id,
            asker=caller_agent,
            question=question,
            options=options,
            related_task=related_task,
            urgency=urgency,
        )
    )
    await _emit(thread_id, {"type": "user_question", "question": created.__dict__})
    return f"已向用户提问 {created.id}"


async def give_up(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    task_id: str,
    reason: str,
) -> str:
    store = await _get_task_store(project_dir)
    task = await store.get(task_id)
    if task is None:
        return f"错误：任务不存在：{task_id}"
    if caller_agent != task.assignee:
        return f"错误：仅 assignee 可放弃任务，当前调用者={caller_agent}"
    updated = await store.update_status(
        task_id,
        new_status="failed",
        actor=caller_agent,
        note=reason,
    )
    if updated is None:
        return f"错误：任务状态更新失败：{task_id}"
    orchestrator = _orchestrator_name(Path(project_dir))
    router = _ROUTERS.get(thread_id)
    text = f"任务 {task_id} 已由 {caller_agent} 放弃，原因：{reason}"
    if router is not None and hasattr(router, "dispatch_internal"):
        await router.dispatch_internal(
            sender=caller_agent,
            to=[orchestrator],
            cc=[],
            content=text,
            metadata={"task_id": task_id, "type": "give_up"},
        )
    await _emit(updated.thread_id, {"type": "task_event", "event": "failed", "task": updated.__dict__})
    return f"已放弃任务 {task_id}，并通知 {orchestrator}"


TEAM_TOOL_DISPATCH: dict[str, Callable[..., Awaitable[str]]] = {
    "assign_task": assign_task,
    "update_task": update_task,
    "submit_deliverable": submit_deliverable,
    "list_tasks": list_tasks,
    "send_message": send_message,
    "ask_user": ask_user,
    "give_up": give_up,
}

