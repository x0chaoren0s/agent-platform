# MVP-Plus++ Todolist（动作风险分级 + 任务级真心跳 + 团队放假）

> 本文档是 RIPER-5 PLAN 模式产物，PLAN 阶段**不写一行代码**。
> 每一步是 EXECUTE 阶段的原子操作，可被多个 session 接力执行。
>
> 上一里程碑：`mvp-plus-milestone-20260425`（commit `3ca04b7`）
> 主分支：`main`
> 本任务分支：暂不切，沿用 `main`
> 任务文件：`.tasks/2026-04-25_2_mvp-plus-plus.md`

---

## 0. 路线图概览

### 0.1 形态收敛过程

8 个原始问题 → 3 + N → **2 个核心机制 + N 个补丁**（INNOVATE 阶段经用户两轮反驳收敛）。

被裁掉的"机制 ①（信号信封 / 测试演练）"理由：用户反驳"区分业务/测试是平台开发者世界观，agent 不该知道"——压测应用业务化内容、用 sandbox 项目（不写代码，仅写 docs/testing-conventions.md）。

### 0.2 最终方案锁定

**核心机制 ① — 动作风险分级 + 服务器拦截**
- 红色清单：`dismiss_member` / `recruit_fixed` / `update_project_context`
- 拦截策略：服务器返回错误字符串，要求先 `ask_user` 拿到用户确认；裸调即拒
- 配套 prompt：所有 orchestrator yaml 加"协调三连"段（send_message → update_task → ask_user → 最后才 dismiss）
- 治：A3、D1

**核心机制 ② — 任务级真心跳 + 团队放假开关**
- 心跳周期：30 秒
- 沉默阈值（按 task.priority 分档）：`high = 10min` / `normal = 30min` / `low = 2h`
- 硬去抖：同一任务连续 advisory 间隔 ≥ 10 分钟（额外保险，防 last_activity 未刷新时反复打扰）
- advisory 形态：事件型（注入 orchestrator 下一轮 prompt 的 inbox），不代行动
- 用户控制：thread 级 `is_paused` 开关，UI 按钮在左侧团队栏顶部 `<h2>团队成员</h2>` 旁
- 治：B1、B2、D2

**N 个独立小补丁**
- A2：tool 失败返回字符串末尾自动追加"建议立即 `list_tasks` 自检"
- C2：`team_tools` 函数捕获 schema 错（`TypeError`、缺字段、错字段名），返回带可读 hint 的错误
- C1：空气泡 UI（纯前端 fix，与本计划同批做）
- A1：写 `docs/testing-conventions.md`，零代码

### 0.3 阶段总览

| Phase | 主题 | 步骤数 | 是否阻塞 EXECUTE 顺序 |
|---|---|---|---|
| 0 | 数据模型与基础设施 | 4 | 阻塞 1 / 2 |
| 1 | 核心机制 ①（红色拦截） | 4 | 不阻塞 2 |
| 2 | 核心机制 ②（心跳 + 暂停） | 6 | 不阻塞 1 |
| 3 | 协调三连 prompt | 2 | 不阻塞 |
| 4 | N 个独立补丁 | 4 | 不阻塞 |
| 5 | 端到端验证 DoD | 3 | 必须最后 |
| 6 | 文档与里程碑 | 2 | 必须最后 |

合计 25 步。Phase 1 / 2 / 3 / 4 之间彼此独立，可并行（多 session 接力时尤为有用）。

---

## 1. Phase 0 — 数据模型与基础设施

### §1.1 文件级改动总览

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `core/task_store.py` | ADD method | `list_silent_tasks(thread_id, now, thresholds_seconds: dict[str,int]) -> list[Task]` |
| `core/task_store.py` | ADD table | `task_advisory(task_id, last_advisory_ts)`，简单 KV，记录硬去抖 |
| `core/task_store.py` | ADD method | `mark_advisory_sent(task_id, ts)`、`get_last_advisory_ts(task_id) -> str|None` |
| `core/conversation_store.py` | ADD column | `is_paused INTEGER DEFAULT 0`（ALTER TABLE 兼容老库） |
| `core/conversation_store.py` | ADD methods | `set_paused(thread_id, paused: bool)`、`is_paused(thread_id) -> bool` |
| `core/router.py` | REMOVE | 删除 `_RECENT_MSGS` / `_check_flood` 副本 |
| `core/team_tools.py` | REFACTOR | 删除 `_RECENT_MSGS`、改 `send_message` 调 `router._check_flood`（router 是单一来源） |

