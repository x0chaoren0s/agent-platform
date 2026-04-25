# 背景
文件名：2026-04-25_2_mvp-plus-plus.md
创建于：2026-04-25 15:43:00
创建者：60490
主分支：main
任务分支：（暂未切，待 PLAN 阶段确认）
Yolo模式：Off

# 任务描述
MVP-Plus 完成后，在 opc 项目实战中暴露出 8 类协议/反馈/UI/协调智能问题。
本任务对 MVP-Plus++ 8 项议题（A1/A2/A3/B1/B2/C1/C2/D1/D2）进行 RESEARCH → INNOVATE → PLAN → EXECUTE → REVIEW 全流程。

# 项目概览
agent-platform：基于 MAF + FastAPI + WebSocket + SQLite 的多智能体协作平台。
当前里程碑：mvp-plus-milestone-20260425（commit 3ca04b7）。

⚠️ 警告：永远不要修改此部分 ⚠️
RIPER-5 核心规则：
- 必须在每个响应开头声明当前模式 [MODE: NAME]
- RESEARCH：仅观察与提问，禁止建议/规划/实施
- INNOVATE：自然段头脑风暴，禁止代码/具体实施细节
- PLAN：详尽编号清单，禁止任何实施
- EXECUTE：100% 忠实执行计划，发现偏差立即回 PLAN
- REVIEW：逐项对照计划，标记任何偏差
- 每次实施后追加"任务进度"段
⚠️ 警告：永远不要修改此部分 ⚠️

# 分析

## 8 个独立问题清单

| # | 类别 | 一句话 | 修复难度 | 严重度 |
|---|---|---|---|---|
| A1 | 协议 | 测试/演练消息无标识，被同事和 orch 误为真实业务 | 中 | 高 |
| A2 | 协议 | 工具失败反馈链断 → agent 不自检 → 嘴上跑动 | 中 | 高 |
| A3 | 协议 | 破坏性操作（dismiss/recruit）无 ask_user 门槛 | 低 | 高 |
| B1 | 反馈 | 限流文案无替代路径指引 | 低 | 中 |
| B2 | 反馈 | CC 强制注入把噪音直送决策中枢 | 中 | 中 |
| C1 | UI | 纯工具消息渲染成空气泡（独立 fix，不进 MVP-Plus++） | 低 | 低 |
| C2 | 容错 | assign_task schema 错误静默吞 | 低 | 中 |
| D1 | 协调智能 | orch 只懂"派"和"训话"，不懂"协调" | 中 | 高 |
| D2 | 心跳 | 无任务静默检测，进度只能靠用户拷问 | 高 | 中 |

## 用户决策（2026-04-25 15:42 确认）
1. 范围：MVP-Plus++ 覆盖以上 8 项**全部**
2. D2 任务静默检测：**纳入**
3. C1 空气泡：**摘出**为独立小 fix（与 MVP-Plus++ 解耦）
4. 测试模式（A1 对策）：由我帮选
5. 立任务文件：是

## 现场证据来源
- chat_log: projects/opc/chat_log/main-t6c4z.json
- 关键 envelope：msg-0007/0009/0014/0028~0064
- 服务器 traceback：assign_task() missing 'title' and 'brief'（已被今早补丁拦截）

# 提议的解决方案（PLAN 阶段已冻结 2026-04-25 16:25）

## 形态：2 + N

**核心机制 ① 动作风险分级 + 服务器拦截**
- 红色清单：dismiss_member / recruit_fixed / update_project_context
- 拦截契约：必须先 ask_user(含 [[confirm:ACTION:TARGET]] marker)，60 秒内用户答 yes 才放行
- 配 prompt：所有 orchestrator yaml 加"红色操作协议"+"协调升级路径"两段

**核心机制 ② 任务级真心跳 + 团队放假**
- 周期 30s；阈值按 priority 分档（high=10min/normal=30min/low=2h）；硬去抖 10min
- advisory 形态：事件型（写入 orchestrator inbox 的 system_advisory envelope）
- 团队放假：thread 级 is_paused，UI 按钮在团队栏顶部

