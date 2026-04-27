# Backlog

## P2 - skills 热插拔的 UI 闭环
- 提供 `GET /api/skills`：列出当前 project 的可用 skills（含 name/description）。
- 提供 `PUT /api/agents/{name}/skills`：为指定 agent 挂载/卸载 skills。
- 前端在成员详情页提供 skills 多选挂载控件，避免手改 yaml。
- 完成后补一条端到端验证：UI 修改后无需重启，agent prompt 与 `load_skill` 白名单同步生效。

## P3 - 跨团队协作（暂缓）
- 维持当前策略：项目隔离 + 老板手动跨项目协调。
- 触发真实痛点后再细分需求：跨项目只读、跨项目产出引用、专家借调、跨项目依赖。
- 暂不进入项目联邦化（高复杂度，当前收益不足）。