### §1.2 错误处理策略

- 所有 `ALTER TABLE` 加 `try/except aiosqlite.OperationalError`（duplicate column 兼容老库）
- `list_silent_tasks` 仅返回符合所有条件的任务，永不抛异常；返回空列表 = 当下没有沉默任务
- `mark_advisory_sent` 用 INSERT OR REPLACE，幂等

### §1.3 步骤清单（Phase 0）

```
实施清单：
1. core/task_store.py: 在 _SCHEMA 字符串末尾追加 task_advisory 表 DDL；签名：
   CREATE TABLE IF NOT EXISTS task_advisory (
       task_id TEXT PRIMARY KEY,
       last_advisory_ts TEXT NOT NULL
   );
   不需要 FK（advisory 记录任务删除后无意义自然过期）。

2. core/task_store.py: 新增方法
   async def list_silent_tasks(self, thread_id, now_iso, thresholds_seconds) -> list[Task]
   - 入参 thresholds_seconds 形如 {"high":600,"normal":1800,"low":7200}
   - SQL: SELECT * FROM tasks
              WHERE thread_id=?
                AND status IN ('ready','in_progress','blocked_on_user')
                AND (julianday(?) - julianday(updated_at)) * 86400
                    >= CASE priority WHEN 'high' THEN ? WHEN 'low' THEN ? ELSE ? END
   - 仅返回 Task 主体，不需要 history / depends_on（心跳不需要）
   - 顺便联表 task_advisory 排除 last_advisory_ts 在 advisory_min_gap_seconds 内的

   附：方法签名建议为
   async def list_silent_tasks(
       self, *, thread_id: str, now_iso: str,
       thresholds_seconds: dict[str, int],
       advisory_min_gap_seconds: int,
   ) -> list[Task]

3. core/task_store.py: 新增两个方法
   async def mark_advisory_sent(self, task_id: str, ts_iso: str) -> None
   async def get_last_advisory_ts(self, task_id: str) -> str | None
   均直接读写 task_advisory 表。

4. core/conversation_store.py: init_db() 中追加 ALTER TABLE 兼容老库的语句：
   try: await db.execute("ALTER TABLE conversations ADD COLUMN is_paused INTEGER DEFAULT 0")
   except aiosqlite.OperationalError: pass  # 已存在
   并新增方法：
   async def set_paused(self, thread_id: str, paused: bool) -> bool
   async def is_paused(self, thread_id: str) -> bool
   set_paused 返回是否找到 thread；is_paused 默认 False（未找到 thread 视为未暂停）。

5. core/router.py: 删除 _check_flood 与 _recent_msgs；改为暴露统一的：
   def check_flood(self, sender: str, to: list[str]) -> bool  （public，被 team_tools 调用）
   或保留 _check_flood 但导出给 team_tools 使用（任选其一，关键是单一实现）。

6. core/team_tools.py: 删除文件顶部的 _RECENT_MSGS / _FLOOD_WINDOW_SECONDS / _FLOOD_LIMIT；
   send_message 中改为调用 router.check_flood(sender, clean_to) 判断
   （router 已通过 set_router 注入到 _ROUTERS dict，可直接取）。
   语义保持：限流时返回相同的中文错误字符串。

7. （冒烟）写或扩展 scripts/smoke_test_tasks.py：
   - 新增一节验证 list_silent_tasks：插入 3 个不同 priority、不同 updated_at 的 task，
     调 list_silent_tasks 验证按阈值正确筛选；mark_advisory_sent 后再调验证去抖生效。
   - 新增一节验证 conversation_store.set_paused / is_paused 的状态切换。
```

---

## 2. Phase 1 — 动作风险分级与服务器拦截

### §2.1 设计要点

**红色操作**：`dismiss_member`、`recruit_fixed`、`update_project_context`

**拦截契约**：
- 调用方（orchestrator）必须**先**调一次 `ask_user`，question 文本里包含特定 marker `[[confirm:<action>:<target>]]`，且用户回答 `yes`
- 平台在执行红色操作前查 `question_store`，找最近 60 秒内同 thread / 同 marker 的 answered=yes 的问题；找到则放行；找不到则拒绝
- 拒绝时返回的错误字符串**显式教 agent 该怎么 ask_user**（含示例 marker），让 agent 一轮就能学会

