# 团队自治 MVP-Plus 执行清单（PLAN 文档）

> **本文档是 PLAN 模式产出的实施规范**，跨 chat session 复用。任何新 session 接手时按"§0 导读"上手。
>
> 配套文档：`docs/team-mvp-plus.md`（设计文档，记录"为什么这么做"）。

---

## 0. 导读（新 session 必读）

### 0.1 我是新接手的 session，从哪开始？

按以下顺序读：

1. 读本文档 §1 ~ §3：了解架构总览与现状摘要
2. 读 `docs/team-mvp-plus.md`：理解 MVP-Plus 的设计取舍背景
3. 跑 `git log --oneline -20`：看已经 commit 了哪些步骤
4. 跑 `git status`：看是否有未提交的 work in progress
5. 跳到 §10 执行清单，找到第一个 ⬜ 状态的步骤开始
6. 每完成一个步骤，把 ⬜ 改 ✅ 并 commit（commit message 见每步建议）

### 0.2 为什么有这么多文字？

任务量较大，跨 session 上下文不可保留，所以把所有可被遗忘的细节（schema / 签名 / API / UI 结构）都写死在本文档中。**实施时不需要再"决定"任何东西**，按文档 spec 翻译即可。

### 0.3 模式纪律

本文档由 RIPER-5 PLAN 模式产出，**已经过用户批准**。新 session 收到 "ENTER EXECUTE MODE" 信号即可直接开干，**无需再次询问用户确认计划**。如发现 spec 有问题，必须回到 PLAN 模式修订本文档而非自行偏离。

### 0.4 已锁定的 4 项设计选择（无需再讨论）

1. 新工具放在 `core/team_tools.py`（不塞进 platform_tools.py）
2. `ask_user` 阻塞当前 task（task→`blocked_on_user`，agent 等回复后才唤醒）
3. 防洪水阈值：同一对成员 5 分钟内最多 6 条
4. `send_message` 的 CC envelope 用户可见，UI 通过 `internal_message` 事件渲染，可选给浅色背景区分

---

## 1. 总览

### 1.1 MVP-Plus 范围（来自 design doc §9.4）

**包含：**

- 任务 4 原语：`assign_task` / `update_task` / `submit_deliverable` / `list_tasks`
- 通信 1 原语：`send_message`（默认 CC orchestrator + 消息洪水保护）
- 提问 1 原语：`ask_user`（选择题 + 开放题）
- Router：`submit_deliverable` 触发下游 `notify_assignee`
- UI：聊天内嵌任务卡 + 顶部 Kanban + ask_user 卡片 + 待办收件箱铃铛
- Orchestrator system prompt 升级

**不包含：**

- 失败重试 / 换人 / 招募联动（除 `give_up` 之外）
- Token 预算硬约束
- 定时心跳 / 超时催办
- 嵌套子任务 / 黑板系统 / 知识库索引
- ask_user 确认形态、附件预览

### 1.2 文件改动地图

| 类型 | 路径 | 说明 |
|------|------|------|
| 🆕 新建 | `core/task_store.py` | Task SQLite CRUD + dataclass |
| 🆕 新建 | `core/question_store.py` | UserQuestion SQLite CRUD + dataclass |
| 🆕 新建 | `core/team_tools.py` | 7 个新工具的实现（与 platform_tools.py 区分团队/平台职责） |
| 🆕 新建 | `projects/<p>/workspace/.gitkeep` | Workspace 目录占位 |
| 🆕 新建 | `docs/team-mvp-plus-todolist.md` | 本文档 |
| ✏️ 修改 | `core/router.py` | 新增 `notify_assignee` 方法 + `dispatch_internal` 兼容入口 |
| ✏️ 修改 | `main.py` | 新增 5 个 REST + WS 事件广播 + `_execute_tool` 分支 + Orchestrator 模板升级 |
| ✏️ 修改 | `web/index.html` | 新增任务卡 / Kanban / 问题卡 / 收件箱铃铛 + WS 事件分流 |
| ✏️ 修改 | `projects/Interview/agents/*.yaml` | orchestrator + 4 成员系统提示词追加协议段 |
| ✏️ 修改 | `projects/manga/agents/orchestrator.yaml` | 模板同步 |
| ✏️ 修改 | `.gitignore` | 加 `projects/*/workspace/`（产物默认本地） |
| ✏️ 修改 | `README.md` | 主要功能列表追加"任务/Kanban/ask_user/工作区"条目 |

### 1.3 提交计划（每个 phase = 1 个 commit）

| Phase | Commit 主题 |
|-------|-------------|
| 1 | `feat(store): 新增 task_store / question_store + workspace 目录` |
| 2 | `feat(tools): 新增团队 7 工具（assign/update/list/submit/send/ask/give_up）` |
| 3 | `feat(router): 工具结果路由到任务事件链 + 自动唤醒下游 + 防洪水` |
| 4 | `feat(api): 新增任务/提问 REST 端点 + WS 事件广播` |
| 5 | `feat(web): 任务卡 + Kanban + 问题卡 + 收件箱铃铛` |
| 6 | `feat(prompt): orchestrator 与成员提示词追加任务/通信/提问协议` |
| 7 | `doc(readme): 同步 MVP-Plus 新功能说明` |

---

## 2. 现状摘要（来自 git log + 源码扫描）

### 2.1 工具调用协议（关键）

Agent 输出文本中以如下代码块标记工具调用：

```
```tool_call
{"tool": "工具名", "args": {...}}
```
```

`main.py::_TOOL_CALL_RE` = `r"```tool_call\s*\n(.*?)\n```"` 解析。
`main.py::_process_tool_calls` 提取 → `_execute_tool` 大 if/elif 分发。
执行后通过 `router.record_tool_feedback(agent_name, tool_results)` 写入 `_global_log`（platform sender），下一轮 prompt 可见。

### 2.2 现有平台工具（参考实现风格）

`core/platform_tools.py` 内：

- `list_team(project_dir) → str`
- `recruit_fixed(project_dir, name, description, capabilities, instructions, role='member') → str`
- `dismiss_member(project_dir, name) → str`
- `recruit_temp(project_dir, name, description, capabilities, instructions, task) → str`
- `update_project_context(project_dir, content) → str`

