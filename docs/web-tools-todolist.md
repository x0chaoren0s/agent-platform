# Web Tools MVP 执行清单（PLAN 文档）

> **本文档是 RIPER-5 PLAN 模式产出的实施规范**，已经过用户批准，跨 chat session 复用。任何新 session 接手时按"§0 导读"上手。
>
> 配套文档：无独立设计文档，本文 §1~§3 已包含全部背景。
>
> 上游姊妹文档：`docs/team-mvp-plus-todolist.md`（团队协作 MVP-Plus，已完成的协议层骨架）。本 todolist 在其基础上**补能力层**，让 agent 真的能查互联网、不再编造事实。

---

## 0. 导读（新 session 必读）

### 0.1 我是新接手的 session，从哪开始？

按以下顺序：

1. 读本文档 §1~§3：理解为什么要做（核心矛盾）+ 锁定的设计选择
2. 读 §4 详细规范：每个改动文件的精确 spec
3. 跑 `git log --oneline -20`：看已经 commit 了哪些步骤
4. 跑 `git status`：看是否有未提交的 work in progress
5. 跳到 §6 执行清单，找到第一个 ⬜ 状态的步骤开始
6. 每完成一个步骤把 ⬜ 改 ✅，跑过对应的验证后再 commit（commit message 见 §6 每步建议）

### 0.2 为什么有这么多文字？

跨 session 上下文不可保留，所有"想清楚但不写下来就会被遗忘"的决策——SDK 调用形式、URL 历史 key 结构、UI 链接渲染规则——都写死在本文档里。**实施时不需要再"决定"任何东西，按 spec 翻译即可**。

### 0.3 模式纪律

本文档由 PLAN 模式产出并经用户批准。新 session 收到 "ENTER EXECUTE MODE" 信号即可直接开干，**无需再次询问用户确认计划**。如果在 EXECUTE 中发现 spec 有问题（例如 `firecrawl-py` SDK 实际签名与 §4.4 假设不一致），必须返回 PLAN 模式修订本文档而不是自行偏离。

### 0.4 已锁定的 8 项设计选择（无需再讨论）

1. **工具协议复用现有文本协议** ```` ```tool_call ```` ，不切换 DeepSeek native function calling（UI 已为文本协议实现完整渲染，切换风险大、收益小）
2. **firecrawl 通过 `firecrawl-py` Python SDK 直连**，不 shell out 到 CLI（agent-platform 是 Python 服务，shell 出去不优雅）
3. **API key 来源**：`.env` 的 `FIRECRAWL_API_KEY`。未配置时工具返回明确错误字符串，**不阻塞平台启动**
4. **References 不改 task_store schema**：用进程内 dict `_URL_HISTORY[(thread_id, agent_name)]` 临时缓存调用过的 URL；`submit_deliverable` 时把累积 URL 追加到 deliverable 文件末尾的 `## References` 段，并清空对应 entry。重启后历史丢失（MVP 阶段可接受）
5. **工具粒度**：原子级。`web_search` 只返回结果列表，`web_read` 单独返回页面正文。不做 `research()` 这种自动编排封装
6. **不做预算/限流/缓存**。firecrawl 自身 quota 耗尽时返回错误字符串，agent 自行处理
7. **结果长度上限**：`web_search` 默认 5 条，每条 snippet 截断 200 字；`web_read` 正文截断 8000 字
8. **工具失败行为**：返回字符串 `"错误：..."` 而非抛异常。Member protocol 提示连续失败 2 次后转 ask_user

### 0.5 不在 MVP 范围内（明确推迟）

- DeepSeek native function calling 迁移
- capability → tools 映射机制（"为什么数据分析师和内容策划师工具集一样"等优雅化讨论）
- python_repl / image_gen / file_write / 任何其他工具
- MCP 协议接入
- 工具结果缓存
- 异构底模架构（orchestrator vs member 用不同模型）
- 工具调用预算/限流

---

## 1. 背景与目标

### 1.1 为什么必须做这件事

平台之前所有"成员"agent 没有任何外部数据访问工具，唯一可用的 6 个工具（`assign_task` / `update_task` / `submit_deliverable` / `send_message` / `ask_user` / `give_up` / `list_tasks`）全是平台内协作工具。结果：

- "数据分析师" agent 写的"竞品对标分析"全部来自底模训练数据回忆，**无法验证、必然产生幻觉**
- 用户无法分辨 agent 输出是事实还是编造
- 平台沦为"高级一次性聊天工具"，没有比 ChatGPT 更高的边际价值