**Marker 格式**（约定，不强制 schema 校验，但 prompt 中要教）：
- 解雇成员：`[[confirm:dismiss:<agent_name>]]`，例：`[[confirm:dismiss:内容选题策划专员]]`
- 招募固定：`[[confirm:recruit:<agent_name>]]`，例：`[[confirm:recruit:数据分析师]]`
- 覆盖项目背景：`[[confirm:context:rewrite]]`

**用户回答规约**：
- 任何 `answer == "yes"` 视为通过（兼容 options 和自由文本）
- options 卡片应包含 `{"id":"yes","label":"确认"}` 和 `{"id":"no","label":"取消"}`

### §2.2 文件级改动

| 文件 | 改动 |
|---|---|
| `core/platform_tools.py` 或新建 `core/red_actions.py` | ADD `async check_red_action_confirm(question_store, thread_id, action, target, max_age_seconds=60) -> bool` |
| `main.py` | MODIFY `_execute_tool` 的 dismiss / recruit / update_project_context 三个分支，先调 confirm 检查 |
| `core/question_store.py` | （已有）`list_pending` / `get` 已够用；可加一个 `find_recent_answered_with_marker(thread_id, marker, since_ts)` 加速查找 |

### §2.3 错误处理与边界

- 找不到 confirm → 返回标准格式：`错误：本操作（<action>）属于不可逆动作，必须先经用户确认。请按以下示例发起 ask_user：\n```tool_call\n{...含 [[confirm:dismiss:X]] 的 ask_user...}\n```\n等用户回答"yes"后再次执行本动作。`
- confirm 已存在但回答不是 yes → 返回 `用户已拒绝该动作（answer="<X>"），请勿重试`
- confirm 超过 60 秒 → 视为过期，按"找不到 confirm"处理（防止陈年同意被复用）

### §2.4 步骤清单（Phase 1）

```
实施清单：
8. 新增 core/red_actions.py 模块（为防止 platform_tools.py 膨胀），导出：
   RED_ACTIONS = {"dismiss_member","recruit_fixed","update_project_context"}
   async def check_confirm(qstore, thread_id, action, target, max_age_seconds=60) -> tuple[bool, str]
       returns (is_confirmed, reason_if_not)
   实现逻辑：用 list_pending（已有）+ 历史扫描 + marker 字符串匹配；
   也可在 question_store 加一个新方法 find_recent_answered_with_marker(thread_id, marker, since_ts)。
   推荐路径：在 question_store 加该方法（步骤 9），check_confirm 仅做 marker 拼装与时窗判断。

9. core/question_store.py: 新增方法
   async def find_recent_answered_with_marker(
       self, thread_id: str, marker: str, since_ts_iso: str
   ) -> UserQuestion | None
   SQL: SELECT * FROM user_questions
            WHERE thread_id=? AND status='answered' AND answered_at >= ?
              AND question LIKE ? -- '%[[confirm:dismiss:X]]%'
            ORDER BY answered_at DESC LIMIT 1

10. main.py::_execute_tool 中：
    - 在 elif tool_name == "dismiss_member" 分支前，加一段：
        从 args 中取 name → marker = f"[[confirm:dismiss:{name}]]"
        is_ok, reason = await check_confirm(qstore, thread_id, "dismiss", name)
        if not is_ok: return reason
    - 在 elif tool_name == "recruit_fixed" 分支前，加同样逻辑（marker = f"[[confirm:recruit:{name}]]"）
    - 在 elif tool_name == "update_project_context" 分支前，加同样逻辑（marker = "[[confirm:context:rewrite]]"）
    需要预先获取 qstore = await _get_question_store(...)（参考 team_tools 中的获取方式）。

11. main.py::_ORCHESTRATOR_YAML_TMPL: 新增"红色操作协议"段（PLAN 阶段不写示例 JSON 内容；
    EXECUTE 阶段编写时格式参照 §8 prompt 模板规范）。
    要点：
    - 列出 3 个红色操作
    - 教标准两步：先 ask_user(含 [[confirm:...]] marker) → 等用户 yes → 再执行原工具
    - 给出每个红色操作各 1 个 ask_user 的 tool_call JSON 示例
    - 强禁令：未拿到用户 yes 不得直接调红色操作，否则视为错误
```

