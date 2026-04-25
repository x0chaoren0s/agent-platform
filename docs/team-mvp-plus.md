# 团队自治 MVP-Plus 设计文档

> 本文档落盘自 2026-04-25 的设计讨论。目的：把多 Agent 平台从「群聊机器人」升级为「有真实战斗力的虚拟团队」。
>
> 阅读顺序建议：先看「问题与目标」→「7 层能力金字塔」→「MVP-Plus 范围」三节即可掌握后续 PLAN 的输入；细节设计章节供实施时回查。

---

## 1. 问题与目标

### 1.1 当前架构能做什么、不能做什么

`core/router.py::_dispatch_inner` 当前的消息流：

```
用户 → dispatch() → orchestrator → 回复用户
```

每个 agent 仅在被点名时被动唤醒，回复也只能发回给 `original_sender`。`reply_envelope` 写死 `to=[original_sender]`，agent 之间**没有任何主动通信渠道**。

唯一例外是 `parse_escalation` 触发的"被动升级"：agent 在回复里写 `【需要协助:cap:desc】`，router 自动转发到拥有该能力的成员（或 orchestrator）。这只能转发一次，不能形成多轮协作。

实际效果：orchestrator 在自然语言里说「我已通知调研专员」是空话，**调研专员的进程从未被调用过**。

### 1.2 用户期望

用户明确表达：「要的就是有真实战斗力的团队，能帮我应对真实场景复杂任务」。
关键关切：

- 成员之间能直接通信（不必经 orchestrator 中转）
- 成员可以主动找用户提问
- 任务失败可重试 / 换人

---

## 2. 7 层能力金字塔（设计目标全景）

```
                ┌──────────────────────┐
        Lv7    │  自适应：换人 / 重试    │  ← 失败容错
                ├──────────────────────┤
        Lv6    │  主动找用户：澄清/汇报  │  ← 人在回路
                ├──────────────────────┤
        Lv5    │  互相协作：成员↔成员    │  ← 去中心化通信
                ├──────────────────────┤
        Lv4    │  任务追踪：状态/Deadline│  ← 谁在干、干到哪
                ├──────────────────────┤
        Lv3    │  共享工作区：产物互通   │  ← 文件/知识/记忆
                ├──────────────────────┤
        Lv2    │  结构化任务分配         │  ← 替代「嘴上说」
                ├──────────────────────┤
        Lv1    │  消息总线：to/cc/广播   │  ← 通信底座
                └──────────────────────┘
```

### 三大核心张力（每层设计都要在此间权衡）

1. **主动性 vs 失控**：让 agent 能自己发消息，但不能无限套娃。
2. **异步并行 vs 顺序可观察**：多人同时干活更高效，但用户/编排器需要清晰看到全貌。
3. **自主决策 vs 用户掌控**：团队自己推进，但用户随时能干预、暂停、重定向。

---

## 3. 通信底座（Lv1）：让 LLM「主动发消息」的语法选择

LLM 输出是文本，所以「主动通信」本质是约定一种它能稳定写出、router 能稳定解析的信号。三种风格：

| 风格 | 形式 | 优点 | 风险 |
|------|------|------|------|
| A. 结构化标签 | `【MSG → 调研员, CC: orchestrator】...` | 自然语言友好 | LLM 偶尔写错格式 |
| B. 工具调用 (Function Call) | `send_message(to, cc, content)` | 模型已习惯、准确率高、UI 已有折叠卡 | 多消息时延迟稍高 |
| C. JSON 末段批量 | 末尾输出 `{actions:[...]}` | 单次回复内可批量 | JSON 不严格易失败、UI 不友好 |

**采纳：B（工具）+ 支持批量字段**，与现有 `recruit_fixed` / `list_team` / `record_tool_feedback` 链路一脉相承。

---

## 4. 任务原语与 TaskBoard（Lv2 + Lv4）

### 4.1 Task 数据模型（最小核心字段）

```python
Task {
  id: "task-001"
  project: "Interview"
  thread_id: "main-u2uf0"
  title: "完成星火万物 AIGC 岗位调研"
  brief: "聚焦公司背景、产研流程、面试真题，输出 Markdown 报告"
  assignee: "企业岗位调研专员"
  created_by: "orchestrator"
  status: "pending" | "in_progress" | "blocked_on_user" | "done" | "failed" | "cancelled"
  priority: "low" | "normal" | "high" | "urgent"
  deadline: "2026-04-26T12:00:00"   # 可空
  depends_on: ["task-000"]          # 可空
  deliverable_kind: "markdown" | "json" | "file" | "decision" | "none"
  deliverable_path: "workspace/reports/star-fire-aigc.md"  # 完成后填
  context_refs: ["msg-0042", "task-000.deliverable"]  # 启动时给 agent 看哪些资料
  retries: 0
  max_retries: 2
  history: [{ts, event, by, note}, ...]
  parent_task: null                  # 当前 MVP 不启用嵌套
}
```

