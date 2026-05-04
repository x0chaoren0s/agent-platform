# 上下文管理机制

## 整体架构：四层上下文

LLM 每次调用看到的 prompt 由 **四层** 叠加构成：

```
┌──────────────────────────────────────────────────┐
│ Layer 4: 运行时的路由注入历史                      │
│  【历史消息（仅你可见）】                          │
│  来自 _global_log → _inbox_for() → 拼入 prompt    │
├──────────────────────────────────────────────────┤
│ Layer 3: Agent 框架层的持久化历史                  │
│  SQLiteHistoryProvider.get_messages()              │
│  从 long_term.db 加载最近的 N 条 Message           │
├──────────────────────────────────────────────────┤
│ Layer 2: 动态上下文（运行时变化）                   │
│  · SkillIndexProvider（技能索引，每轮重新生成）     │
│  · 工具定义（以 TEXT 嵌入 instructions，非 API 定义）│
├──────────────────────────────────────────────────┤
│ Layer 1: 准静态上下文（创建后不变）                 │
│  · instructions（YAML 角色提示词 + 行为准则）       │
│  · ProjectContextProvider（context.md）            │
└──────────────────────────────────────────────────┘
```

---

## Layer 1：准静态上下文

**文件**：`core/registry.py`（agent 工厂，第 118-151 行）

agent 创建时一次性拼入，之后不变化（除非重启/重载 YAML）：

| 组件 | 来源 | 说明 |
|------|------|------|
| `instructions` | YAML `instructions` + `member_protocol.py` 注入 | 角色定义 + 行为准则 + **工具文本说明** |
| `ProjectContextProvider` | `projects/<name>/context.md` | 项目背景，每次 LLM 调用前重新读文件 |

### 协议注入（`core/member_protocol.py`）

所有 agent 的 instructions 在加载时自动拼入：

```python
compose_member_instructions(business_instructions):
    return (BASE_BEHAVIOR_PROTOCOL 
          + MEMBER_TASK_PROTOCOL 
          + tools_section          # ← 工具文本描述
          + business_instructions)
```

组成：
- `BASE_BEHAVIOR_PROTOCOL` — 通用准则（先想再做、简单优先、外科手术、事实诚信等）
- `MEMBER_TASK_PROTOCOL` / `TEMP_TASK_PROTOCOL` — 任务执行规则
- `tools_section` — **所有可用工具的文本描述**（见 Layer 2）
- `business_instructions` — YAML 原始 instructions

### YAML `skills` 字段

YAML 中可预配置 `skills:` 列表。启动时 `seed_agent_skills_from_yaml()` 将这些技能名写入 `_AGENT_SKILL_MAP`，后续由 `SkillIndexProvider` 读取。

---

## Layer 2：动态上下文

### 工具定义（以 TEXT 嵌入，非 API 定义）

**这是当前架构的关键设计选择**：工具不是以 LLM API 的 `tools` / `functions` 参数传递，而是作为**纯文本描述**拼入 instructions。

**文件**：`core/tools/registry.py:114-129`

```python
def render_tools_for_prompt(self, role, is_temp):
    tools = self.list_for_role(role, is_temp)   # 按角色过滤
    lines = ["【可用工具】"]
    for tool in tools:
        lines.append(f"- {tool.signature}：{tool.desc}")
    lines.append("""【tool_call 格式示例】
```tool_call
{"tool":"<name>","args":{...}}
```""")
    return "\n".join(lines)
```