---

## 3. Phase 2 — 任务级真心跳与团队放假

### §3.1 心跳调度器架构

新建 `core/heartbeat.py`，导出：

```
class HeartbeatScheduler:
    def __init__(self, *, interval_seconds: int = 30,
                 thresholds: dict[str,int] = {"high":600,"normal":1800,"low":7200},
                 advisory_min_gap_seconds: int = 600,
                 router_lookup: Callable[[str], MessageRouter | None],
                 task_store_lookup: Callable[[str], Awaitable[TaskStore]],
                 conversation_store: ConversationStore,
                 broadcaster: Callable | None = None)
    async def start(self) -> None        # asyncio.create_task wraps _loop
    async def stop(self) -> None         # cancel + await
    async def _loop(self) -> None        # while True: await asyncio.sleep(interval); await _tick()
    async def _tick(self) -> None        # 单次扫描所有活跃 thread
    async def _scan_thread(self, thread_id: str, project_dir_str: str) -> None
```

**`_tick` 流程**：
1. 列出所有活跃 thread（从 `_ROUTERS` 取，或从 conversation_store.list_by_project 跨项目扫）
2. 对每个 thread：
   - 若 `conversation_store.is_paused(thread_id)` → skip
   - `silent = await task_store.list_silent_tasks(thread_id, now_iso, thresholds, advisory_min_gap_seconds)`
   - 若 silent 非空：
     - 拼一条 advisory 文本（含每个 silent task 的 id/title/assignee/已沉默分钟数）
     - 通过 `router.record_system_advisory(...)`（步骤 14 新增）写入 orchestrator 的 inbox
     - 对每个 silent task 调 `task_store.mark_advisory_sent(task_id, now_iso)` 更新去抖
     - 通过 `_BROADCASTER` 推一条 `{"type":"heartbeat","silent_count":N,"thread_id":...}` 给前端

### §3.2 与 main.py 启动/关闭挂接

- 在 `@app.on_event("startup")` 创建并启动 HeartbeatScheduler
- 在 `@app.on_event("shutdown")` 调用 stop()
- HeartbeatScheduler 单例，跨所有 thread 工作
- router_lookup 从 `team_tools._ROUTERS` 拿；task_store_lookup 复用 `team_tools._get_task_store`
- 注意：心跳必须能拿到 `project_dir`，需要扩展 `_ROUTERS` 的注册时同时记 `project_dir`，或新增 `_THREAD_PROJECT_DIR: dict[str,str]`

### §3.3 团队放假 API

| METHOD | PATH | BODY | 返回 |
|---|---|---|---|
| `POST` | `/api/threads/{thread_id}/pause` | `{}` | `{"ok":True,"is_paused":True}` |
| `POST` | `/api/threads/{thread_id}/resume` | `{}` | `{"ok":True,"is_paused":False}` |
| `GET` | `/api/threads/{thread_id}/status` | — | `{"is_paused":bool, "silent_task_count":int}` |

WebSocket 广播：暂停/恢复后向所有该 thread 的 client 推 `{"type":"thread_paused","is_paused":bool}`。

### §3.4 前端按钮

位置：`web/index.html` 第 422 行 `<h2>团队成员</h2>` 旁加按钮组件
- 状态文案：未暂停时 `▶ 团队工作中`（绿点），暂停时 `⏸ 团队已暂停`（黄点）
- 点击切换；调相应 `/pause` / `/resume` 接口
- 收到 `thread_paused` WS 事件时同步状态
- 心跳活动时（收到 `heartbeat` 事件）顶部铃铛/小图标短闪一下，可选

### §3.5 步骤清单（Phase 2）