### 4.2 设计选择的取舍

| 议题 | 当前选择 | 理由 |
|------|----------|------|
| 嵌套子任务 | 不启用，扁平 + `depends_on` | 拆解时让 orchestrator 拆成多个平级任务，UI 更易呈现 |
| 任务 reassign 权限 | 仅 orchestrator/user 可 | 防止互相踢皮球；普通成员只能 give_up |
| 状态可逆？ | 不允许（done 不能回到 in_progress） | 被拒时开新任务 `task-001-rev1`，保留可追溯 |

### 4.3 工具签名（5 个原语 + 3 个辅助）

```python
# —— 任务原语 ——
assign_task(
    assignee: str,                  # 单个 agent 名
    title: str,
    brief: str,
    deadline: str | None = None,    # ISO 或自然语言「明天中午」由后端归一化
    depends_on: list[str] = [],
    deliverable_kind: str = "markdown",
    context_refs: list[str] = [],
    priority: str = "normal",
) -> task_id

update_task(
    task_id: str,
    status: str | None = None,      # in_progress / blocked / done / failed
    progress_note: str | None = None,
    deliverable_path: str | None = None,
)

list_tasks(filter: dict | None = None) -> list[Task]

reassign_task(task_id: str, new_assignee: str, reason: str)   # 仅 orchestrator/user

cancel_task(task_id: str, reason: str)

# —— 辅助通信原语 ——
send_message(to, cc, content, related_task: str | None = None)
ask_user(question, options=None, urgency='normal', related_task=None)
submit_deliverable(task_id, content_or_path, summary)
```

`submit_deliverable` 单独存在的理由：它是仪式性动作，触发：
1. 写入工作区 + 索引到知识库
2. 自动检查下游 `depends_on=该 task` 的任务，从 `pending` 推进到 `ready`
3. 通知所有相关方（创建者 + CC）
4. （可选）触发 orchestrator 的"质量审查"任务

### 4.4 TaskBoard UI 风格选择

四种 UI 形态对比：

| 风格 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A. 顶部 Tab 切换 | 聊天 / 任务 / 产出物 | 极简 | 切来切去看不见对应关系 |
| B. 右侧抽屉 | 主区聊天 + 可折叠面板 | 边聊边看 | 屏幕窄时拥挤 |
| C. Slack 三栏 | 成员/任务 → 聊天 → 详情 | 信息密度高 | 移动端废 / 开发量大 |
| **D. 聊天内嵌任务卡 + 顶部 Kanban** | 任务事件以特殊卡片渲染在聊天流 + 顶部入口展开 Kanban | 聊天即时间线、任务即里程碑、贴现有 UI | — |

**采纳 D**。

### 4.5 任务卡 UI 草图（D 风格）

```
[10:24] orchestrator → 用户：好，我来分配任务
        ↓
        ┌─────────────────────────────────────┐
        │ 📋 任务卡 #task-001                  │
        │ 标题：完成星火万物 AIGC 岗位调研     │
        │ 负责：企业岗位调研专员                 │
        │ DDL：明天 12:00                       │
        │ 状态：● 待开始    [催办] [取消]      │
        └─────────────────────────────────────┘

[10:25] 调研员 → orchestrator：收到，我开始了
        ↓
        ┌─────────────────────────────────────┐
        │ 📋 #task-001 状态变更                │
        │ ● 待开始 → ● 进行中                  │
        └─────────────────────────────────────┘

[12:03] 调研员 → orchestrator：交付完成
        ↓
        ┌─────────────────────────────────────┐
        │ 📦 交付物 #task-001                  │
        │ 📄 star-fire-aigc.md  (8.2KB)        │
        │ 摘要：公司成立于2018年...             │
        │       [查看] [下载] [拒收]            │
        └─────────────────────────────────────┘
```

顶部入口浮层 Kanban：