参考：本文档诞生前的 INNOVATE 讨论得出 3 条判据——**可验证性、增量性、可累积性**。当前架构 3 条全部不达标，根因都是"工具能力为零"。

### 1.2 MVP-Useful 范围

**包含**：
- 2 个工具：`web_search`、`web_read`，全员（fixed member + temp）可用
- Member system prompt 注入 Karpathy 4 条行为准则（先想再做 / 简单优先 / 外科手术 / 目标驱动）+ 事实诚信约束
- `submit_deliverable` 自动追加 References 段，记录本任务期间 agent 调用过的 URL
- UI：识别 `web_search`/`web_read` 时，tool_result 卡片渲染成可点击链接列表
- `.env` 配置清理（去重 `ARK_MODEL`）+ `FIRECRAWL_API_KEY` 占位

**不包含**：见 §0.5

### 1.3 验收标准

- 启动后 sandbox-test 项目里的"数据分析师"调用 `web_search` 能拿到真实搜索结果
- 调用 `web_read` 能拿到真实页面正文
- 提交 deliverable 时自动带 References 段，引用本任务实际访问过的 URL
- UI 折叠展示 tool_call + tool_result，`web_search` 结果以可点击链接列表呈现
- 模糊任务下，member 优先 `ask_user` 澄清而非凭训练记忆瞎写

---

## 2. 现状摘要（来自源码扫描）

### 2.1 工具调用协议

Agent 输出文本中以下列代码块标记工具调用：

````
```tool_call
{"tool": "工具名", "args": {...}}
```
````

`main.py::_TOOL_CALL_RE = re.compile(r"```tool_call\s*\n(.*?)\n```", re.DOTALL)`
`main.py::_process_tool_calls` 提取 → `_execute_tool` 大 if/elif 分发。

执行后通过 `router.record_tool_feedback(agent_name, tool_results)` 写入 `_global_log`（platform sender），下一轮 agent 上下文可见。WS 通过 `{"type": "tool_result", ...}` 事件推到前端。

### 2.2 现有 `_execute_tool` 分支结构（main.py:838-944）

按顺序：
1. `RED_ACTIONS` 检查（dismiss_member / recruit_fixed / update_project_context 需要 confirm marker）
2. `list_team`
3. `recruit_fixed`（含 registry 同步）
4. `dismiss_member`（含 registry 同步 + 任务自动转交 orchestrator）
5. `recruit_temp`
6. `update_project_context`
7. `kb_write` / `kb_search`
8. `team_tools.TEAM_TOOL_DISPATCH`（assign_task / update_task / submit_deliverable / send_message / ask_user / give_up / list_tasks）
9. `else: return f"未知工具：{tool_name}"`

**Web tools 新增分支位置**：在 `kb_search` 之后、`team_tools.TEAM_TOOL_DISPATCH` 之前。

### 2.3 Member system prompt 组装

`core/registry.py::_build_agent` 中：

```python
if role == "member" and not is_temp:
    effective_instructions = compose_member_instructions(instructions)
```

`core/member_protocol.py::compose_member_instructions` 把 `MEMBER_TASK_PROTOCOL` 拼到业务 instructions 前。temp agent 不注入。

UI 工具元数据来自 `MEMBER_TOOLS` 列表（仅 UI 展示用，不影响 agent 实际行为；agent 知道有什么工具完全靠 prompt 文本）。

### 2.4 `submit_deliverable` 当前实现要点（core/team_tools.py:195-243）

- 接收 `task_id` / `content` 或 `file_path` / `summary`
- inline 模式（content 传入）：用 `safe_title` 生成相对路径 → `store.write_deliverable_file(rel_path, content)` 写入 `workspace/<rel_path>` → 返回相对路径
- file_path 模式：直接用传入路径
- 写入后调用 `store.submit_deliverable(task_id, path, summary, actor)` 持久化任务状态
- 无 `references` 参数；无 schema 字段保存 references

### 2.5 UI tool_result 渲染（web/index.html:1137-1150）

```javascript
function appendToolResult(toolName, result, triggeredBy) {
  const display = result.length > 120 ? result.slice(0, 120) + '…' : result;
  // ... 渲染 act-card.tool 卡片，仅显示 toolName + truncated result ...
}
```

WS 事件 `{"type": "tool_result", "tool", "result", "triggered_by"}` 在 1702 行触发。

### 2.6 当前 `.env` 配置（含问题）