```
实施清单：
12. 新建 core/heartbeat.py：实现 HeartbeatScheduler 类（构造 + start/stop + _loop + _tick + _scan_thread）。
    - _loop 用 try/except asyncio.CancelledError 优雅退出
    - 每次 _tick 用 try/except 兜底（一个 thread 出错不影响其他 thread）
    - 日志用 logging.getLogger("heartbeat")，每次 _tick 仅在 silent_count>0 时 INFO，否则 DEBUG

13. core/team_tools.py: 增加 _THREAD_PROJECT_DIR: dict[str,str]，
    set_router(thread_id, router) 时同步记 project_dir；新增 get_project_dir(thread_id)。
    或在 router 上挂 project_dir 属性（任选其一）。

14. core/router.py: 新增方法
    def record_system_advisory(self, to_agent: str, text: str, metadata: dict|None=None) -> dict|None
    构造 Envelope(sender="platform", to=[to_agent], cc=[], content=text,
                  metadata={"system_advisory":True, **(metadata or {})})
    append + flush + 返回 envelope.to_dict()。

15. main.py: @app.on_event("startup") 创建 HeartbeatScheduler 单例 _heartbeat_sched 并 start()；
    @app.on_event("shutdown") 调 stop()。
    需要在文件顶部加 from core.heartbeat import HeartbeatScheduler。

16. main.py: 新增 3 个 REST 端点 /api/threads/{thread_id}/pause、/resume、/status
    - pause/resume 调 _conv_store.set_paused(thread_id, True/False)
      并 _broadcast_to_thread({"type":"thread_paused","is_paused":...})
    - status 调 _conv_store.is_paused + heartbeat scheduler 的最近一次 silent_count 缓存
      （scheduler 应在 _scan_thread 中维护一个 dict[thread_id]→last_silent_count）

17. web/index.html: 在 <h2>团队成员</h2> 旁追加暂停/继续按钮 + 状态点。
    - 按钮 id="thread-pause-btn"，初始状态从 GET /api/threads/{tid}/status 拉
    - WebSocket onmessage 增加 "thread_paused" 类型分支
    - CSS 复用现有 var(--accent) / var(--muted)

（可选）增强：
18*. 心跳 advisory 注入时，给前端推送 {"type":"heartbeat","silent_tasks":[...]}，
     前端可在收件箱铃铛上加红点提示，但不在本 PLAN 强制实施。
```

---

## 4. Phase 3 — 协调三连 prompt 升级

### §4.1 协调三连段（标准文案）

要塞进 orchestrator yaml 与 `_ORCHESTRATOR_YAML_TMPL`：

```
【协调升级路径（必须按顺序尝试，禁止跳过）】
当成员沉默、表现异常或任务受阻时，按下面顺序依次尝试，前一步无效再升级：
1. send_message：先发一条简短消息询问"是否遇到困难、需要什么"
2. update_task：在任务上加 progress_note，记录你已介入协调
3. ask_user：若两步无果，弹问题卡让用户决策（含 options：等待 / 替换成员 / 调整任务）
4. dismiss_member：仅在用户明确选择后执行，且必须先走 §红色操作协议 拿到 [[confirm:dismiss:X]]

禁止跨步：禁止在没走完前 3 步的情况下直接 dismiss / 重派 / 重大调整。
```

### §4.2 步骤清单（Phase 3）

```
实施清单：
18. main.py::_ORCHESTRATOR_YAML_TMPL: 在末尾"【硬性约束】"段之前插入"【协调升级路径】"段
    + "【红色操作协议】"段（步骤 11 已规划同一个模板更新位置，可合并执行）。

19. 批量更新所有现存 orchestrator yaml：
    - projects/Interview/agents/orchestrator.yaml
    - projects/manga/agents/orchestrator.yaml
    - projects/opc/agents/orchestrator.yaml
    每个文件追加协调升级路径 + 红色操作协议两段（与模板保持一字不差）。
    成员 yaml 不动（成员不需要这两段）。
```

---

## 5. Phase 4 — N 个独立小补丁

### §5.1 步骤清单（Phase 4）