```
┌─────────┬─────────┬─────────┬─────────┐
│ 待开始(2)│ 进行中(3)│ 阻塞(1) │ 完成(5) │
├─────────┼─────────┼─────────┼─────────┤
│ #003 ...│ #001 ...│ #004 ...│ #000 ...│
│ #005 ...│ #002 ...│         │ ...     │
│         │ #006 ...│         │         │
└─────────┴─────────┴─────────┴─────────┘
```

每张卡可点开看详情、跳到对应聊天位置。

---

## 5. ask_user：人在回路（Lv6.1，MVP 包含）

### 5.1 三种提问形态

**形态 1：选择题**（最常用、质量最高）

```python
ask_user(
    question="您简历更想突出哪个方向？",
    options=[
        {"id": "tech",    "label": "技术深度", "hint": "强调架构、性能、底层"},
        {"id": "product", "label": "产品视角", "hint": "强调用户、增长、业务理解"},
        {"id": "both",    "label": "两版都要", "hint": "我后续再选"},
    ],
    urgency="high",
    related_task="task-002",
)
```

UI 渲染卡片 + 按钮，用户点按钮 → router 把 `{answer: "tech"}` 推回提问者。

**形态 2：开放问答** — 卡片 + 输入框（可上传附件）。

**形态 3：确认/审批** — 内嵌附件预览（markdown 渲染、图片缩略）+ `[批准]` `[需要修改]` 按钮。

### 5.2 关键交互细节

| 场景 | 设计 |
|------|------|
| 多人同时提问 | 顶部铃铛 + 待办收件箱抽屉；仅 `urgency=high` 才在主流弹卡片，普通的只在收件箱堆 |
| 用户回复路由 | 提问时记录 `(question_id, asker, related_task)`；用户回复构造特殊 envelope `metadata={reply_to_question: ...}`，agent 下次唤醒时即可看到自己问的问题 + 答案 |
| 用户不回复 | 任务进入 `blocked_on_user`；软超时 1h 红点提醒；硬超时 24h orchestrator 通知或替选默认 |
| agent 滥用 ask_user | 每任务最多 N 次（默认 3），超了 give_up；提示词引导「先尝试自己解决/搜索/与同事讨论」 |

### 5.3 阻塞 vs 非阻塞

- **阻塞式**（MVP）：agent 提问后停下，任务进入 `blocked_on_user`。简单、符合直觉。
- **非阻塞式**（推后）：agent 提问后继续做能做的部分，把问题挂在 `pending_questions`。更像真实工作场景，但实现复杂。

---

## 6. 心跳循环：怎么写不会爆 token

### 6.1 关键洞见

天真实现：每分钟把所有 agent 都叫一遍 → 灾难。
正确做法：**事件驱动 + 极轻量调度，绝大多数 tick 不调用 LLM**。

### 6.2 心跳要做的事，按 LLM 成本分级

```
Lv0  零 LLM 调用（占 99% 的 tick）
  - 扫描 TaskBoard：找 status=in_progress && now > deadline
  - 扫描 ask_user 队列：找超时未回复
  - 扫描 depends_on 链：找上游刚完成、下游可启动的任务
  - 推送状态到 WebSocket（仅 UI 刷新）

Lv1  局部 LLM 调用（~1%）
  - 「下游任务可启动」→ 给下游 agent 发「轮到你了」消息
    （消息派发本身不调 LLM；下游 agent 处理消息时才调）

Lv2  编排级 LLM 调用（罕见）
  - 任务超时催办：让 orchestrator 看一眼超时清单
  - 异常熔断：检测到死循环或预算告急，让 orchestrator 总结+暂停
```

### 6.3 触发策略

**事件驱动（主力）：**
- 新消息进来 → 立即调度
- task 状态变化 → 立即调度
- 用户回复 ask_user → 立即唤醒对应 agent

**定时驱动（兜底）：**
- 每 60 秒（可配）跑一次「超时扫描」，仅 Lv0
- 整个项目无 in_progress 任务时心跳停止

### 6.4 防止 token 爆炸的硬约束

| 约束 | 默认值 | 触发后果 |
|------|--------|---------|
| 任务级 token 预算 | 50K | 标记 `failed`，原因 `budget_exceeded` |
| 项目级 token 预算 | 可配 | 80% 警告条 / 95% 暂停非关键 / 100% 硬停 |
| 防回声（无意义短消息） | <20 字 + 命中「好的/收到/确认」 | 不触发对方 agent，仅 UI 显示 |
| 一对消息 5 分钟内最多 N 条 | 6 | 超出 → 强制 CC orchestrator 介入 |
| 单 agent 上下文窗口 | 按 task 切片 | 仅含相关 task + 自己最近 N 条消息 |
| 「need_action」预判 | 规则/小模型 | 非必须响应 → 标记已读不调主模型 |

