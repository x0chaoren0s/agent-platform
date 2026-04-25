# Agent Platform

一个基于 FastAPI、WebSocket 和 Microsoft Agent Framework 的多 Agent 协作平台。平台支持项目级团队管理、对话续聊、上下文压缩、团队共享记忆、工具调用展示，以及基于锚点的对话回滚检查点。本仓库用于取代此前在仓库外单独尝试的 `manga-crew` 实验项目（已废弃删除）。

## 主要功能

- 项目与团队管理：创建项目、切换项目、添加或解散团队成员。
- 对话管理：按项目保存多个对话，支持手动切换、重命名、删除和续聊。
- 流式聊天 UI：实时展示 Agent 回复，并预留 reasoning / thinking 过程气泡。
- 工具调用展示：从 Agent 输出中分离 `tool_call` 代码块，并折叠显示。
- 上下文管理：长对话使用滑动窗口与滚动摘要压缩早期上下文。
- 团队记忆：项目级 `context.md` 和 SQLite 知识库用于跨对话共享背景。
- 检查点回滚：在**用户或团队成员**任一气泡上可点 📌 设锚点；保存的是**该条消息出现之前**的对话（不含该条及之后，回滚后停在「上一条之后」）。侧边栏「＋ 创建检查点」为无锚点全量快照。
- 任务派发与依赖驱动的自动接力：`assign_task` / `submit_deliverable` 触发下游自动就绪。
- TaskBoard：聊天内嵌任务卡 + 顶部 Kanban 面板。
- 成员之间直接通信：`send_message` 自动 CC orchestrator，并带防洪水保护。
- ask_user 主动提问 + 待办收件箱：支持选项题/开放题，用户回答后自动回流给提问成员。
- 项目工作区：`workspace/` 存放交付物文件，任务状态落库到 `tasks.db`。
- 红色动作安全阀：`dismiss_member` / `recruit_fixed` / `update_project_context` 必须先 `ask_user` 获得确认 marker。
- 任务级真心跳：按任务优先级检测静默任务（high=10m / normal=30m / low=2h），并带 10 分钟 advisory 去抖；assignee 有发言则不算静默。
- 孤儿任务警告：解雇成员后，其任务自动转交 orchestrator，并推送转交 advisory；heartbeat 检测到无主任务时发出一次性孤儿告警（24h debounce）。
- 团队放假开关：线程级 pause/resume，暂停后心跳不再注入 advisory。
- 协调升级路径：orchestrator 按 `send_message -> update_task -> ask_user -> dismiss_member` 顺序升级干预。

## 目录结构

```text
agent-platform/
  core/                 # Agent 注册、路由、记忆、检查点、平台工具
  projects/             # 项目配置与 Agent YAML（运行数据已在 .gitignore 中排除）
    <project>/memory/tasks.db   # 任务与提问存储
    <project>/workspace/        # 交付物文件目录（默认忽略，仅保留 .gitkeep）
  web/                  # 单页前端 UI
  main.py               # FastAPI 入口
  requirements.txt      # Python 依赖（不锁版本）
```

## 环境变量

在项目根目录创建 `.env`：

```env
ARK_API_KEY=你的火山方舟 API Key
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=doubao-seed-2-0-pro-260215
AGENT_PROJECT=manga
```

`.env` 已加入 `.gitignore`，不要提交真实密钥。

## 安装与运行

```bash
python -m pip install -r requirements.txt
python main.py
```

启动后打开：

```text
http://localhost:8765
```

## Git 注意事项

仓库默认排除以下运行数据：

- `.env`
- `projects/*/memory/`
- `projects/*/sessions/`
- `projects/*/chat_log/`
- 本地实验项目 `projects/Interview/`、`projects/travel_plan/`

如需提交新的示例项目，建议只提交 `projects/<name>/agents/*.yaml` 这类可复用配置，不提交对话日志、数据库、会话文件或个人项目背景。

## 测试规范

- 参见 `docs/testing-conventions.md`，统一约束 sandbox 压测方式、业务化测试话术与复盘清单。
- 端到端测试 SOP：参见 `docs/test-sop-mvp-plus-plus.md`，从建项目到 DoD 全部 9 项的步骤化操作规范。