```
实施清单：
20. 补丁 A2（工具失败附自检建议）：
    main.py::_execute_tool 末尾的 try/except TypeError 块（已有）改为：
    捕获后返回字符串末尾追加："\n建议立即调用 list_tasks(scope='mine') 查看当前任务实际状态，
    避免基于错误假设继续推进。"
    扩展捕获到所有 team_tools 工具，不只是 TypeError——
    将整个 if/elif tool_name in TEAM_TOOL_DISPATCH 分支用 try/except Exception as exc 包裹，
    针对 Exception 也返回 "工具调用异常（{tool_name}）：{exc}\n建议立即 list_tasks 自检。"

21. 补丁 C2（schema 错给 hint）：
    core/team_tools.py 中给 assign_task / update_task / submit_deliverable 等
    必填字段较多的函数加显式校验头：
    - assign_task: 在函数体最前 if not title or not brief: return "错误：assign_task 必须包含 title 与 brief 字段，请检查 args"
    - submit_deliverable: 已有 content/file_path 二选一校验，可加更友好提示
    - update_task: 已有 status 校验
    保持现有 TypeError 兜底（已在补丁 A2 中处理），但优先在工具入口给出语义化 hint。

22. 补丁 C1（空气泡 UI）：
    web/index.html::appendMessage（约第 1145 行）：
    - 当 cleaned 为空但 blocks.length > 0 时，不渲染 bubble div，
      或将 bubble class 改为 'bubble bubble-tools-only' + 用 CSS 隐藏空 bubble
    - 并把 toolCallsHtml 直接挂在 msg-meta 行下方（视觉上变成"meta + 工具折叠"两行结构，无空气泡）
    保持非空 cleaned 时的渲染不变。
    streaming 渲染（splitStreamingToolCalls / startStreamBubble 第 1410-1411 行）同步处理：
    - 当 mainText 为空且只有 toolBlocks 时，stream-{name} 元素不显示

23. 补丁 A1（写文档不写代码）：
    新建 docs/testing-conventions.md，内容大纲（PLAN 不写正文，EXECUTE 时按下表写）：
    - § 为什么压测要用业务化内容
    - § 推荐方法：开 sandbox 项目、用真实任务话术（不要 flood-1/flood-2/...）
    - § 反例：opc 项目 main-t6c4z 线程的"内容选题策划专员被误开除"事件复盘
    - § 检查清单：压测前 / 压测中 / 压测后
```

---

## 6. Phase 5 — 端到端验证 DoD

### §6.1 DoD 硬性标准（MVP-Plus++ 视为完成）

- [ ] 红色拦截：在 opc 测试中，orchestrator 直接调 `dismiss_member` 不带 confirm marker → 被服务器拒绝并返回标准教学式错误
- [ ] 红色放行：orchestrator 先调 `ask_user(question="...[[confirm:dismiss:X]]...", options=[yes/no])` → 用户答 yes → 再调 dismiss → 成功执行
- [ ] 心跳 advisory：派一个 `priority=high` 任务，10 分钟不动 → orchestrator 的 inbox 中应出现 `system_advisory` envelope
- [ ] 心跳去抖：advisory 触发后 10 分钟内同任务不应再触发第二条
- [ ] 团队暂停：点击"暂停"按钮 → 之后 30 秒心跳应跳过该 thread（日志可见）；点击"继续" → 心跳恢复
- [ ] 协调三连：orch 收到给 task 的 give_up 通知后，应先 send_message 询问 → update_task → ask_user，而非直接 dismiss
- [ ] 补丁 A2/C2：人为构造缺字段的 assign_task → 错误信息含 "list_tasks 自检" 与字段名 hint
- [ ] 补丁 C1：在 UI 上观察成员仅工具调用的消息 → 不应出现"空气泡"
- [ ] 文档 A1：`docs/testing-conventions.md` 已落盘并被 README 链接

### §6.2 步骤清单（Phase 5）

```
实施清单：
24. 写或扩展 scripts/smoke_test_tasks.py，覆盖：
    a. list_silent_tasks 阈值分档与去抖
    b. conversation_store.set_paused / is_paused
    c. red_actions.check_confirm 的过期/找不到/yes 三种返回
    跑 python scripts/smoke_test_tasks.py 通过 → 进入端到端

25. 端到端验证：
    a. 重启服务（kill 8765 旧进程 + 启动 main.py）
    b. 在 opc 项目按 §6.1 DoD 9 项逐项跑通；每项失败回 PLAN 修订
    c. 截图 + 关键 envelope id 留证

26. 回填验证记录：
    把验证结果（每项 ✅/❌ + 证据）写到本文档 §10「DoD 验证记录」（EXECUTE 阶段补）
    并同步更新 .tasks/2026-04-25_2_mvp-plus-plus.md 的"任务进度"段。
```

---

## 7. Phase 6 — 文档与里程碑

### §7.1 步骤清单（Phase 6）