### 6.5 心跳骨架伪代码

```python
async def heartbeat_loop():
    while True:
        triggers = await drain_event_queue(timeout=60)  # 事件 or 60s 兜底
        if not triggers:
            triggers = ["periodic_check"]

        for trigger in triggers:
            # 第 1 步：纯调度（零 LLM）
            ready_tasks    = scheduler.find_ready_tasks()
            timeout_tasks  = scheduler.find_timeouts()
            stuck_questions= scheduler.find_stuck_asks()

            # 第 2 步：派发动作（少量 LLM，按需）
            for t in ready_tasks:
                router.notify_assignee(t)   # 只发消息，被通知方处理时才调 LLM

            for t in timeout_tasks:
                if t.retries < t.max_retries:
                    router.escalate_to_orchestrator(t, reason="deadline")
                else:
                    router.escalate_to_user(t, reason="repeated_failure")

            for q in stuck_questions:
                ui.flag_pending(q)          # 仅 UI 提示，不调 LLM

        broadcast_state_to_ui()
```

99% 时间在做无 LLM 的状态扫描+消息派发。真正的"思考"只发生在 agent 被唤醒时，唤醒始终由消息驱动。

---

## 7. 共享工作区（Lv3，MVP 用最简方案）

### 7.1 三种方案

| 方案 | 形式 | 适用 |
|------|------|------|
| A. 简单文件夹 | `projects/<p>/workspace/<file>` + `read_file`/`write_file` 工具 | 起步推荐 |
| B. 知识库 + 检索 | 复用 `knowledge_base` 模块，打 tag、`search_kb` 拉取 | 后期检索需求 |
| C. 结构化制品库 | SQLite 存 deliverable 对象 + UI「团队成果」Tab | 想看见产出全貌时 |

**MVP 采纳 A**；待真出现"找不到产物"或"产物太多翻不完"时再叠 C。

---

## 8. 失败容错（Lv7，MVP 用最简方案）

### 8.1 失败模式

| 模式 | 检测 | 处理 |
|------|------|------|
| 显式失败 | agent 调 `give_up(reason)` | 自动 escalate 给 orchestrator |
| 超时未交付 | TaskBoard 后台 deadline 检查 | 催办 → 仍无响应升级 |
| 质量不合格 | 下游 `reject_deliverable(task_id, reason)` | 触发返工或换人 |
| 死循环/跑偏 | 监控对话深度、token、相似度 | 熔断 + 暂停 + 通知用户 |

### 8.2 换人 / 重试策略

- 同岗位有 Plan B → 直接重派；
- 没有 → orchestrator 启动招募（已有的 `recruit_fixed` 或临时工机制）；
- 都不行 → 正式向用户报告失败 + 建议；
- 重试默认最多 2 次，每次附带"上次为什么失败"的信息；历次留痕用户可审。

**MVP 仅实现 `give_up → orchestrator 接手` 这一最小子集**；完整失败容错等真出现失败案例后再设计。

---

## 9. MVP 范围决策（多次迭代后的最终版本）

### 9.1 三个备选 MVP 包

| 选项 | 包含 | 类比 |
|------|------|------|
| MVP-Lite | 任务原语 + ask_user + 依赖链自动接力 | 流水线 |
| **MVP-Plus（采纳）** | + `send_message` 成员互通 + 默认 CC orchestrator + 消息洪水保护 | 真团队雏形 |
| MVP-Plus+ | + give_up 自动换人 + 全员广播 | 团队应变 |

### 9.2 为什么是 MVP-Plus 而非 MVP-Lite

- 用户明确目标是「真战斗力」，Lite 跑出来仍像群聊机器人；
- `send_message` 用 router 已有的 `to`/`cc`/`Envelope` 字段，与 Lite 共底座 → **不存在重构债**；
- 工程量增量约 0.5-1 天，相对总盘子是边际成本；
- 第一印象不可逆，Lite 上线后再补 send_message，用户已经定型。

### 9.3 为什么暂不做 MVP-Plus+

- "失败重试 / 换人 / 异常熔断" 在没有真实失败案例前盲设计容易做出花架子；
- 等 Plus 跑过几次有真实失败再设计，能精准命中真问题。

### 9.4 MVP-Plus 详细范围

**包含：**