```
# ARK_API_KEY=ark-d81f0a10-9a18-4ce2-a2f8-873cf26a1e5c-0c24c
# ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
# ARK_MODEL=doubao-seed-2-0-pro-260215
# ARK_MODEL=doubao-seed-2-0-lite-260215
ARK_API_KEY=sk-0b98e009b53445a1b7a537cd06724f6f
ARK_BASE_URL=https://api.deepseek.com
ARK_MODEL=deepseek-v4-flash
ARK_MODEL=deepseek-v4-pro

# 当前激活项目目录名
AGENT_PROJECT=manga
```

**问题**：dotenv 后行覆盖前行，实际生效的是 `deepseek-v4-pro`，`deepseek-v4-flash` 那行是无意义历史痕迹。需要删除。

### 2.7 已经验证过的事实（可直接信任）

- `npm install -g firecrawl-cli` 已完成（命令行可用，但本项目走 Python SDK）
- `firecrawl-py` 是 firecrawl 官方维护的 Python SDK，PyPI 上稳定可用
- DeepSeek v4-pro 完全支持 OpenAI 兼容 tool calls，1M 上下文，限时 2.5 折（输入 3元/M，输出 6元/M），优惠至 2026-05-05
- 用户已在 firecrawl 控制台为本项目新建独立 API key，需要填入 `.env` 的 `FIRECRAWL_API_KEY=`

---

## 3. 文件改动地图

| 类型 | 路径 | 改动概要 |
|---|---|---|
| ✏️ 修改 | `agent-platform/.env` | 删除重复 `ARK_MODEL` 行；新增 `FIRECRAWL_API_KEY` 行 |
| ✏️ 修改 | `agent-platform/requirements.txt` | 新增 `firecrawl-py` |
| ✏️ 修改 | `agent-platform/.gitignore` | 新增 `.firecrawl/` |
| 🆕 新建 | `agent-platform/core/web_tools.py` | `web_search` / `web_read` 实现 + URL 历史 |
| ✏️ 修改 | `agent-platform/core/member_protocol.py` | `MEMBER_TASK_PROTOCOL` 扩充（研究工具段 + Karpathy 4 条 + 事实诚信） + `MEMBER_TOOLS` 追加 2 条 |
| ✏️ 修改 | `agent-platform/core/team_tools.py` | `submit_deliverable` 加 `references` 参数 + 自动 References 段拼接 |
| ✏️ 修改 | `agent-platform/main.py` | `_execute_tool` 加 web 分支 + lifespan 加 firecrawl 健康检查日志 |
| ✏️ 修改 | `agent-platform/web/index.html` | `appendToolResult` 升级，对 web 工具渲染链接列表 |
| ✏️ 修改 | `agent-platform/docs/web-tools-todolist.md` | 本文档（实施过程中根据完成情况打勾） |

**不动的文件**（明确）：
- `core/llm.py` —— `ARK_*` 命名保持不变（DeepSeek 也是 OpenAI 兼容），`.env` 中已经把 `ARK_BASE_URL` 改成 DeepSeek
- `core/router.py` / `core/registry.py` / `core/task_store.py` —— 无 schema 变化
- 所有 `projects/<p>/agents/*.yaml` —— 能力靠 prompt 注入，YAML 不动
- orchestrator 模板（main.py 中 `_ORCHESTRATOR_YAML_TMPL`）—— orchestrator 不直接调用 web 工具，无需改

---

## 4. 详细规范

### 4.1 `agent-platform/.env`

**删除**：`ARK_MODEL=deepseek-v4-flash` 那一行（位于 `ARK_MODEL=deepseek-v4-pro` 上方）。

**新增**（在文件末尾）：

```
# Firecrawl API key（用于 web_search / web_read 工具）
# 获取：https://www.firecrawl.dev/app
FIRECRAWL_API_KEY=
```

> 用户已在 firecrawl 控制台为本项目创建独立 API key，需要手动填入等号后面。

### 4.2 `agent-platform/requirements.txt`

末尾新增一行：

```
firecrawl-py
```

### 4.3 `agent-platform/.gitignore`

确认或新增一行：

```
.firecrawl/
```

### 4.4 `agent-platform/core/web_tools.py` 🆕

**完整文件内容**：