```
实施清单：
27. 更新 README.md：
    - 在「核心特性」追加：动作风险分级、任务级真心跳、团队暂停、协调升级路径
    - 在「架构」加 HeartbeatScheduler / red_actions 模块说明
    - 链接 docs/testing-conventions.md

28. 按 gitpush 流程分批提交：
    - 批 1: 数据基础（task_store + conversation_store + router 防洪水统一）→ refactor(core)
    - 批 2: 红色拦截 + 协调三连 prompt → feat(safety)
    - 批 3: 心跳调度器 + 暂停 API + UI → feat(heartbeat)
    - 批 4: N 补丁（A2/C2/C1 + testing-conventions.md）→ feat(robustness)
    - 批 5: README + smoke test → doc/test
    最后 git tag mvp-plus-plus-milestone-YYYYMMDD + git push --tags
```

---

## 8. Prompt 模板规范（EXECUTE 阶段写 prompt 时遵守）

### §8.1 红色操作协议段（必须含 4 要素）

1. 红色清单（3 个工具名）
2. 标准两步流程（先 ask_user 再红色操作）
3. 每个红色操作各 1 个 ask_user tool_call JSON 示例
4. 强禁令文本（裸调即错）

### §8.2 协调升级路径段（4 步）

1. send_message 询问
2. update_task 留备注
3. ask_user 让用户决策
4. dismiss / 重派（且必须经过红色操作协议）

### §8.3 写法约定

- 所有 tool_call JSON 用 ```` ```tool_call ```` 围栏包裹
- 强禁令用「【硬性约束 - 违反视为错误】」开头
- yaml 文件 instructions 段用 `|` 多行字符串
- 不动成员 yaml（成员不需要协调三连/红色操作段）

---

## 9. 数据模型与 API 速查

### §9.1 数据库 schema 增量

- `task_advisory(task_id PK, last_advisory_ts)` — 新表
- `conversations.is_paused INTEGER DEFAULT 0` — ALTER TABLE 加列

### §9.2 REST 增量

| METHOD | PATH | 用途 |
|---|---|---|
| POST | /api/threads/{tid}/pause | 暂停团队心跳 |
| POST | /api/threads/{tid}/resume | 恢复团队心跳 |
| GET | /api/threads/{tid}/status | 查暂停状态 + 沉默任务数 |

### §9.3 WebSocket 事件增量

- `{"type":"thread_paused","is_paused":bool}` — 暂停状态变更
- `{"type":"heartbeat","silent_count":int,"thread_id":str}` — 心跳触发（前端用于铃铛闪动）

### §9.4 Envelope metadata 增量

- `metadata.system_advisory = True` — 心跳 advisory 注入的 envelope（区别于 tool_feedback）

---

## 10. DoD 验证记录（EXECUTE 阶段填）

| # | DoD 项 | 状态 | 证据 |
|---|---|---|---|
| 1 | 红色拦截裸调被拒 | ⬜ | |
| 2 | 红色放行（confirm 通过） | ⬜ | |
| 3 | 心跳 advisory 注入 | ⬜ | |
| 4 | 心跳去抖 | ⬜ | |
| 5 | 暂停/恢复生效 | ⬜ | |
| 6 | 协调三连不跳步 | ⬜ | |
| 7 | A2/C2 hint 正确 | ⬜ | |
| 8 | C1 无空气泡 | ⬜ | |
| 9 | A1 文档落盘 | ⬜ | |

---

## 11. 风险与回滚

### §11.1 已识别风险

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| 心跳调度器吞 token | 中 | advisory 太频繁，长项目 token 爆 | 阈值按 priority 分档 + 10min 去抖 + 暂停按钮三道闸 |
| 红色拦截卡死合理操作 | 中 | 用户被频繁问"yes/no" | 60 秒短窗 + ask_user 已有去抖 |
| ALTER TABLE 在老库失败 | 低 | 旧项目无法启动 | try/except OperationalError 兼容 |
| 心跳 _scan_thread 阻塞 | 中 | 一个项目慢拖累所有 | 每个 thread 独立 try/except + asyncio.gather |
| 防洪水重复实现合并出 bug | 中 | send_message 限流失效 | smoke test 必须覆盖此路径 |

### §11.2 回滚策略

- Phase 1 / 2 / 3 / 4 各自独立 commit；任一阶段验证失败可单独 revert
- HeartbeatScheduler 默认禁用一个开关（环境变量 `HEARTBEAT_ENABLED=0` 即可关掉）
- ALTER TABLE 是兼容性操作，不可回滚但不会破坏老库

### §11.3 兼容性

- 老项目（无 task_advisory 表）：init_db 自动建
- 老 conversations 表（无 is_paused）：ALTER TABLE 自动加默认 0
- 老 orchestrator yaml（无红色操作段 / 协调三连段）：调红色操作时被拦截，错误文案教 prompt 该怎么改；用户可手动追加 yaml 段，registry 热加载即可

---

## 12. 接力 prompt 模板（新 session 接手用）

```
继续 agent-platform 的 MVP-Plus++ 任务。先按下面步骤报到：
- 已读：docs/team-mvp-plus.md ✅
- 已读：docs/team-mvp-plus-todolist.md ✅（上一里程碑）
- 已读：docs/team-mvp-plus-plus-todolist.md ✅（本任务）
- 已读：.tasks/2026-04-25_2_mvp-plus-plus.md ✅
- git log -5：
    <粘贴最近 5 条 commit>