1. 后端：Task 数据模型 + SQLite 任务存储
2. 工具：`assign_task` / `update_task` / `submit_deliverable` / `list_tasks` 4 个 platform_tool
3. 工具：`send_message`（默认 CC orchestrator + 消息洪水保护）
4. 工具：`ask_user`（先支持选择题 + 开放题）
5. Router：`submit_deliverable` 触发下游 `notify_assignee`（事件驱动，零 LLM）
6. UI：聊天内嵌任务卡（创建 / 状态变更 / 交付）+ 顶部 Kanban 浮层
7. UI：ask_user 卡片（按钮 / 输入框）+ 待办收件箱铃铛
8. Orchestrator system prompt 升级：必须用 `assign_task` 派活，禁止只用嘴说

**不包含（推后）：**

- 失败重试、换人、招募联动（`give_up` 之外的）
- Token 预算硬约束（仅显示用量，不熔断）
- 定时心跳 / 超时催办（先纯事件驱动）
- 黑板系统、知识库索引、嵌套子任务
- ask_user 确认/审批形态（先两种）

**预估开发量：** 中等。后端 4 个任务工具 + Task 表 + Router 联动 ≈ 2-3 个模块；前端 2-3 个新组件 + WebSocket 新事件类型。

---

## 10. 后续演进路径（MVP-Plus 跑通之后）

按这个顺序灰度上线，避免跳着做塌房：

| 阶段 | 主题 | 何时启动 |
|------|------|---------|
| v1 = MVP-Plus | 任务派活 + 成员互通 + ask_user + Kanban | 下一轮 PLAN |
| v2 | 失败容错（give_up 之外的换人/重试） | 真实失败案例出现后 |
| v3 | 定时心跳 / 超时催办 | 真出现"任务卡住没人管" |
| v4 | Token 预算熔断 | 真出现成本失控 |
| v5 | 共享工作区结构化制品库（方案 C） | 产物量大到翻不完 |
| v6 | 非阻塞式 ask_user | 频繁出现"提问就卡死" |
| v7 | 异常熔断 / 死循环检测 | 真出现死循环 |

---

## 11. 风险全景图（Lv5 ~ Lv7 引入后的潜在风险）

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| 消息洪水（agent 互发不停） | 高 | 任务最大消息数 + token 预算 |
| 跑偏 / 死循环 | 高 | 监控相似度 + 深度上限 + 用户中断按钮 |
| 成本爆炸 | 高 | 每项目 token 预算上限 + 实时计费显示 |
| 用户被 push 淹没 | 中 | 消息聚合 + 优先级 + 静默时段 |
| 责任不清（谁的锅） | 中 | TaskBoard 全程留痕 |
| 状态不一致（黑板 vs 消息） | 中 | TaskBoard 作为单一事实源 |
| Agent 互相"客套"耗 token | 低 | 系统提示要求"商务简洁" + need_action 预判 |

---

## 12. 与现有代码的衔接点（实施前的 grep 索引）

| 现有文件 | 当前职责 | MVP-Plus 如何复用/扩展 |
|----------|---------|----------------------|
| `core/router.py` | `Envelope`、`dispatch`、`record_tool_feedback`、capability escalation | `send_message` 的实际派发就是复用 `_dispatch_inner`；新增 `notify_assignee` 派发逻辑 |
| `core/platform_tools.py` | `recruit_fixed` / `list_team` 等 platform 工具 | 新增 `assign_task` 等 7 个工具的实现 |
| `core/registry.py` | Agent YAML 监听与加载 | 不动（新工具与 agent 解耦） |
| `core/conversation_store.py` | 对话/线程持久化 | 任务存储新增独立 SQLite 表，不与对话表耦合 |
| `main.py` | FastAPI + WebSocket 入口 | 新增任务相关 REST + WebSocket 事件类型 |
| `web/index.html` | 单页前端 | 新增任务卡组件、Kanban 浮层、ask_user 卡片、收件箱铃铛 |
| 项目目录 `projects/<p>/` | 当前已有 `agents/`、`memory/`、`sessions/`、`chat_log/`、`context.md` | 新增 `tasks.db`（SQLite）和 `workspace/` 目录 |

---

## 13. 下一步

进入 PLAN 模式，把 §9.4 的 MVP-Plus 范围细化为：

- 编号清单（具体到文件路径、函数签名、UI 组件名）
- WebSocket 新事件 schema
- SQLite 任务表 DDL
- Orchestrator system prompt 改写要点
- 灰度顺序（先后端工具 → 再 Router 联动 → 再 UI）

PLAN 完成并经 review 后再 ENTER EXECUTE MODE。