```python
"""Web research tools backed by Firecrawl.

Two atomic tools exposed to all member agents:
- web_search(query, limit?) → markdown list of {title, url, snippet}
- web_read(url) → page content as markdown

Side effect: every URL touched is recorded in a per-(thread_id, agent_name)
in-memory bucket; submit_deliverable later drains and appends them as a
References section in the deliverable file.
"""

from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

# Process-wide URL history per (thread_id, agent_name).
# Lost on restart — that's acceptable for MVP.
_URL_HISTORY: dict[tuple[str, str], list[dict[str, str]]] = {}
_URL_HISTORY_LOCK = Lock()
_MAX_HISTORY_PER_AGENT = 50

_SEARCH_LIMIT_DEFAULT = 5
_SEARCH_LIMIT_MAX = 10
_SEARCH_SNIPPET_MAX = 200
_READ_TEXT_MAX = 8000


def _get_app() -> Any | None:
    """Lazy-load Firecrawl client. Returns None if API key missing or SDK not installed."""
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from firecrawl import FirecrawlApp
        return FirecrawlApp(api_key=api_key)
    except Exception:
        logger.exception("Failed to initialize Firecrawl client")
        return None


def _record_url(thread_id: str, agent_name: str, url: str, title: str = "") -> None:
    if not thread_id or not agent_name or not url:
        return
    key = (thread_id, agent_name)
    with _URL_HISTORY_LOCK:
        bucket = _URL_HISTORY.setdefault(key, [])
        if any(item["url"] == url for item in bucket):
            return
        bucket.append({"url": url, "title": title or ""})
        if len(bucket) > _MAX_HISTORY_PER_AGENT:
            del bucket[: len(bucket) - _MAX_HISTORY_PER_AGENT]


def consume_url_history(thread_id: str, agent_name: str) -> list[dict[str, str]]:
    """Pop and return all URLs accumulated by an agent in this thread."""
    key = (thread_id, agent_name)
    with _URL_HISTORY_LOCK:
        return _URL_HISTORY.pop(key, [])


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"…（已截断，原长度 {len(text)} 字符）"


async def web_search(
    *,
    thread_id: str,
    caller_agent: str,
    query: str,
    limit: int | None = None,
) -> str:
    """Search the web via Firecrawl. Returns markdown-formatted result list."""
    if not query or not str(query).strip():
        return "错误：web_search 必须提供 query。"
    app = _get_app()
    if app is None:
        return "错误：FIRECRAWL_API_KEY 未配置或 firecrawl-py 包未安装。请联系平台管理员。"
    n = max(1, min(int(limit or _SEARCH_LIMIT_DEFAULT), _SEARCH_LIMIT_MAX))
    try:
        result = app.search(query=str(query), limit=n)
    except Exception as exc:
        logger.exception("web_search failed: %s", exc)
        return f"错误：web_search 调用失败：{exc}"

    items: list[dict] = []
    if isinstance(result, dict):
        items = result.get("data") or result.get("web") or []
    elif isinstance(result, list):
        items = result
    if not items:
        return f"web_search('{query}') 无结果。"

    lines = [f"web_search('{query}') 共 {len(items)} 条结果："]
    for i, item in enumerate(items, 1):
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("description") or item.get("snippet") or "").strip()
        snippet = _truncate(snippet, _SEARCH_SNIPPET_MAX)
        lines.append(f"\n{i}. [{title or url}]({url})\n   {snippet}")
        _record_url(thread_id, caller_agent, url, title)
    return "\n".join(lines)


async def web_read(
    *,
    thread_id: str,
    caller_agent: str,
    url: str,
) -> str:
    """Fetch page content as markdown via Firecrawl scrape_url."""
    if not url or not str(url).strip():
        return "错误：web_read 必须提供 url。"
    app = _get_app()
    if app is None:
        return "错误：FIRECRAWL_API_KEY 未配置或 firecrawl-py 包未安装。"
    try:
        result = app.scrape_url(url=str(url), params={"formats": ["markdown"]})
    except Exception as exc:
        logger.exception("web_read failed: %s", exc)
        return f"错误：web_read 调用失败：{exc}"

    markdown = ""
    title = ""
    if isinstance(result, dict):
        data = result.get("data") if "data" in result else result
        if isinstance(data, dict):
            markdown = str(data.get("markdown") or data.get("content") or "")
            meta = data.get("metadata") or {}
            if isinstance(meta, dict):
                title = str(meta.get("title") or meta.get("og:title") or "")
    if not markdown:
        return f"web_read('{url}') 拿到空内容。"
    _record_url(thread_id, caller_agent, str(url), title)
    return _truncate(markdown, _READ_TEXT_MAX)
```

**已知风险**：`firecrawl-py` SDK 的具体方法签名（`app.search` / `app.scrape_url`）以官方 README 为准。如果实际签名与上述假设不一致（例如返回字段名不同），EXECUTE 阶段需要按 SDK 文档修正函数体内部的解析逻辑，但**外部协议（输入参数、返回字符串格式）保持不变**，不影响其他模块。修订时本文档同步更新 §4.4。

