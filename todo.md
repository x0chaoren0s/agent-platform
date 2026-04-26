# Task TODO

- [x] 1. 新建 `core/member_protocol.py`，提供成员任务协议与拼接函数
- [x] 2. 修改 `core/registry.py`：为固定成员注入协议并记录 `_effective_instructions`
- [x] 3. 修改 `core/team_tools.py`：增强 `depends_on` 错误信息 + `ready` 任务自动通知 assignee
- [x] 4. 修改 `main.py`：增强 orchestrator 的 `depends_on` 协议与示例
- [x] 5. 新增 `GET /api/agents/{name}/effective_prompt` 调试接口
- [x] 6. 本地验证与回归检查（5 项全部 PASS，服务器 log 确认 notify_assignee 触发）

## UI Inspector（B+2）

- [x] 1. 后端 `effective_prompt` 增加 `available_tools` 字段
- [x] 2. 新增工具目录映射（member/orchestrator）
- [x] 3. Web 新增 Agent Inspector 抽屉样式与容器
- [x] 4. Agent 卡片新增详情按钮并可打开 Inspector
- [x] 5. Inspector 支持“业务说明 / 完整提示词”切换
- [x] 6. 在 `agent-platform` conda 环境重启并完成接口冒烟验证