**流程**：
1. 启动时 `RuntimeToolRegistry.discover()` 扫描 `core/tools/categories/` 下所有工具类
2. 每个工具声明 `role`（orchestrator / member / temp）决定谁可用
3. 创建 agent 时 `compose_*_instructions()` 调用 `render_tools_for_prompt()` → 文本描述嵌入 instructions
4. LLM 看到的是纯文本描述 + 示例，**没有** API 级 tool definition
5. LLM 回复后，`_tool_executor` → `_process_tool_calls()` 用 regex 提取 ````tool_call``` 块并执行

**特点**：
- 跨模型兼容（不依赖 API tool calling 支持）
- 工具列表随角色不同而变化（orchestrator 有 recruit_fixed 等团队管理工具，member 没有）
- 但文本描述占用的是 prompt 中的 system 部分 token，不是 tools 参数的配额

### SkillIndexProvider（技能索引）

**文件**：`core/skill_index_provider.py`

运行时动态注入**技能索引**（名称列表 + 描述），每轮 LLM 调用时重新生成：

```python
class SkillIndexProvider(HistoryProvider):
    async def get_messages(self, session_id, ...):
        skills = get_agent_skills(self._agent_name)
        text = build_skill_index(self._project_dir, skills)   # 只生成索引，不含完整内容
        if not text:
            return []
        return [Message(role="system", text=text)]
```

**注意：只索引技能名和描述**，不是技能完整内容。完整内容通过 `load_skill(name)` 按需加载。

**技能来源**：
| 来源 | 时机 | 说明 |
|------|------|------|
| YAML `skills:` 字段 | agent 创建时 | `seed_agent_skills_from_yaml()` |
| `mount_skill()` | 对话中动态 | 运行时挂载（`_AGENT_SKILL_MAP`），不写 YAML |
| `unmount_skill()` | 对话中动态 | 运行时卸载 |

### `load_skill(name)` — 按需加载完整内容

**文件**：`core/skill_store.py:149`

```python
async def load_skill(project_dir, thread_id, caller_agent, *, name):
    return skill_store.load_for_agent(project_dir, caller_agent, name)
```

**机制**：
1. 验证技能已挂载到该 agent
2. 从磁盘读取 `SKILL.md` 的正文部分（不含 frontmatter）
3. 内容作为工具执行结果返回 → 进入 `_global_log` → 作为 `【load_skill】\n<完整内容>` 出现在下一轮的 `【历史消息（仅你可见）】` 中

**⚠️ 重要：`load_skill()` 的内容不是永久注入 instructions**。它只是一个工具结果，位于 `_global_log` 中，受所有裁剪机制影响：
- Token 预算（750K）：超限后被截断
- 滚动摘要：60% 最旧对话被压缩，其中的 skill 内容可能丢失
- 长对话中需重新调用 `load_skill()` 刷新内容

---

## Layer 3：SQLiteHistoryProvider — 持久化历史

---

## Layer 2: SQLiteHistoryProvider — 持久化历史

### 存储

```
文件: projects/<name>/memory/long_term.db
表:  history_<agent_name>    （每个 agent 独立一张表）
列:  id, session_id, data, created_at
```

### 消息类型

`data` 列存 JSON，包含 role + text：

```json
{"role": "user", "text": "用户消息"}
{"role": "assistant", "text": "agent 回复（含 tool_call 代码块）"}
{"role": "tool", "text": "工具执行结果"}
{"role": "system", "text": "系统消息"}
```

### 写入时机

Agent 框架每轮 `run()` 完成后，框架内部调用 `save_messages()` 把本轮所有 Message 写入。包括：用户消息、agent 回复、工具结果、系统提示。

### 读取时机

`get_messages(session_id)` 在 agent 每次 `run()` 前被框架调用：

```sql
SELECT data FROM (
    SELECT id, data FROM "history_<agent_name>"
    WHERE session_id = ?
    ORDER BY id DESC LIMIT ?
) ORDER BY id ASC
```

参数 `LIMIT ?` = `min(YAML max_history, _trim_sqlite_history)`，实际由两者中更小的决定。

### 两个限制叠加

#### 限制 A：YAML `max_history`

| 角色 | 默认值 |
|------|--------|
| orchestrator | 100 |
| 固定成员 | 80 |
| 临时工 | 20 |

在 `core/registry.py:124-128`：

```python
memory_provider = SQLiteHistoryProvider(
    db_path=db_path,
    agent_id=agent_id,
    max_messages=max_history,    # ← 从 YAML 读取
)
```

#### 限制 B：`_trim_sqlite_history(agent_name, max_rows=20)`

**文件**：`core/router.py:814-834`

**调用时机**：`_run_agent()` 的 `while True` 循环中，每轮 LLM 调用**之前**执行。

```python
def _trim_sqlite_history(self, agent_name: str, max_rows: int = 20) -> None:
    db_path = ... / "long_term.db"
    table = f"history_{agent_name}"
    cnt = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    if cnt and cnt[0] > max_rows:
        # 只保留最新的 max_rows 行，删除旧的
        last = db.execute(f'SELECT rowid FROM "{table}" ORDER BY rowid DESC LIMIT ?', (max_rows,)).fetchall()
        min_id = min(r[0] for r in last)
        db.execute(f'DELETE FROM "{table}" WHERE rowid < ?', (min_id,))