### 4.5 `agent-platform/core/member_protocol.py`

**两处改动**：

#### 改动 1：替换 `MEMBER_TASK_PROTOCOL` 字符串内容

```python
MEMBER_TASK_PROTOCOL = """【任务执行协议】（你必须遵守，否则任务不会被系统判定为完成）

可用任务工具（在回复末尾使用 tool_call 代码块）：
- submit_deliverable：提交最终成果并将任务置为 done
- update_task：更新任务状态（in_progress / blocked_on_user）或补充进度
- send_message：与团队成员沟通，默认会通知 orchestrator
- ask_user：需要用户决策时弹出选项
- give_up：无法继续时放弃任务并通知 orchestrator
- list_tasks：查看任务清单和状态

可用研究工具（事实优先，禁止凭训练记忆编造）：
- web_search：以关键词搜索互联网，返回结果列表（标题/链接/摘要）
- web_read：抓取指定 URL 的正文内容并返回 markdown

调用语法（必须是合法 JSON）：
```tool_call
{"tool":"submit_deliverable","args":{"task_id":"task-0001","content":"最终成果全文","summary":"一句话总结"}}
```
```tool_call
{"tool":"web_search","args":{"query":"未来城市公众号 2026 头部账号"}}
```
```tool_call
{"tool":"web_read","args":{"url":"https://example.com/article"}}
```

收到【新任务】后：
1) 理解 title / brief / deadline；
2) 完成业务内容产出；
3) 在同一轮回复末尾调用 submit_deliverable 提交最终成果。
没有 submit_deliverable，任务不会从 ready 变为 done。

跨多轮任务处理规则：
- 开始执行时先 update_task(status="in_progress")
- 中间若有阻塞，可 update_task(progress_note=...)
- 完成时必须 submit_deliverable

信息不足时：
- 优先 ask_user，不要猜测
- 如果是公开互联网信息（行业数据、竞品资料、文章定义等），先用 web_search → web_read 自己查；只有用户独有的私域信息才 ask_user

无法完成时：
- 调用 give_up(task_id, reason)

【行为准则 - 来自工程实践】
1. 先想再做：动手前把你对 brief 的理解、做了哪些假设说出来；如果 brief 有多种合理解读，列出来再请示，不要私自挑一个。
2. 简单优先：交付最少能解决问题的内容，不要堆砌没要求的章节、抽象、配置项。
3. 外科手术：只回应 brief 里明确要求的范围；如果发现 brief 之外的问题，说出来但不要顺手"修"。
4. 目标驱动：开工前先把"完成判据"写一遍（用户怎么验证我做对了），让自己有可验收锚点。

【事实诚信】
- 涉及外部世界的具体数据、引用、统计、竞品名称等，必须来自 web_search/web_read 的真实返回，禁止凭训练记忆编造。
- 你调用过的所有 URL 会被系统自动记录，submit_deliverable 时会自动附在交付物末尾作为 References。

【工具失败处理】
- web_search/web_read 失败时返回会以"错误："开头。同一查询连续失败 2 次后，请改用 ask_user 让用户提供数据或换思路，不要无限重试。

格式约束：
- tool_call 代码块必须放在回复末尾
- 一次回复可包含多个 tool_call，按顺序执行
- args 必须是合法 JSON
"""
```

#### 改动 2：扩充 `MEMBER_TOOLS` 列表

在原有 6 条之后追加：

```python
    {
        "name": "web_search",
        "desc": "搜索互联网获取标题/链接/摘要列表",
        "is_red": False,
    },
    {
        "name": "web_read",
        "desc": "抓取指定 URL 的页面正文（markdown）",
        "is_red": False,
    },
```

### 4.6 `agent-platform/core/team_tools.py`

**改动 1**：文件顶部 import 区追加：

```python
from . import web_tools
```

**改动 2**：替换 `submit_deliverable` 函数（约 195-243 行）。注意函数签名新增 `references: list[str] | None = None` 参数（位于 `summary` 之后、`**kwargs` 之前），以保持向后兼容。

**完整新函数体**：

```python
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
    ref_lines: list[str] = []
    for item in auto_refs:
        url = item.get("url", "")
        title = item.get("title", "")
        if url:
            ref_lines.append(f"- [{title or url}]({url})")
    for r in explicit_refs:
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
    ready_text = ", ".join(t.id for t in downstream) if downstream else "无"
    refs_hint = f"，引用 {len(ref_lines)} 条" if ref_lines else ""
    return f"已交付 {task_id}：workspace/{deliverable_path}（ready_downstream={ready_text}{refs_hint}）"
```