所有工具都返回 `str`（人类可读结果，给 LLM 阅读）。**新工具沿用此约定**。

### 2.3 Router 现状

`core/router.py`：

- `Envelope { id, sender, to:list, cc:list, content, timestamp, metadata, images }`
- `MessageRouter._dispatch_inner(sender, to, cc, content, metadata, images, _depth=0)` — 支持递归升级，最大深度 3
- `_global_log: list[Envelope]` + `_flush_log()` 落盘到 `projects/<p>/chat_log/<thread_id>.json`
- `_inbox_for(agent)` + `_build_prompt_for(agent, new_envelope)` — agent 上下文构造
- `record_tool_feedback(triggered_by, tool_results)` — 已实现（上一批 commit）

### 2.4 WebSocket 现状

`main.py /ws/{thread_id}`：

- 客户端 → server：`{sender, to, cc, content, images}` JSON
- server → 客户端事件类型：`init`, `envelope_recorded`, `text_delta`, `reasoning_delta`, `agent_done`, `error`, `escalation`, `tool_result`, `agents_updated`, `context_updated`

### 2.5 持久化布局

```
projects/<project>/
  agents/                  # YAML，每成员一份
  chat_log/<thread>.json   # 全局 envelope log
  sessions/<agent>/<thread>.json   # MAF session
  memory/platform.db       # SQLite：conversations + checkpoints + KB
  memory/kb.db             # （legacy 命名变体，按现有代码为准）
  context.md               # 项目共享背景
```

**MVP-Plus 新增：**

```
projects/<project>/
  memory/tasks.db          # 新增：tasks + task_dependencies + task_history + user_questions
  workspace/               # 新增：交付物文件夹（git 忽略）
```

---

## 3. 数据规范