**N 个独立补丁**
- A2: tool 失败附"建议 list_tasks 自检"
- C2: team_tools 入口加字段校验返回可读 hint
- C1: 前端空气泡渲染优化
- A1: 写 docs/testing-conventions.md（不写代码）

## 详细计划落盘
docs/team-mvp-plus-plus-todolist.md（28 步原子操作，分 7 个 Phase）

## 数据模型增量
- 新表：task_advisory(task_id PK, last_advisory_ts)
- 新列：conversations.is_paused INTEGER DEFAULT 0
- 新 metadata：envelope.metadata.system_advisory = True

## REST 增量
- POST /api/threads/{tid}/pause
- POST /api/threads/{tid}/resume
- GET /api/threads/{tid}/status

## WebSocket 事件增量
- {"type":"thread_paused","is_paused":bool}
- {"type":"heartbeat","silent_count":int,"thread_id":str}

# 当前执行步骤："Phase 5/6 进行中：已完成本地 smoke + 编译校验，待你做端到端会话验证"

# 任务进度

[2026-04-25 15:43] 任务文件创建，进入 INNOVATE 模式
[2026-04-25 16:25] PLAN 完成，详细计划落盘 docs/team-mvp-plus-plus-todolist.md（28 步），等用户决策
[2026-04-25 16:31]
- 已修改：core/task_store.py、core/conversation_store.py、core/router.py、core/team_tools.py、scripts/smoke_test_tasks.py
- 更改：新增 task_advisory 表与静默任务查询/去抖接口；conversation 增加 is_paused 持久化；flood 检查统一到 router.check_flood；smoke test 覆盖静默任务与暂停状态
- 原因：落实 MVP-Plus++ Phase 0（步骤 1-7）数据与基础设施
- 阻碍因素：无
- 状态：成功
[2026-04-25 16:36]
- 已修改：core/red_actions.py、core/question_store.py、main.py
- 更改：新增红色操作确认模块（marker + 60 秒确认窗 + yes 校验）；question_store 增加 marker 检索；_execute_tool 接入红色拦截；orchestrator 模板加入红色操作协议示例
- 原因：落实 MVP-Plus++ Phase 1（步骤 8-11）不可逆动作防护
- 阻碍因素：无
- 状态：成功
[2026-04-25 16:44]
- 已修改：core/heartbeat.py、main.py、core/router.py、core/team_tools.py、web/index.html
- 更改：接入任务级心跳调度器（30s 周期、优先级阈值、10min 去抖）；新增 thread pause/resume/status API；前端团队栏加入暂停按钮并监听 thread_paused/heartbeat 事件
- 原因：落实 MVP-Plus++ Phase 2（步骤 12-17）任务静默检测与团队放假开关
- 阻碍因素：无
- 状态：成功
[2026-04-25 16:46]
- 已修改：main.py、projects/opc/agents/orchestrator.yaml、projects/Interview/agents/orchestrator.yaml、projects/manga/agents/orchestrator.yaml
- 更改：模板与现有项目 orchestrator 提示词同步加入“红色操作协议”与“协调升级路径”两段
- 原因：落实 MVP-Plus++ Phase 3（步骤 18-19）协调智能约束
- 阻碍因素：无
- 状态：成功
[2026-04-25 16:49]
- 已修改：main.py、core/team_tools.py、web/index.html、docs/testing-conventions.md
- 更改：工具异常统一附 list_tasks 自检建议（A2）；team_tools 显式字段校验提示（C2）；修复工具-only 空气泡渲染（C1）；新增测试规范文档（A1）
- 原因：落实 MVP-Plus++ Phase 4（步骤 20-23）独立补丁
- 阻碍因素：无
- 状态：成功
[2026-04-25 16:52]
- 已修改：scripts/smoke_test_tasks.py、README.md
- 更改：本地 smoke 测试通过（含静默任务/暂停开关）；Python 文件编译校验通过；README 补充红色动作、真心跳、暂停开关、协调升级路径与 testing-conventions 文档链接
- 原因：推进 Phase 5/6 验证与文档收口
- 阻碍因素：端到端 DoD（需真实对话线程交互）需你在 UI 实测配合
- 状态：未确认

# 最终审查
（REVIEW 阶段填写）