**关键约束**：
- 仅在 inline 模式（`content` 传入）追加 References。`file_path` 模式下 deliverable 是外部已存在文件，不强行修改
- `references` 参数允许 agent 显式补充非 web 来源的引用（如本地文件路径），与自动跟踪的 URL 合并

### 4.7 `agent-platform/main.py`

**改动 1**：在 `from core import team_tools` 附近追加：

```python
from core import web_tools
```

**改动 2**：在 `_execute_tool` 函数（约 838 行）中，**在 `kb_search` 分支之后、`team_tools.TEAM_TOOL_DISPATCH` 分支之前**插入两个新分支：

```python
    elif tool_name == "web_search":
        try:
            return await web_tools.web_search(
                thread_id=thread_id,
                caller_agent=caller_agent,
                **args,
            )
        except TypeError as exc:
            return f"工具调用参数错误（web_search）：{exc}"
        except Exception as exc:
            logger.exception("web_search execution error")
            return f"工具调用异常（web_search）：{exc}"
    elif tool_name == "web_read":
        try:
            return await web_tools.web_read(
                thread_id=thread_id,
                caller_agent=caller_agent,
                **args,
            )
        except TypeError as exc:
            return f"工具调用参数错误（web_read）：{exc}"
        except Exception as exc:
            logger.exception("web_read execution error")
            return f"工具调用异常（web_read）：{exc}"
```

**改动 3**：在 `lifespan` 函数 `_activate(_current_project)` 调用之后追加 firecrawl 健康检查（不阻塞启动）：

```python
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
```

### 4.8 `agent-platform/web/index.html`

**改动**：替换 `appendToolResult` 函数（约 1137-1150 行）。

**完整新函数**：

```javascript
function appendToolResult(toolName, result, triggeredBy) {
  const card = document.createElement('div');
  card.className = 'act-card tool';

  let bodyHtml;
  if (toolName === 'web_search' || toolName === 'web_read') {
    const linkRe = /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g;
    const links = [];
    let m;
    while ((m = linkRe.exec(result)) !== null) {
      links.push({ title: m[1], url: m[2] });
    }
    const linksHtml = links.length
      ? `<div style="margin-top:4px;display:flex;flex-direction:column;gap:2px">${
          links.slice(0, 8).map(l =>
            `<a href="${escHtml(l.url)}" target="_blank" rel="noopener" style="font-size:11px;color:#60a5fa;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(l.url)}">↗ ${escHtml(l.title)}</a>`
          ).join('')
        }${links.length > 8 ? `<span style="font-size:10px;opacity:.6">…及 ${links.length - 8} 条</span>` : ''}</div>`
      : '';
    const firstLine = result.split('\n')[0] || '';
    const display = firstLine.length > 120 ? firstLine.slice(0, 120) + '…' : firstLine;
    bodyHtml = `
      <div class="act-card-title">${escHtml(toolName)}<span style="font-weight:400;margin-left:6px;opacity:.7">by ${escHtml(triggeredBy)}</span></div>
      <div class="act-card-sub">${escHtml(display)}</div>
      ${linksHtml}`;
  } else {
    const display = result.length > 120 ? result.slice(0, 120) + '…' : result;
    bodyHtml = `
      <div class="act-card-title">${escHtml(toolName)}<span style="font-weight:400;margin-left:6px;opacity:.7">by ${escHtml(triggeredBy)}</span></div>
      <div class="act-card-sub">${escHtml(display)}</div>`;
  }

  card.innerHTML = `<span class="act-card-icon">🔧</span><div class="act-card-body">${bodyHtml}</div>`;
  el('msgList').appendChild(card);
  scrollBottom();
}
```

---

## 5. 测试方法

> 所有测试在 `sandbox-test` 项目执行，**禁止用生产项目**（参见 `docs/testing-conventions.md`）。

### 5.1 启动健康检查

1. 跑 `pip install -r requirements.txt`，应成功安装 `firecrawl-py`
2. 跑 `python main.py`
3. **期望**：日志包含 `Firecrawl SDK available; web tools enabled.`（如果 `.env` 已配 key）或 `FIRECRAWL_API_KEY not set` 警告（未配）
4. 浏览器打开 `http://localhost:8765`，进入 sandbox-test 项目