```

**含义**：不管 YAML 配了多少，long_term.db 里每个 agent 的表**始终只保留最新 100 行**。旧的被物理删除。

> 实际生效值 = `min(YAML max_history, 100)`。

---

## Layer 3: `_global_log` → 运行时路由注入

### 3.1 `_global_log` 是什么

**文件**：`core/router.py:132`

```python
self._global_log: list[Envelope] = []
```

纯内存列表。每个 `Envelope` 包含：

```
sender      - 发送者 (user / agent_name / platform)
to          - 收件人列表
cc          - 抄送列表
content     - 正文文本
timestamp   - ISO 时间戳
metadata    - 附加元数据（tool_feedback, system_advisory 等）
images      - 图片列表
```

### 3.2 `_global_log` 的写入点

| 触发时机 | 调用位置 | 内容 |
|----------|----------|------|
| 用户发消息 | `_dispatch_inner()` L473 | 原始用户消息 |
| agent 回复 | `_run_agent()` L589 | agent 生成的回复文本 |
| 工具执行结果 | `record_tool_feedback()` L767 | `【工具执行结果】\n【tool_name】结果内容` |
| 系统提醒 | `record_system_advisory()` L791 | `【系统提醒】...` |
| 交接缺失提醒 | `_run_agent()` L689 | `【系统提醒｜非用户反馈】...` |

### 3.3 持久化

每追加一条 Envelope，`_flush_log()` 将 `_global_log` 序列化为 JSON 写入：

```
projects/<name>/chat_log/<thread_id>.json
```

服务重启时 `_load_log()` 重新加载。

### 3.4 构建最终 LLM Prompt

链路：

```
_build_run_input(agent_name, new_envelope)
  └→ _build_prompt_for(agent_name, new_envelope)
       ├→ 【历史摘要（早期对话已压缩）】        ← _inbox_summary 缓存
       ├→ 【历史消息（仅你可见）】              ← _inbox_for() 结果拼成
       └→ 【新消息 from xxx】                  ← 当前要处理的消息
```

### 3.5 `_inbox_for()` 筛选逻辑

**文件**：`core/router.py:262-279`

```python
def _inbox_for(self, agent_name: str):
    # 从 _global_log 中筛选该 agent 可见的消息
    full = [
        env for env in self._global_log
        if agent_name in env.recipients() or env.sender == agent_name
    ]
    # 按 token 预算从新到旧截断
    budget = int(MODEL_MAX_TOKENS * TOKEN_BUDGET_RATIO)   # = 750,000
    kept, used = [], 0
    for env in reversed(full):
        est = _estimate_tokens(env.content or "") + 20    # +20 元数据开销
        if used + est > budget:
            break                                          # 超出预算，丢弃更旧的
        kept.append(env)
        used += est
    kept.reverse()
    return kept