- git status：
    <粘贴当前未提交改动>
- §1.3 ✅ 计数：N / 28
- 当前应执行步骤：第 X 步「...」
- 阻塞？无 / 有：...

[本 session 计划完成步骤范围]
第 X ~ 第 Y 步
```

---

## 13. 工作约定

- 严格 RIPER-5：当前已 PLAN 完成；EXECUTE 阶段每步完成后追加"任务进度"到 `.tasks/2026-04-25_2_mvp-plus-plus.md`
- 每步实施后跑相关 smoke test；失败回 PLAN
- 不能跳步、不能合并步骤；每步是一个原子单元
- 每个 Phase commit 一次（共 5 次 commit），最后打 tag
- 所有中文交流；模式声明保持英文 `[MODE: ...]`

---

## 14. 总览：实施清单（28 步全集）

```
实施清单：
1.  task_store.py: 加 task_advisory 表 DDL
2.  task_store.py: 加 list_silent_tasks 方法
3.  task_store.py: 加 mark_advisory_sent + get_last_advisory_ts
4.  conversation_store.py: 加 is_paused 列 + set_paused/is_paused 方法
5.  router.py: 删除 _check_flood 副本，统一为 public check_flood
6.  team_tools.py: 删除 _RECENT_MSGS，改调 router.check_flood
7.  smoke test 扩展（list_silent + paused）

8.  新建 core/red_actions.py
9.  question_store.py: 加 find_recent_answered_with_marker
10. main.py::_execute_tool: 3 个红色分支前加 confirm 检查
11. main.py::_ORCHESTRATOR_YAML_TMPL: 加红色操作协议段（含 JSON 示例）

12. 新建 core/heartbeat.py: HeartbeatScheduler
13. team_tools.py: 加 _THREAD_PROJECT_DIR + get_project_dir
14. router.py: 加 record_system_advisory
15. main.py: startup/shutdown 接入 scheduler
16. main.py: 加 pause/resume/status 3 个 REST 端点 + ws 广播
17. web/index.html: 团队栏顶部加暂停按钮 + 状态点

18. main.py::_ORCHESTRATOR_YAML_TMPL: 加协调升级路径段
19. 批量更新 3 个项目 orchestrator yaml

20. 补丁 A2: _execute_tool 异常追加自检建议
21. 补丁 C2: team_tools 入口加显式字段校验
22. 补丁 C1: 前端空气泡渲染条件
23. 补丁 A1: 写 docs/testing-conventions.md

24. smoke test 全量跑
25. 端到端 DoD 9 项验证
26. §10 验证记录回填

27. 更新 README.md
28. 分批 commit + 打 tag
```

---

## 15. 接下来需要你做的判断（PLAN 阶段最后一关）

1. **本 PLAN 是否批准**？
   - 接受 → 我立即更新 `.tasks/2026-04-25_2_mvp-plus-plus.md` 的"提议的解决方案"段并冻结，等你说 `ENTER EXECUTE MODE`
   - 不接受 → 告诉我哪步要改，回 PLAN 修订
2. **是否要切独立分支**？建议 `task/mvp-plus-plus_2026-04-25_2`（隔离风险，最后 PR merge 回 main）
3. **EXECUTE 节奏**：你倾向"逐步确认"（每步问一次"成功？"）/"按 Phase 确认"（每完成一个 Phase 问一次）/"全自动跑完再 REVIEW"？
