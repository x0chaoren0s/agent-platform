# Agent Platform

一个基于 FastAPI、WebSocket 和 Microsoft Agent Framework 的多 Agent 协作平台。平台支持项目级团队管理、对话续聊、上下文压缩、团队共享记忆、工具调用展示，以及基于锚点的对话回滚检查点。本仓库用于取代此前在仓库外单独尝试的 `manga-crew` 实验项目（已废弃删除）。

## 主要功能

- 项目与团队管理：创建项目、切换项目、添加或解散团队成员。
- 对话管理：按项目保存多个对话，支持手动切换、重命名、删除和续聊。
- 流式聊天 UI：实时展示 Agent 回复，并预留 reasoning / thinking 过程气泡。
- 工具调用展示：从 Agent 输出中分离 `tool_call` 代码块，并折叠显示。
- 上下文管理：长对话使用滑动窗口与滚动摘要压缩早期上下文。
- 团队记忆：项目级 `context.md` 和 SQLite 知识库用于跨对话共享背景。
- 检查点回滚：可在用户消息上创建锚点检查点，之后将对话回滚到该消息位置。

## 目录结构

```text
agent-platform/
  core/                 # Agent 注册、路由、记忆、检查点、平台工具
  projects/             # 项目配置与 Agent YAML（运行数据已在 .gitignore 中排除）
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