```

**关键参数**：

```
MODEL_MAX_TOKENS = 1_000_000       # DeepSeek v4 pro 上下文窗口
TOKEN_BUDGET_RATIO = 0.75          # inbox 占 75%
→ inbox 预算 = 750,000 tokens      # 约 1,000,000 个中文字符
                                  # 剩余 65% 给 system prompt + 工具定义 + 摘要
```

**估算方式**：`len(text) * 3/4`（中英文混合的粗略估计）。

超出预算的部分从**最旧**的开始丢弃。

### 3.6 消息格式（agent 视角）

`_build_prompt_for()` 最终拼成的格式：

```
【历史摘要（早期对话已压缩）】
## 关键决策
- ...

## 任务进展
- ...

## 待办/未解决
- ...


【历史消息（仅你可见）】
[2026-05-03 22:25:17] From: user → To: orchestrator
<消息内容>

---

[2026-05-03 22:25:18] From: orchestrator → To: 数据调研员
<消息内容>

---

[2026-05-03 22:25:19] From: platform → To: 数据调研员
【工具执行结果】
【web_search】结果内容...

【新消息 from user】
<当前要处理的消息>
```

---

## 滚动摘要（Rolling Summary）

### 触发条件

**文件**：`core/router.py:806-812`

```python
def _needs_token_based_summary(self, agent_name: str) -> bool:
    inbox = self._inbox_for(agent_name)            # 当前 inbox
    total_est = sum(_estimate_tokens(e.content) for e in inbox)
    summary_est = _estimate_tokens(self._inbox_summary.get(agent_name, ""))
    threshold = 350_000
    return (total_est + summary_est) > threshold
```

即：inbox 已用 token + 已有摘要 token > 750K 时触发。

### 执行过程

**文件**：`core/router.py:836-875`

```python
async def do_rolling_summary(self):
    total = len(self._global_log)
    cutoff = int(total * 0.6)        # 取最旧的 60%

    old_envelopes = self._global_log[:cutoff]

    # 按 agent 分组压缩
    for agent_name in affected_agents:
        relevant = [e for e in old_envelopes if e 对该 agent 可见]
        summary = await summarize_envelopes(relevant)   # 独立 LLM 调用
        if summary:
            self._inbox_summary[agent_name] += summary  # 追加到已有摘要

    # 丢弃最旧的 60%
    self._global_log = self._global_log[cutoff:]
```

### 摘要模型

**文件**：`core/summarizer.py:32-47`

```python
_SUMMARIZE_SYSTEM = """你是一个专业的对话分析助手。
输出格式：
## 关键决策
- ...

## 任务进展
- ...

## 待办/未解决
- ..."""

# 模型: deepseek-v4-flash (通过 DeepSeek API)
# temperature=0.3, max_tokens=600
```

三条固定 section：关键决策、任务进展、待办/未解决。

### 摘要失败的情况

```python
except Exception as exc:
    if "insufficient balance" in str(exc) or "error code: 402" in str(exc):
        # 摘要模型余额不足，静默跳过
        return ""
```

摘要模型与对话模型是**两个独立的 API 调用**。如果火山引擎账户余额不足，摘要被跳过，**最旧的 60% 消息直接丢失**，不会留下摘要替代。

---

## `_run_agent()` 每轮循环中的上下文操作

**文件**：`core/router.py:509-704`

### 循环流程

```
                    ┌──────────────────────┐
                    │  收到新消息/工具反馈   │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                 ┌─▶│  _trim_sqlite_history │← 裁剪 long_term.db 至 100 行
                 │  │  (每轮都执行)          │
                 │  └──────────┬───────────┘
                 │             ▼
                 │  ┌──────────────────────┐
                 │  │  needs_summary?      │
                 │  │  → do_rolling_summary│← 压缩最旧 60%，丢旧留新
                 │  └──────────┬───────────┘
                 │             ▼
                 │  ┌──────────────────────┐
                 │  │  _build_run_input()  │← 构建 prompt → LLM
                 │  └──────────┬───────────┘
                 │             ▼
                 │  ┌──────────────────────┐
                 │  │  agent.run() → LLM   │
                 │  └──────────┬───────────┘
                 │             ▼
                 │  ┌──────────────────────┐
                 │  │  解析 tool_call       │
                 │  │  执行工具              │
                 │  │  record_tool_feedback │← 结果写入 _global_log
                 │  └──────────┬───────────┘
                 │             ▼
                 │  ┌──────────────────────┐
                 │  │  auto_continue < 15? │──→ 是 → 继续循环
                 │  │  ask_user?            │
                 │  │  交接缺失提醒?         │
                 │  └──────────┬───────────┘
                 │             否
                 │             ▼
                 └──────────退出循环, 等待下一条消息