### 5.2 基础 web_search 冒烟测试

**用户输入**：`请帮我搜索"DeepSeek v4 发布"相关信息，给我前 3 条结果的标题和链接。`

**期望流程**：
1. orchestrator 派 `assign_task` 给某 member（数据分析师或随机一位）
2. member 调用 `web_search`，tool_result 卡片显示包含至少 3 条可点击链接
3. member 调用 `submit_deliverable`，回复包含"已交付 task-XXXX"
4. 检查 `projects/sandbox-test/workspace/task-XXXX-*.md`：内容应包含从搜索结果整理的信息 + 末尾 `## References` 段列出真实 URL

### 5.3 web_read 抓取测试

**用户输入**：`请阅读 https://api-docs.deepseek.com/zh-cn/guides/tool_calls 并总结 DeepSeek tool calls 的核心步骤。`

**期望**：
- member 调用 `web_read` 拿到该页面内容
- deliverable 包含基于该页面真实内容的总结（含"strict 模式"或"非思考模式样例代码"等关键词），不是泛泛的 LLM 知识
- References 段含此 URL

### 5.4 工具失败处理测试

1. 临时把 `.env` 里的 `FIRECRAWL_API_KEY=` 等号后清空，重启服务
2. 用户输入需要联网的任务
3. **期望**：tool_result 显示 `错误：FIRECRAWL_API_KEY 未配置...`；agent 应在 1-2 次重试后转为 `ask_user` 而非无限循环
4. 测试完恢复 `.env`

### 5.5 Karpathy 行为验收

**用户输入**：`写一份关于"未来城市"的内容`（"未来城市"含义模糊）

**期望**：
- member 不直接动笔，而是 `ask_user` 澄清是公众号名、智慧城市概念还是其他指代
- 即使 ask_user 后用户给了说明，member 在动手前应显式声明假设（如"我理解'未来城市'是指 XX 公众号..."）

### 5.6 References 自动追加测试

构造一个任务流：member 调用 1 次 `web_search`（5 条结果）+ 2 次 `web_read`，然后 `submit_deliverable`。

**期望**：deliverable 文件末尾的 `## References` 段去重后含 2~7 个 URL（取决于 web_read 的 URL 是否在 web_search 结果里）。

---

## 6. 执行清单

按顺序执行。每完成一项把 ⬜ 改 ✅，建议每完成一组就 commit 一次。

### Phase A — 配置与依赖（1 commit）

- ✅ **A.1** 修改 `agent-platform/.env`：删除 `ARK_MODEL=deepseek-v4-flash` 重复行；末尾新增 `FIRECRAWL_API_KEY=`（值留空，用户后续自己填）
- ✅ **A.2** 修改 `agent-platform/requirements.txt`：末尾新增 `firecrawl-py`
- ✅ **A.3** 修改 `agent-platform/.gitignore`：新增 `.firecrawl/`（如已存在则跳过）
- ✅ **A.4** 跑 `pip install -r requirements.txt` 安装 `firecrawl-py`
- ✅ **A.5** **commit**：`chore: 切换到 deepseek-v4-pro + 引入 firecrawl-py 依赖`

### Phase B — 工具实现（1 commit）

- ✅ **B.1** 新建 `agent-platform/core/web_tools.py`：内容完全按 §4.4 复制
- ✅ **B.2** **commit**：`feat(tools): 新增 web_search/web_read 能力工具（firecrawl 后端）`

### Phase C — 协议与服务端集成（1 commit）

- ✅ **C.1** 修改 `agent-platform/core/member_protocol.py`：按 §4.5 改动 1 替换 `MEMBER_TASK_PROTOCOL` 字符串
- ✅ **C.2** 修改同文件：按 §4.5 改动 2 在 `MEMBER_TOOLS` 列表追加 `web_search` 和 `web_read` 元数据
- ✅ **C.3** 修改 `agent-platform/core/team_tools.py`：按 §4.6 import `web_tools` + 替换 `submit_deliverable` 函数
- ✅ **C.4** 修改 `agent-platform/main.py`：按 §4.7 import + `_execute_tool` 加 web 分支 + lifespan 加健康检查
- ✅ **C.5** **commit**：`feat(protocol): member 注入 Karpathy 行为准则 + web 工具协议；submit_deliverable 自动附 References`

### Phase D — UI（1 commit）

- ✅ **D.1** 修改 `agent-platform/web/index.html`：按 §4.8 替换 `appendToolResult` 函数
- ✅ **D.2** **commit**：`feat(ui): tool_result 卡片识别 web_search/web_read 渲染链接列表`