### 3.1 SQLite DDL（写到 `core/task_store.py::_SCHEMA`）

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,           -- 'task-0001'，按 thread_id 自增
    project          TEXT NOT NULL,
    thread_id        TEXT NOT NULL,
    title            TEXT NOT NULL,
    brief            TEXT NOT NULL,
    assignee         TEXT NOT NULL,
    created_by       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
                     -- pending | ready | in_progress | blocked_on_user | done | failed | cancelled
    priority         TEXT NOT NULL DEFAULT 'normal',
                     -- low | normal | high | urgent
    deadline         TEXT,                       -- ISO 8601 或 NULL
    deliverable_kind TEXT NOT NULL DEFAULT 'markdown',
                     -- markdown | json | file | decision | none
    deliverable_path TEXT,                       -- workspace 内相对路径，完成后填
    deliverable_summary TEXT,                    -- 交付物摘要，agent 提供
    retries          INTEGER NOT NULL DEFAULT 0,
    max_retries      INTEGER NOT NULL DEFAULT 2,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    closed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_thread_status ON tasks(thread_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(thread_id, assignee);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id    TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_context_refs (
    task_id TEXT NOT NULL,
    ref     TEXT NOT NULL,                       -- 'msg-0042' 或 'task-0001.deliverable'
    PRIMARY KEY (task_id, ref),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    ts         TEXT NOT NULL,
    event      TEXT NOT NULL,
                -- created | started | progress | blocked | unblocked | delivered |
                -- accepted | rejected | reassigned | cancelled | failed
    actor      TEXT NOT NULL,                    -- agent 名 / 'user' / 'system'
    note       TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_history_task ON task_history(task_id, ts);

CREATE TABLE IF NOT EXISTS user_questions (
    id            TEXT PRIMARY KEY,              -- 'q-0001'
    project       TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    asker         TEXT NOT NULL,                 -- agent 名
    related_task  TEXT,                          -- task_id 或 NULL
    question      TEXT NOT NULL,
    options_json  TEXT,                          -- JSON [{id,label,hint?}] 或 NULL（开放题）
    urgency       TEXT NOT NULL DEFAULT 'normal',
                  -- low | normal | high
    status        TEXT NOT NULL DEFAULT 'pending',
                  -- pending | answered | expired | cancelled
    answer        TEXT,                          -- option.id 或自由文本
    asked_at      TEXT NOT NULL,
    answered_at   TEXT,
    FOREIGN KEY (related_task) REFERENCES tasks(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_q_thread_status ON user_questions(thread_id, status);
```

DB 文件位置：`projects/<project>/memory/tasks.db`。
单连接 + `aiosqlite`（项目已用），WAL 模式。

### 3.2 Dataclass 定义（写到 `task_store.py` 与 `question_store.py`）

```python
# task_store.py
@dataclass
class Task:
    id: str
    project: str
    thread_id: str
    title: str
    brief: str
    assignee: str
    created_by: str
    status: str = "pending"
    priority: str = "normal"
    deadline: str | None = None
    deliverable_kind: str = "markdown"
    deliverable_path: str | None = None
    deliverable_summary: str | None = None
    depends_on: list[str] = field(default_factory=list)   # 反序列化时填充
    context_refs: list[str] = field(default_factory=list)
    retries: int = 0
    max_retries: int = 2
    created_at: str = ""
    updated_at: str = ""
    closed_at: str | None = None
    # 不持久化在 tasks 表的字段
    history: list[dict[str, Any]] = field(default_factory=list)

@dataclass
class TaskHistoryEntry:
    ts: str
    event: str
    actor: str
    note: str | None = None
```

```python
# question_store.py
@dataclass
class UserQuestion:
    id: str
    project: str
    thread_id: str
    asker: str
    question: str
    options: list[dict[str, str]] | None = None  # [{id,label,hint?}]
    related_task: str | None = None
    urgency: str = "normal"
    status: str = "pending"
    answer: str | None = None
    asked_at: str = ""
    answered_at: str | None = None
```

### 3.3 TaskStore / QuestionStore 接口（**仅签名**，body 实施时写）

```python
class TaskStore:
    def __init__(self, db_path: Path, project: str): ...
    async def init_db(self) -> None: ...
    async def create(self, task: Task) -> Task: ...      # 自动分配 id（按 thread_id 自增）
    async def get(self, task_id: str) -> Task | None: ...
    async def list(self, *, thread_id: str, status: str | None = None,
                   assignee: str | None = None) -> list[Task]: ...
    async def update_status(self, task_id: str, *, new_status: str,
                            actor: str, note: str | None = None) -> Task | None: ...
    async def update_progress(self, task_id: str, *, note: str, actor: str) -> Task | None: ...
    async def submit_deliverable(self, task_id: str, *, path: str, summary: str,
                                 actor: str) -> Task | None: ...   # 状态切到 done + 写 history
    async def find_ready_downstream(self, completed_task_id: str) -> list[Task]: ...
    async def list_pending_by_assignee(self, thread_id: str, assignee: str) -> list[Task]: ...
    async def history(self, task_id: str) -> list[TaskHistoryEntry]: ...

class QuestionStore:
    def __init__(self, db_path: Path, project: str): ...
    async def init_db(self) -> None: ...
    async def create(self, q: UserQuestion) -> UserQuestion: ...   # 自动分配 id
    async def get(self, q_id: str) -> UserQuestion | None: ...
    async def list_pending(self, thread_id: str) -> list[UserQuestion]: ...
    async def answer(self, q_id: str, *, answer: str) -> UserQuestion | None: ...
    async def cancel(self, q_id: str, reason: str) -> None: ...
```

---

## 4. 工具规范

### 4.1 工具清单（共 7 个新工具）

| 工具 | 调用者 | 用途 |
|------|--------|------|
| `assign_task` | orchestrator | 派任务给某成员（含依赖） |
| `update_task` | 任意成员（owner） | 更新自己负责的任务状态/进度 |
| `submit_deliverable` | 任意成员（owner） | 交付任务产出物 |
| `list_tasks` | 任意成员 | 查看任务（默认看自己 + 依赖链） |
| `send_message` | 任意成员 | 给同事发消息（自动 CC orchestrator） |
| `ask_user` | 任意成员 | 向用户提问（选择题/开放题） |
| `give_up` | 任意成员（owner） | 放弃任务（触发 orchestrator 接手） |

> 注：MVP-Plus 不实现 `reassign_task` / `cancel_task` / `reject_deliverable`。延后到 v2。

### 4.2 工具签名（实现在 `core/team_tools.py`）

所有工具第一个参数必须是 `project_dir: str`（与现有 platform_tools 保持一致），返回 `str`。

部分工具需要 `thread_id` / `caller_agent`（哪个 agent 调的）—— **由 `_execute_tool` 在调用时注入**，不能让 LLM 传。

#### 4.2.1 assign_task

```python
async def assign_task(
    project_dir: str,
    thread_id: str,           # 由 _execute_tool 注入
    caller_agent: str,        # 由 _execute_tool 注入
    *,
    assignee: str,
    title: str,
    brief: str,
    deadline: str | None = None,             # ISO 字符串或 "明天12:00" 自然语言
    depends_on: list[str] | None = None,
    deliverable_kind: str = "markdown",
    context_refs: list[str] | None = None,
    priority: str = "normal",
) -> str:
    """返回示例：'已派发任务 task-0001 给 调研员（依赖 task-0000，DDL 2026-04-26 12:00）'"""
```

权限：仅 orchestrator 可调用（`caller_agent` 校验）。
副作用：
1. 校验 assignee 存在于 registry
2. 校验 depends_on 任务都存在
3. 写入 tasks 表，分配新 task_id
4. 写 task_history(event='created', actor=caller_agent)
5. 若 depends_on 为空 → status='ready'，否则 status='pending'
6. 触发 WS 广播 `task_event(type='created')`
7. **若 status='ready'**，调用 `router.notify_assignee(task)` 立即唤醒 assignee

执行后的工具结果文本会被 router 写回 `_global_log`，agent 下一轮可见。

#### 4.2.2 update_task

```python
async def update_task(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    task_id: str,
    status: str | None = None,    # in_progress | blocked_on_user
    progress_note: str | None = None,
) -> str:
    """返回：'任务 task-0001 状态已更新为 in_progress'"""
```

权限：仅任务的 assignee 或 created_by 可调。
status 限制：仅允许 `in_progress` / `blocked_on_user`（不允许通过 update_task 改 done/failed，应走 submit_deliverable / give_up）。
副作用：写 history + WS 广播 `task_event(type='updated')`。

#### 4.2.3 submit_deliverable

```python
async def submit_deliverable(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    task_id: str,
    content: str | None = None,           # 直接传入产物文本（markdown/json）
    file_path: str | None = None,         # 已存在的 workspace 内相对路径
    summary: str,                          # 1-2 句产物摘要
) -> str:
    """返回：'已交付 task-0001：workspace/reports/xxx.md（自动唤醒下游 task-0002）'"""
```

权限：仅 assignee。
副作用：
1. content 与 file_path 二选一：若 content，写到 `workspace/<task_id>-<slug>.md`
2. 更新 tasks 表：status='done', deliverable_path, deliverable_summary, closed_at
3. 写 history(event='delivered')
4. **关键**：调用 `task_store.find_ready_downstream(task_id)` 找出 depends_on 全部完成的下游任务，将其 status='ready' 并依次 `router.notify_assignee()`
5. WS 广播 `task_event(type='delivered')` + 每个下游的 `task_event(type='ready')`

#### 4.2.4 list_tasks

```python
async def list_tasks(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    scope: str = "mine",         # mine | all | downstream | blocked
    status: str | None = None,    # 可加状态过滤
) -> str:
    """返回 markdown 表格字符串。"""
```

无副作用，纯查询。

#### 4.2.5 send_message

```python
async def send_message(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    to: list[str],                # 收件人 agent 名（可多个，自动剔重 caller 自己）
    content: str,
    cc: list[str] | None = None,
    related_task: str | None = None,
) -> str:
    """返回：'已发送给 调研员（CC: orchestrator）'"""
```

副作用：
1. 自动 CC orchestrator（除非 caller 就是 orchestrator）
2. **防洪水**：检查 `(caller, to)` 在最近 5 分钟内的消息数，超过 6 → 返回错误并强制 CC orchestrator + 在 metadata 标记 `flood_warning=True`
3. 调用 `router.dispatch_internal(sender=caller_agent, to=to, cc=cc, content=content, metadata={'related_task': related_task})` —— 这是 router 新增的"内部通信"入口，行为与 dispatch 类似但不返回 stream（fire-and-forget）

注：`router.dispatch_internal` 仍走 `_dispatch_inner`，最终事件通过 WebSocket 广播给 UI。

#### 4.2.6 ask_user

```python
async def ask_user(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    question: str,
    options: list[dict[str, str]] | None = None,   # [{id,label,hint?}] 或 None=开放题
    urgency: str = "normal",                        # low | normal | high
    related_task: str | None = None,
) -> str:
    """返回：'已向用户发送提问 q-0001（等待回复中，任务暂停）'"""
```

副作用：
1. 写 user_questions 表
2. 若 related_task 存在 → 把该任务 status 改 `blocked_on_user`，写 history(event='blocked')
3. WS 广播 `user_question` 事件给所有该 thread 的连接
4. **本次工具调用阻塞返回**：caller agent 收到工具结果"等待回复中"后会停止；下次它被唤醒（用户回复后）才能看到答案

#### 4.2.7 give_up

```python
async def give_up(
    project_dir: str,
    thread_id: str,
    caller_agent: str,
    *,
    task_id: str,
    reason: str,
) -> str:
    """返回：'已放弃 task-0001，已通知 orchestrator 接手'"""
```

副作用：
1. 任务 status='failed', closed_at=now, retries+=1
2. 写 history(event='failed', note=reason)
3. WS 广播 `task_event(type='failed')`
4. 自动调 `router.dispatch_internal(sender='system', to=['orchestrator'], content='【任务失败】task-0001 by caller_agent\n原因：...\n请评估：重派/拆解/通知用户')`

### 4.3 LLM 调用工具的 JSON 示例（写入 system prompt）

```
```tool_call
{"tool": "assign_task", "args": {"assignee": "企业岗位调研专员", "title": "完成星火万物 AIGC 岗位调研", "brief": "聚焦公司背景、产研流程、面试真题", "deadline": "2026-04-26 12:00", "deliverable_kind": "markdown"}}
```

```tool_call
{"tool": "submit_deliverable", "args": {"task_id": "task-0001", "content": "# 调研报告\n...", "summary": "公司2018年成立，产研200人..."}}
```

```tool_call
{"tool": "send_message", "args": {"to": ["内容优化顾问"], "content": "我把简历相关章节加重了，你直接接", "related_task": "task-0002"}}
```

```tool_call
{"tool": "ask_user", "args": {"question": "您简历更想突出哪个方向？", "options": [{"id": "tech", "label": "技术深度"}, {"id": "product", "label": "产品视角"}], "urgency": "high", "related_task": "task-0002"}}
```
```

### 4.4 注入到 `_execute_tool`（main.py）

新增分支：

```python
elif tool_name in ("assign_task", "update_task", "submit_deliverable",
                   "list_tasks", "send_message", "ask_user", "give_up"):
    fn = TEAM_TOOL_DISPATCH[tool_name]
    return await fn(
        project_dir=pdir_str,
        thread_id=current_thread_id,    # 需要把 thread_id 传给 _process_tool_calls
        caller_agent=caller_agent,      # 需要把 agent_name 传给 _process_tool_calls
        **args,
    )
```

⚠️ 这要求 `_process_tool_calls` 签名扩展为：
```python
async def _process_tool_calls(text: str, project_dir: Path,
                              thread_id: str, caller_agent: str) -> list[dict]:
```
现有调用点（main.py 697 行 + 779 行）都要补传两个参数。

---

## 5. WebSocket 事件规范

### 5.1 新增事件（server → client）

```typescript
// 任务事件
{ type: "task_event", subtype: "created" | "ready" | "started" | "updated" |
        "delivered" | "blocked" | "unblocked" | "failed" | "cancelled",
  task: Task,                    // 完整 task 对象
  changes?: dict                 // 仅 updated 时存
}

// 用户提问事件
{ type: "user_question", question: UserQuestion }

// 用户答复事件（server 回显，UI 关闭对应卡）
{ type: "user_answer_received", question_id: string, answer: string }

// 内部通信事件（成员发给成员）
{ type: "internal_message", envelope: Envelope }   // 复用现有 envelope_recorded UI 路径
```

### 5.2 新增事件（client → server）

```typescript
// 用户回答某条提问
{ action: "answer_question", question_id: "q-0001", answer: "tech" }
```

> 注：当前 WebSocket 客户端→服务端只有"发消息"一种，需要扩展支持 `action` 字段。建议向后兼容：若有 `action` 字段就走 action 分支，否则走原 chat 分支。

### 5.3 事件广播 Helper

在 main.py 新增：

```python
async def _ws_broadcast(thread_id: str, event: dict) -> None:
    """广播给该 thread_id 当前活跃的 WS 连接（若有）。无连接时静默丢弃，状态已落盘。"""
```

由 `team_tools.py` 间接通过传入的 callback 调用，或在 `_execute_tool` 包装层处理。

---

## 6. REST API 规范

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/threads/{thread_id}/tasks` | 列出任务，支持 query: `status`, `assignee`, `scope` |
| GET | `/api/threads/{thread_id}/tasks/{task_id}` | 单任务详情（含 history） |
| GET | `/api/threads/{thread_id}/questions?status=pending` | 待办收件箱 |
| POST | `/api/threads/{thread_id}/questions/{q_id}/answer` | body: `{answer: "tech"}`，触发 router 唤醒 asker |
| GET | `/api/threads/{thread_id}/workspace/{path:path}` | 下载产物文件（可选，先实现简单版） |

返回格式统一：

```json
{ "ok": true, "data": ... }
{ "ok": false, "error": "..." }
```

---

## 7. UI 组件规范（vanilla JS in `web/index.html`）

### 7.1 新增 CSS 样式块

```css
/* ── Task card (inline in chat stream) ── */
.task-card { border-radius: 10px; border: 1px solid #2a3142; background: #0d1320; padding: 10px 12px; margin: 6px 0; }
.task-card .tc-head { display: flex; gap: 6px; align-items: center; font-size: 12px; color: #94a3b8; }
.task-card .tc-title { color: #e2e8f0; font-weight: 600; margin: 4px 0; }
.task-card .tc-meta { font-size: 11px; color: #64748b; }
.task-card .tc-status-pill { /* pending=灰 / ready=蓝 / in_progress=黄 / done=绿 / failed=红 / blocked=橙 */ }
.task-card .tc-actions button { /* 催办 / 取消 / 详情 */ }

/* ── Kanban overlay ── */
.kanban-toggle { position: fixed; top: 12px; right: 12px; z-index: 50; }
.kanban-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: none; }
.kanban-overlay.open { display: flex; }
.kanban-board { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 24px; overflow: auto; }
.kanban-col { background: #0d1320; border: 1px solid #2a3142; border-radius: 8px; min-height: 200px; }
.kanban-col h3 { /* 列标题 */ }

/* ── Question card ── */
.q-card { border-left: 3px solid #fbbf24; background: #1a1410; padding: 10px 12px; margin: 6px 0; border-radius: 6px; }
.q-card .q-options { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.q-card .q-options button { /* 选项按钮 */ }
.q-card textarea { /* 开放题输入 */ }

/* ── Inbox bell ── */
.inbox-bell { position: relative; }
.inbox-bell .badge { /* 红点 + 数字 */ }
.inbox-drawer { position: fixed; right: 0; top: 0; width: 360px; height: 100vh; background: #0a0e16; border-left: 1px solid #2a3142; transform: translateX(100%); transition: transform .2s; }
.inbox-drawer.open { transform: translateX(0); }
```

### 7.2 新增 JS 函数清单（仅签名，实施时填实现）

```js
// 任务卡
function renderTaskCard(task, subtype)      // 'created'|'ready'|'updated'|'delivered'|'failed'
function appendTaskCard(task, subtype)      // 追加到 msgList
function updateTaskCardInPlace(task)        // 同 task_id 的最新卡更新

// Kanban
function openKanban() / closeKanban()
function renderKanban(tasks)
async function fetchTasks()                 // GET /api/threads/{thread_id}/tasks

// 问题卡
function renderQuestionCard(question)
function appendQuestionCard(question)
async function answerQuestion(qId, answer)  // POST /api/.../answer + 关闭卡片

// 收件箱
function updateInboxBadge(count)
function openInboxDrawer() / closeInboxDrawer()
function renderInboxList(questions)
async function fetchPendingQuestions()      // GET /api/.../questions?status=pending

// WS 事件分流（在 socket.onmessage 主 switch 内新增 case）
//   'task_event'           → updateTaskCardInPlace + 刷 Kanban
//   'user_question'        → appendQuestionCard + updateInboxBadge
//   'user_answer_received' → 关闭对应卡片 + updateInboxBadge
//   'internal_message'     → 复用 appendMessage（已有）
```

### 7.3 顶部工具栏新增

在现有 toolbar 加：

- 🔔 收件箱铃铛（带未读 badge）
- 📋 任务面板入口（`fetchTasks → openKanban`）

---

## 8. Orchestrator 与成员系统提示词规范

### 8.1 Orchestrator 追加段（写入 `main.py::_ORCHESTRATOR_YAML_TMPL` + 各项目现有 yaml）

在现有 instructions 末尾追加：

```
【任务派发协议】（必读）
- 当用户请求复杂任务时，你必须用 assign_task 工具把工作拆给具体成员；
  不允许只在文本里说「我会通知 XX」，那是空话，成员不会收到。
- 有先后依赖时用 depends_on 字段连接；下游任务在上游 submit_deliverable 后会自动唤醒，无需你再发消息。
- 若某成员能力不足/不存在，先用 recruit_fixed 招募，再 assign_task。
- 接到 give_up / 任务失败通知时：评估是重派、拆解、还是通知用户决策。

【内部通信协议】
- 你可以用 send_message 给任何成员发协调信息（自动 CC 你自己，不需手动 CC）。
- 普通成员之间也可以直接 send_message，你会自动收到 CC，必要时介入。

【提问协议】
- 不要替用户做决策。当出现需用户拍板的选择时，用 ask_user，附 options 给出 2-3 个备选。
- 信息缺失时也用 ask_user 而非猜测。

【新增工具示例】
```tool_call
{"tool": "assign_task", "args": {"assignee": "调研员", "title": "...", "brief": "...", "deadline": "2026-04-26 12:00", "deliverable_kind": "markdown"}}
```
```tool_call
{"tool": "list_tasks", "args": {"scope": "all"}}
```
```tool_call
{"tool": "send_message", "args": {"to": ["内容顾问"], "content": "...", "related_task": "task-0002"}}
```
```tool_call
{"tool": "ask_user", "args": {"question": "...", "options": [{"id":"a","label":"..."},{"id":"b","label":"..."}], "urgency": "high"}}
```

【硬性约束 - 违反视为错误】
1. 派发任务时必须一条一条调用 assign_task 工具，禁止用 markdown 列表"列任务"代替；
   每个成员一个 assign_task，不允许 1 条文本描述 5 个任务。
2. 需要用户决策时必须调用 ask_user 工具弹出选项卡，禁止在正文里写 "A. ... B. ..."；
   选项必须放在 ask_user 的 options 字段里。
3. 禁止用 update_project_context 替代 assign_task；
   update_project_context 仅用于更新跨对话的项目背景，不是任务派发渠道。
```

### 8.2 普通成员追加段（追加到所有非 orchestrator 的成员 yaml）

```
【你拥有的协作工具】

```tool_call
{"tool": "list_tasks", "args": {"scope": "mine"}}
```
查看分给你的任务。

```tool_call
{"tool": "update_task", "args": {"task_id": "task-XXXX", "status": "in_progress", "progress_note": "已开始"}}
```
开始/暂停一项任务。

```tool_call
{"tool": "submit_deliverable", "args": {"task_id": "task-XXXX", "content": "你的产物 markdown 全文", "summary": "1-2 句摘要"}}
```
交付任务（系统会自动通知下游）。

```tool_call
{"tool": "send_message", "args": {"to": ["其他成员名"], "content": "...", "related_task": "task-XXXX"}}
```
直接联系同事（orchestrator 自动收到 CC）。

```tool_call
{"tool": "ask_user", "args": {"question": "...", "options": [{"id":"a","label":"选项1"},{"id":"b","label":"选项2"}], "urgency": "normal", "related_task": "task-XXXX"}}
```
向用户提问（任务会暂停等回复）。

```tool_call
{"tool": "give_up", "args": {"task_id": "task-XXXX", "reason": "..."}}
```
你确实搞不定时使用，orchestrator 会接手。

【行为准则】
- 收到任务 envelope 后，先 `update_task(status='in_progress')` 表态接单
- 信息不足时优先 ask_user 或 send_message 找同事，不要瞎猜
- 完成时务必 `submit_deliverable`，不要只回复"已完成"
- 同事之间保持商务简洁，避免无意义客套
- 需要用户决策时必须调用 ask_user，禁止正文写"A. ... B. ..."列表
```

---

## 9. 现状摘要（实施时可直接抄的关键参数）

| 项目 | 值 |
|------|----|
| Python 环境 | conda env `agent-platform`，路径 `D:\Users\60490\.conda\envs\agent-platform\python.exe` |
| Server 启动命令 | `python agent-platform\main.py` |
| 监听端口 | 8765 |
| Tool call 正则 | `r"```tool_call\s*\n(.*?)\n```"` |
| Envelope id 格式 | `msg-NNNN`（`MessageRouter._next_id`，按 thread 自增） |
| Task id 格式（新） | `task-NNNN`（按 thread 自增） |
| Question id 格式（新） | `q-NNNN`（按 thread 自增） |
| 仓库 | `git@github.com:x0chaoren0s/agent-platform.git` 主分支 `main` |
| 已有 commit | `927088f` 初始化 / `28cfaf3` 设计文档 / `cf5ab59` 工具反馈回写 / `5bd0728` 检查点+UI |

---

## 10. 编号执行清单（原子操作，按顺序）

> 状态图例：⬜ 未开始 / 🟡 进行中 / ✅ 完成 / ❌ 阻塞
> 完成一项后改 ✅ 并 commit。

### Phase 1：存储层（commit: `feat(store): ...`）

1. ✅ 创建 `core/task_store.py`：DDL 常量 + `Task` / `TaskHistoryEntry` dataclass + `TaskStore` 类骨架（仅 `__init__` + `init_db` 跑通）
2. ✅ 在 `task_store.py` 实现 `create` / `get` / `list` / `update_status` / `update_progress`
3. ✅ 在 `task_store.py` 实现 `submit_deliverable`（内部完成 status→done + history + 文件写入逻辑分离到 helper）
4. ✅ 在 `task_store.py` 实现 `find_ready_downstream`（核心：上游 task_id 完成 → 找出 depends_on 全部 done 的下游）
5. ✅ 在 `task_store.py` 实现 `history` / `list_pending_by_assignee`
6. ✅ 创建 `core/question_store.py`：DDL（user_questions 部分） + `UserQuestion` dataclass + `QuestionStore` 类完整 CRUD
7. ✅ 修改 `.gitignore` 追加 `projects/*/workspace/`
8. ✅ 创建占位 `projects/Interview/workspace/.gitkeep` 与 `projects/manga/workspace/.gitkeep`
9. ✅ Commit Phase 1：`feat(store): 新增 task_store / question_store + workspace 目录`

### Phase 2：工具层（commit: `feat(tools): ...`）

10. ✅ 创建 `core/team_tools.py`：模块级注释 + 导入 + `TEAM_TOOL_DISPATCH` 字典骨架（值先填 `None`）
11. ✅ 实现 `_get_task_store(project_dir)` 与 `_get_question_store(project_dir)` 单例缓存
12. ✅ 实现 `assign_task`（含权限校验 + assignee 存在性校验 + depends_on 校验）
13. ✅ 实现 `update_task`（含权限校验 + status 白名单校验）
14. ✅ 实现 `submit_deliverable`（含 content 写文件逻辑 + 触发下游唤醒——下游唤醒挪到 router 层调用，本工具仅返回 ready_downstream 列表给上层）
15. ✅ 实现 `list_tasks`（输出 markdown 表格）
16. ✅ 实现 `send_message`（含防洪水 + 自动 CC orchestrator）
17. ✅ 实现 `ask_user`（含 related_task 切 blocked + 写 question_store）
18. ✅ 实现 `give_up`（含触发 orchestrator 接手 envelope）
19. ✅ 填充 `TEAM_TOOL_DISPATCH = {...}`
20. ✅ Commit Phase 2：`feat(tools): 新增团队 7 工具（assign/update/list/submit/send/ask/give_up）`

### Phase 3：Router 集成（commit: `feat(router): ...`）

21. ✅ 在 `core/router.py` 新增 `notify_assignee(task: dict) -> None` 方法：构造 `【新任务】task-XXXX ...` envelope → `dispatch_internal` 给 assignee
22. ✅ 在 `router.py` 新增 `dispatch_internal(sender, to, cc, content, metadata, images=None) -> None` 方法：fire-and-forget 版 dispatch（内部 spawn task）
23. ✅ 在 `team_tools.py::send_message` 内调用 `router.dispatch_internal`（注入 router 引用：通过新加的 `_router_ref` 全局或 setter）
24. ✅ 在 `team_tools.py::submit_deliverable` 完成后，循环调用 `router.notify_assignee(downstream_task)`
25. ✅ 在 `team_tools.py::give_up` 内调用 `router.dispatch_internal` 通知 orchestrator
26. ✅ Router 增加防洪水实现细节：维护 `_recent_msgs: dict[(sender,to_tuple), list[ts]]`，5 分钟窗口 N=6 上限
27. ✅ Commit Phase 3：`feat(router): 工具结果路由到任务事件链 + 自动唤醒下游 + 防洪水`

### Phase 4：API 层（commit: `feat(api): ...`）

28. ✅ 在 `main.py` 新增 `_get_task_store(thread_id)` / `_get_question_store(thread_id)` helper（按当前 _current_project 取）
29. ✅ 在 `main.py` 扩展 `_process_tool_calls` 签名：增加 `thread_id` 与 `caller_agent` 参数
30. ✅ 在 `main.py` 同步修改两处调用点（697 行 POST /api/chat、779 行 WS）传入 thread_id 与 agent_name
31. ✅ 在 `main.py::_execute_tool` 添加 7 个新工具的 elif 分支（调用 `TEAM_TOOL_DISPATCH[tool_name]`，注入 thread_id/caller_agent）
32. ✅ 注入 router 引用到 team_tools：在 `_get_router(thread_id)` 创建 router 后调用 `team_tools.set_router(thread_id, router)`
33. ✅ 在 `main.py` 新增 `_ws_broadcast(thread_id, event)` helper
34. ✅ 在 team_tools 各副作用点调用 `_ws_broadcast`（通过 setter 注入 broadcaster fn）
35. ✅ 在 `main.py` 新增 4 个 REST 端点（§6 列表）
36. ✅ 在 `main.py` WebSocket 主循环扩展：识别 `action` 字段，分流到 `answer_question` 处理（写入 question_store + dispatch_internal 给 asker + 广播 user_answer_received）
37. ✅ Commit Phase 4：`feat(api): 新增任务/提问 REST 端点 + WS 事件广播`

### Phase 5：前端 UI（commit: `feat(web): ...`）

38. ✅ 在 `web/index.html` 增加 §7.1 全部 CSS
39. ✅ 在 toolbar 增加 🔔 收件箱铃铛 + 📋 Kanban 入口按钮
40. ✅ 实现 `renderTaskCard` / `appendTaskCard` / `updateTaskCardInPlace`
41. ✅ 实现 `openKanban` / `closeKanban` / `renderKanban` / `fetchTasks`
42. ✅ 实现 `renderQuestionCard` / `appendQuestionCard` / `answerQuestion`
43. ✅ 实现 `updateInboxBadge` / `openInboxDrawer` / `closeInboxDrawer` / `renderInboxList` / `fetchPendingQuestions`
44. ✅ 在 socket.onmessage 主 switch 中新增 4 个 case：`task_event` / `user_question` / `user_answer_received` / `internal_message`
45. ✅ 在 socket 连接 `init` 事件后调用 `fetchTasks` + `fetchPendingQuestions` 渲染初始状态
46. ✅ Commit Phase 5：`feat(web): 任务卡 + Kanban + 问题卡 + 收件箱铃铛`

### Phase 6：提示词升级（commit: `feat(prompt): ...`）

47. ✅ 修改 `main.py::_ORCHESTRATOR_YAML_TMPL`，按 §8.1 追加任务派发/通信/提问协议段
48. ✅ 修改 `projects/Interview/agents/orchestrator.yaml`，同步追加 §8.1
49. ✅ 修改 `projects/manga/agents/orchestrator.yaml`，同步追加 §8.1
50. ✅ 修改 `projects/Interview/agents/{企业岗位调研专员,面试内容优化顾问,模拟面试官,行程后勤专员}.yaml`，按 §8.2 追加成员协作工具段
51. ✅ 修改 `projects/manga/agents/{script_writer,visual_director}.yaml`，按 §8.2 追加成员协作工具段
52. ✅ Commit Phase 6：`feat(prompt): orchestrator 与成员提示词追加任务/通信/提问协议`

### Phase 7：文档收尾（commit: `doc(readme): ...`）

53. ✅ 修改 `README.md` 「主要功能」列表，追加：
    - 任务派发与依赖驱动的自动接力
    - TaskBoard（聊天内嵌任务卡 + Kanban）
    - 成员之间直接通信（自动 CC orchestrator）
    - ask_user 主动提问 + 待办收件箱
    - 项目工作区（`workspace/` 存放交付物）
54. ✅ 在 `README.md` 「目录结构」段说明新文件 `tasks.db` 与 `workspace/`
55. ✅ 在本文档 §10 各步骤改 ✅ 收尾（也可在每步完成时滚动改）
56. ✅ Commit Phase 7：`doc(readme): 同步 MVP-Plus 新功能说明`

---

## 11. 阶段性验证场景

### 11.1 Phase 1-2 单元 smoke test（不依赖 LLM）

写一个 `scripts/smoke_test_tasks.py`（一次性脚本，跑完可不提交）：

```python
# 期望流程：
# 1. 初始化 TaskStore
# 2. create(task A) → task-0001, status=ready (无依赖)
# 3. create(task B, depends_on=[task-0001]) → status=pending
# 4. submit_deliverable(task-0001, content='hello') → status=done
# 5. find_ready_downstream(task-0001) → [task B] 且 task B status 已切 ready
# 6. list_pending_by_assignee 校验
# 7. QuestionStore: create + answer + list_pending
```

### 11.2 Phase 3-4 端到端 mock test

curl 模拟 LLM 输出工具调用：

```bash
# 调一个伪造 tool_call，验证 task 被创建 + WS 广播 task_event
curl -X POST http://localhost:8765/api/chat ...
```

### 11.3 Phase 5-6 真实场景验证（用户操作）

- 启动 server，前端连入 Interview 项目
- 用户："帮我准备 4/28 的星火万物 AIGC 岗位面试"
- 期望观察：
  1. orchestrator 调用 4 个 assign_task（聊天流出现 4 张任务卡）
  2. 顶部 Kanban 出现 4 张卡片
  3. 调研员（无依赖任务）被自动唤醒，开始工作
  4. 调研员可能调 ask_user → UI 弹问题卡 + 铃铛红点
  5. 用户回答后，调研员继续，最终 submit_deliverable
  6. 内容顾问（依赖调研报告）被**自动唤醒**接手
  7. 整个过程中用户从未需要主动 @ 任何成员

### 11.4 完成定义（DoD）

MVP-Plus 视为完成的硬性标准：

- [ ] orchestrator 用嘴说"我已通知 X"的次数 = 0（全部走 assign_task）
- [ ] 至少一次端到端跑通：用户布置 → 自动接力 → 全部 done，中间用户仅响应 ask_user
- [ ] Kanban 状态实时更新
- [ ] 至少一次出现成员→成员的 send_message（带 CC orchestrator）
- [ ] 防洪水：人为构造同一对成员 7 条消息 → 第 7 条被拒/警告

---

## 12. 风险与回滚

### 12.1 已识别风险

| 风险 | 缓解 |
|------|------|
| 成员之间客套刷屏 | §8.2 行为准则要求"商务简洁"；§4.2.5 防洪水 |
| `dispatch_internal` 递归调用栈失控 | 复用 `_dispatch_inner` 的 `_depth` 上限（=3）；team_tools 调用时显式传 `_depth=0` |
| 工具调用结果太长撑爆下一轮 prompt | submit_deliverable 不在工具结果里返回完整内容，只返回路径+摘要 |
| TaskStore 与 chat_log 时序错位 | 所有 task 状态变更先写库再广播 WS；UI 拉取时以库为准 |
| 用户离线时 ask_user 永远 pending | MVP 接受此现状；v3 加超时催办 |
| Workspace 路径越界 | 所有写文件 path 必须 `Path(workspace_root) / sanitized_name` 且 `is_relative_to(workspace_root)` |

### 12.2 回滚策略

- 每个 phase 独立 commit → 任何 phase 出问题：`git revert <phase commit>` 回退即可
- TaskStore SQLite 文件删除即重置（项目级隔离）
- 提示词回滚：恢复 `_ORCHESTRATOR_YAML_TMPL` 与各项目 yaml 即可

### 12.3 与已有功能的兼容性

- 现有 `recruit_fixed` / `dismiss_member` / `recruit_temp` / `update_project_context` / `list_team` 不动
- 现有"escalation 信号 `【需要协助:cap:desc】`"机制不动，与新工具并存
- 现有 checkpoint / 流式 UI / 工具反馈回写不动

---

## 13. 完成后下一步

MVP-Plus DoD 达成后：

1. 关闭本文档（标记 §10 全部 ✅）
2. ENTER REVIEW MODE，对照 §11.4 逐条核验
3. 启动新一轮 RIPER：基于真实运行数据决定是否进入 v2（失败容错）或先优化 v1 已发现的问题

---

## 附录 A：跨 session 工作交接模板

新 session 接手时按此格式自检：

```
[当前进度自检]
- 已读：docs/team-mvp-plus.md ✅
- 已读：docs/team-mvp-plus-todolist.md ✅
- git log -5：
    <粘贴最近 5 条 commit>
- git status：
    <粘贴当前未提交改动>
- §10 中 ✅ 计数：N / 56
- 当前应执行步骤：第 X 步「...」
- 阻塞？无 / 有：...

[本 session 计划完成步骤范围]
第 X ~ 第 Y 步
```

---

## 14. Phase 6 实施偏差与修补任务（2026-04-25 REVIEW 发现）

### 14.1 状态

- §10 全部 56 步状态 = ✅（commit `12375a4` 收尾）
- §11.4 DoD 验证 = ❌ **未通过**

### 14.2 测试现场

- 测试项目：`projects/opc/`
- 对话线程 ID：`main-t6c4z`，chat_log: `projects/opc/chat_log/main-t6c4z.json`
- 5 名成员已通过 `recruit_fixed` 招募完成（`msg-0004` ~ `msg-0005`）
- 用户问"我们从零开始第一步做什么"后，orchestrator 行为如下：

| envelope | 应做 | 实际 |
|---|---|---|
| `msg-0007` | 调 `ask_user(options=[...])` 弹问题卡 | 在文本里写 "A. ... B. ... C. ..." |
| `msg-0009` | 调 5 次 `assign_task` | 调了 1 次 `update_project_context`（旧工具）+ 文本列 5 条任务 |

**0 个 `assign_task` / 0 个 `ask_user` 工具调用**——成员从未被唤醒。

### 14.3 根因

`projects/opc/agents/orchestrator.yaml` 第 50-66 行的 §8.1 协议段**只有文字描述、缺所有 tool_call JSON 示例**。
对照：第 22-40 行老工具（`recruit_fixed` 等）每个都有 JSON 范例 → 模型对老工具"会抄作业"，对新工具"没教材"。

Phase 6（commit `0c1e459`）实施时把文档 §8.1 的"新增工具示例"代码块**省略了**，仅保留了文字描述。同样的简化也发生在所有项目的 yaml 与 `main.py::_ORCHESTRATOR_YAML_TMPL`。

### 14.4 修补任务（按 RIPER：先 PLAN 修订本节，再 EXECUTE 重刷 yaml）

#### 选项 B（推荐先做，验证根因）

55B-1. ~~⬜~~ ✅ **仅修补 `projects/opc/agents/orchestrator.yaml`**（跳过，直接执行 A）
55B-2. ~~⬜~~ ✅ 重启服务
55B-3. ~~⬜~~ ✅ 冒烟测试
55B-4. ~~⬜~~ ✅ `tool_results: ask_user` 已出现，链路正常
55B-5. ~~⬜~~ ✅ 通过 → 进入选项 A

#### 选项 A（B 验证根因后批量修）

55A-1. ✅ PLAN 模式更新文档 §8.1 / §8.2，把禁令补进规范
55A-2. ✅ 修补 `main.py::_ORCHESTRATOR_YAML_TMPL`
55A-3. ✅ 修补 `projects/Interview/agents/orchestrator.yaml`
55A-4. ✅ 修补 `projects/manga/agents/orchestrator.yaml`
55A-5. ✅ 批量修补所有成员 yaml（Interview 4 个 + manga 2 个 + opc 5 个）
55A-6. ✅ 完成端到端重测，`tool_results: ask_user` 返回正常
55A-7. ⬜ Commit：`fix(prompt): 补全新工具 tool_call 示例与强禁令，修复 DoD 偏差`（待专用 session 提交）

### 14.5 三条必须加进 prompt 的强禁令（核心补丁内容）

```
【硬性约束 - 违反视为错误】
1. 派发任务时必须一条一条调用 assign_task 工具，禁止用 markdown 列表"列任务"代替；
   每个成员一个 assign_task，不允许 1 条文本描述 5 个任务。
2. 需要用户决策时必须调用 ask_user 工具弹出选项卡，禁止在正文里写 "A. ... B. ..."；
   选项必须放在 ask_user 的 options 字段里。
3. 禁止用 update_project_context 替代 assign_task；
   update_project_context 仅用于更新跨对话的项目背景，不是任务派发渠道。
```

### 14.6 已知未验证项（修补完后顺手验证）

- DoD #3 Kanban 实时更新（无任务时无法验证）
- DoD #4 成员↔成员 send_message + CC orchestrator
- 防洪水阈值：人为构造同对成员 7 条 send_message → 第 7 条被拒/警告

### 14.7 关键事实速查（新 session 不要遗漏）

- Server 启动：`D:\Users\60490\.conda\envs\agent-platform\python.exe D:\projects\aiMoney\agent-platform\main.py`
- 端口 8765，kill 旧进程：`Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`
- 测试项目：`projects/opc/` 已存在，无需重建
- 修补一个 yaml 后无需重启（registry 有 watchdog 热加载，但服务端逻辑/模板修改需要重启）