```

### 循环终止条件

| 条件 | 代码位置 | 说明 |
|------|----------|------|
| `auto_continue_rounds >= 15` | L633 | 工具调用轮次达到上限 |
| `ask_user` 被调用 | L631 | 等待用户回答，阻塞 |
| 空回复（无文本无工具） | L574-575 | `if not reply_text.strip(): break` |
| 异常 | L568-571 | LLM 调用抛异常，break |
| 交接缺失提醒已发 + handoff_gap >= 2 | L682-701 | 两次非通信工具后发提醒，再次达到则退出 |

---

## 配置参数速查表

| 参数 | 代码位置 | 当前值 | 控制范围 | 类型 |
|------|----------|--------|----------|------|
| `max_auto_continue_rounds` | `router.py:538` | 15 | 单轮内最多连续工具调用次数 | 硬限制 |
| `MODEL_MAX_TOKENS` | `router.py:107` | 1,000,000 | DeepSeek v4 pro 上下文窗口上限 | 硬限制 |
| `TOKEN_BUDGET_RATIO` | `router.py:108` | 0.75 ~ 750K | inbox 占模型窗口的比例 | 硬限制 |
| inbox 预算 | `_inbox_for()` | **750,000 tokens** | `_global_log` 中 agent 可见部分的最大 token 数 | 硬限制 |
| `_trim_sqlite_history` max_rows | `router.py:814` | **100 行** | `long_term.db` 每 agent 保留行数 | 硬限制 |
| `KEEP_FULL_MESSAGES` | `router.py:113` | **20 条** | 最新 N 条消息不压缩工具内容 | 硬限制 |
| `TOOL_COMPRESS_PREVIEW_CHARS` | `router.py:114` | **150** | 压缩后工具结果保留前导字符数 | 硬限制 |
| `max_history` (YAML) | `registry.py:127` | 100/80/20 | SQL LIMIT（实际被 20 覆盖） | 硬限制 |
| 滚动摘要丢弃比例 | `do_rolling_summary()` | **60%** | 超出预算后丢弃最旧的比例 | 硬限制 |
| `MAX_TOOL_RESULT_CHARS` | `router.py:109` | 3,000 | 每个工具结果正文截断长度 | 硬限制 |
| `MAX_ESCALATION_DEPTH` | `router.py:462` | 3 | 任务转发递归最大深度 | 硬限制 |
| 摘要模型 | `summarizer.py:66` | deepseek-v4-flash | API 不可用时静默跳过 | 软限制 |
| 消息洪泛防护 | `check_flood()` | 6条/5分钟 | 同对(发,收)超限后自动 CC orchestrator | 硬限制 |
| 工具结果截断 | `router.py:742` | 5,000 字符 | 超长工具结果截断 | 硬限制 |
| web 搜索上限 | `web_runtime.py` | 10 条 | 单次 web_search 最多返回结果数 | 硬限制 |
| web 读取上限 | `web_runtime.py` | 8,000 字符 | 单次 web_read 返回长度 | 硬限制 |
| 单文件读取上限 | `files_runtime.py` | 256 KiB | 单次 read_file 返回长度 | 硬限制 |
| URL 历史上限 | `web_runtime.py` | 50/agent | submit_deliverable 引用追踪 | 硬限制 |

---

## 各裁剪点的数据流向图

```
用户消息/工具反馈
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│                    _global_log (内存)                    │
│  所有 Envelope：用户消息 + agent回复 + 工具结果 + 系统提醒  │
│                                                         │
│  持久化: chat_log/<thread_id>.json                      │
│  摘要: 超出 750K → 压缩最旧 60% → 摘要存 _inbox_summary  │
└────────────────────┬────────────────────────────────────┘
                     │ _inbox_for() 筛选（收件人 + 750K 预算）
                     ▼