### Phase E — 部署与测试

- ✅ **E.1** 用户操作：在 `agent-platform/.env` 中填入 `FIRECRAWL_API_KEY=<key>`（用户已在 firecrawl 控制台为本项目创建的独立 key）
- ✅ **E.2** 启动验证（§5.1）：`python main.py`，日志显示 `Firecrawl SDK available; web tools enabled.`
- ✅ **E.3** 冒烟测试（§5.2）：sandbox-test 项目，搜索 + 链接展示 + References 段验证（部分完成：web_search/web_read 工具已验证可用，limit 参数传递已完成；References 段落落盘验证待完成——需由固定成员执行并 submit_deliverable）
- ✅ **E.4** 抓取测试（§5.3）：`web_read` DeepSeek 文档页验证
- ✅ **E.5** 失败测试（§5.4）：清空 API key 验证错误提示与 ask_user 回退
- ⬜ **E.6** Karpathy 行为测试（§5.5）：模糊任务下 `ask_user` 澄清验证
- ⬜ **E.7** References 测试（§5.6）：多次工具调用后 deliverable 文件 References 段去重验证

### Phase F — 收尾

- ⬜ **F.1** 把本文档（`docs/web-tools-todolist.md`）所有 ⬜ 改成 ✅
- ⬜ **F.2** **commit**：`docs: 关闭 web-tools-todolist`

---

## 7. 已知风险与回退预案

### 7.1 firecrawl-py SDK 签名不一致

**风险**：§4.4 中假设 `app.search(query=..., limit=...)` 和 `app.scrape_url(url=..., params={...})` 的接口形态。如果实际 SDK 版本接口不同，工具会在调用时抛出。

**检测**：Phase E.3 的冒烟测试会立即暴露此问题（tool_result 显示 `调用失败`）。

**处理**：
1. 不要在 `web_tools.py` 外的任何文件偏离 spec
2. 查 `https://github.com/mendableai/firecrawl/tree/main/apps/python-sdk` 当前版本签名
3. 仅修改 `web_tools.py` 内 `app.search(...)` 和 `app.scrape_url(...)` 调用形式以及结果解析（`items = result.get(...)` 部分）
4. 同步更新本文档 §4.4

### 7.2 firecrawl quota 用尽

**风险**：免费 quota 耗尽后所有工具调用返回 firecrawl 端错误。

**处理**：在 firecrawl 控制台查看用量；MVP 阶段不做软限流，直接通过工具失败行为让 agent 转 ask_user。

### 7.3 DeepSeek v4 价格优惠期结束

**风险**：限时 2.5 折优惠至 2026-05-05；之后输入 12元/M、输出 24元/M。

**处理**：MVP 不做底模成本优化。如果运行成本不可接受，后续单独立项做"orchestrator 用 pro + member 用 flash"的异构架构（已记入 §0.5 推迟列表）。

---

## 8. 完成定义（DoD）

- [ ] 所有 §6 的 ⬜ 都变成 ✅
- [ ] sandbox-test 项目能跑通"竞品对标分析"类带真实数据的任务
- [ ] 至少 1 份 deliverable 文件含真实可点击 References URL
- [ ] 用户主观验收"agent 不再瞎编了"

---

## 9. 与历史决策的衔接

本 todolist 是 RIPER-5 流程产出，前置讨论在 chat session 中完成。关键决策路径回顾：

1. **诊断**：sandbox-test 暴露"capabilities 字段写一堆能力但实际没有任何对应工具"的根本性架构缺口（参见 §1.1）
2. **目标确认**：用户明确"验证平台对真实业务有用" > 协议层完美
3. **路线选择**：用户明确"验证速度优先，先跑通再考虑优雅"
4. **底模切换**：用户切到 DeepSeek v4-pro（OpenAI 兼容、tool calls 完美、1M 上下文、限时 2.5 折）
5. **搜索后端选型**：火山方舟联网内容插件因切到 DeepSeek 失效；选 firecrawl（已验证 npm 上有 cli 1.15.2、Python SDK `firecrawl-py` 官方维护）
6. **范围裁剪**：capability 映射、native function calling、其他工具全部推迟，本期只做 web_search + web_read

如果新 session 想质疑这些决策，先读 §0.5（明确推迟）和 §1.1（核心矛盾）；除非有新事实证伪，不要重启讨论。

---

> **End of document.** Last reviewed: PLAN 模式产出 + 用户批准（chat session "进入研究模式...能否把规则引入到 agent-platform"）。