┌─────────────────────────────────────────────────────────┐
│                _build_prompt_for() 输出                  │
│  1. 历史摘要（如有）                                      │
│  2. 【历史消息（仅你可见）】   ← inbox 中的 Envelope 拼成  │
│  3. 【新消息 from xxx】        ← 当前待处理消息            │
└────────────────────┬────────────────────────────────────┘
                     │ + 来自 Agent 框架的 context_providers
                     ▼
┌─────────────────────────────────────────────────────────┐
│  最终 LLM Prompt                                        │
│  ┌─────────────────────────────────────────────────┐    │
│  │ instructions（准静态）                            │    │
│  │   ├─ BASE_BEHAVIOR_PROTOCOL                     │    │
│  │   ├─ MEMBER/TEMP_TASK_PROTOCOL                  │    │
│  │   ├─ tools_section（文本描述，每 agent 创建时生成）│    │
│  │   └─ YAML instructions + lark 等 skill 内容       │    │
│  │ ProjectContextProvider (context.md)             │    │
│  │ SkillIndexProvider（动态，每轮重新生成）            │    │
│  │ SQLiteHistoryProvider (long_term.db 历史) ← 100行 │    │
│  │ ─────────────────────────────────────────────── │    │
│  │ 【历史摘要】                                    │    │
│  │ 【历史消息（仅你可见）】                          │    │
│  │ 【新消息】                                      │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## 典型对话中的上下文衰减

以饥荒攻略对话（201 条记录）为例，说明上下文如何逐步衰减：

```
阶段 1: 对话初期（1-40 条）
  _global_log 完整，inbox 未超预算
  long_term.db 未超 100 行
  → agent 能看到全部上下文

阶段 2: 对话中期（40-80 条）
  _global_log 增长，inbox 接近 750K
  _trim_sqlite_history 开始删除旧行（仅保留 20）
  → long_term.db 中只看到最近 20 条
  → 但 _global_log（inbox）仍完整

阶段 3: 对话后期（80+ 条）
  inbox 超 750K 预算
  → do_rolling_summary 触发，最旧 60% 被压缩
  → 精确的任务分配记录、搜索结果等被摘要替代
  → 摘要模型余额不足时，旧消息直接丢失
```

> 注意：`_global_log`（Layer 3）和 `long_term.db`（Layer 2）是**两个独立通道**。
> 当前 session 中，`_global_log` 是 prompt 构建的主力源。
> session 中断重连后，`long_term.db` 成为唯一历史来源（此时 100 行限制生效）。

---

## 各参数调整的影响评估

| 调整 | 影响 | 代价 |
|------|------|------|
| 增大 `max_auto_continue_rounds` | agent 单轮可做更多连续工具调用 | LLM token 消耗增加，单轮变长 |
| 增大 `_trim_sqlite_history` max_rows | session 重连后保留更多历史 | 数据库略大，启动加载略慢 |
| 提高 `TOKEN_BUDGET_RATIO` | inbox 容纳更多历史消息 | system prompt + 工具定义的可用空间缩小 |
| 降低摘要触发阈值 | 更早触发压缩，降低上下文丢失风险 | 摘要 LLM 调用更频繁（有成本） |
| 更换摘要模型/提高摘要频率 | 历史压缩更可靠 | 额外 API 费用 |
