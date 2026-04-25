# MVP-Plus++ 端到端测试 SOP

**版本**：2026-04-25  
**覆盖 DoD**：红色拦截 / 红色放行 / 心跳 advisory / 心跳去抖 / 团队暂停 / 协调三连 / A2/C2 参数错误提示 / C1 空气泡修复 / A1 文档  
**预计耗时**：约 25 ~ 40 分钟（心跳部分需等待真实超时，可跳过用 debug 快捷步骤代替）

---

## 0. 前置条件

1. 服务已启动：`python main.py`，确认 `http://localhost:8765` 可打开。
2. 浏览器打开控制台（F12 → Console + Network），方便观察 WebSocket 事件。
3. 确认 **全新建一个测试项目**，不要在 `opc` / `manga` / `Interview` 旧项目上操作，以免污染历史数据。

---

## 1. 建立测试项目与团队

### 1.1 新建项目

1. 在侧边栏点 **＋** 新建项目，名称填 `sandbox-test`。
2. 确认切换到 `sandbox-test`，侧边栏显示 "orchestrator" 为唯一成员。

### 1.2 让 orchestrator 招募两名成员

在收件人选择 **orchestrator**（To），发送以下消息：

```
我们要做一个公众号内容策划项目"未来城市"。请先招募两名成员：
1. 内容策划师：负责选题、写作大纲、内容产出
2. 数据分析师：负责阅读数据分析与竞品分析
招募前请先向我确认方案。
```

**预期**：orchestrator 先用 `ask_user` 弹出招募方案确认卡，等你选择"确认"后，调用 `recruit_fixed` 两次（各弹出一次红色操作确认）。

> ⚠️ **当前行为应该是**：orchestrator 会先 `ask_user` 弹 `[[confirm:recruit:内容策划师]]`，你回答 yes 后才会招募成功。如果 orchestrator 直接调 `recruit_fixed` 而没有 ask_user，服务器会返回拒绝提示，并让 orchestrator 重新走确认流程。这就是**红色操作验证**（DoD #1/#2）的第一次自然触发机会。

**记录**：
- [ ] orchestrator 发起了 `ask_user` confirm 卡 → **有 marker 字段**？（例如 `[[confirm:recruit:内容策划师]]`）
- [ ] 你点 "确认（yes）" 后招募成功？
- [ ] 如果 orchestrator 跳过了 ask_user 直接招募，服务器返回了拦截错误字符串？

---

## 2. 验证 C2 / A2 — 参数错误提示

团队建好后，**你直接发消息给 orchestrator**（不用经过正常流程，目的是触发错误）：

```
请你帮我测试一下，故意调用一次 assign_task，只填 assignee 字段，不填 title 和 brief，看看系统返回什么。
```

**预期**：orchestrator 尝试调 `assign_task`，服务器返回类似：

```
错误：assign_task 必须包含 title 字段，请检查 args。
建议立即调用 list_tasks(scope='mine') 查看当前任务实际状态，避免基于错误假设继续推进。
```

**记录**：
- [ ] 错误字符串中含 `title` 或 `brief` 字段名 hint？
- [ ] 错误字符串末尾含 `list_tasks` 自检建议？

---

## 3. 验证 C1 — 空气泡修复

让 orchestrator 做一次仅含工具调用的输出（查询团队状态）：

```
请你直接调用 list_tasks 和 list_team，不用加其他解释，只输出工具调用。
```

**预期**：orchestrator 的气泡里只有"🔧 工具调用"折叠区，**没有空白的对话气泡**（不会出现一个高度只有几像素、内容为空的灰色气泡）。

**记录**：
- [ ] 消息渲染正常，无空气泡？

---

## 4. 分配任务并验证正常流程

发送给 orchestrator：

```
好，现在分配任务：
- 请内容策划师调研"城市更新政策对年轻人的影响"这一选题，产出选题大纲（priority: high）
- 请数据分析师分析过去 3 个月同类话题的阅读量趋势（priority: normal）
```

**预期**：orchestrator 分两次调用 `assign_task`，每次的 `title`/`brief`/`assignee`/`priority` 都填齐，创建成功后在聊天窗口出现两张任务卡。

**记录**：
- [ ] 两张任务卡出现，状态为 `ready`？
- [ ] task-0001 → 内容策划师，priority=high
- [ ] task-0002 → 数据分析师，priority=normal

---

## 5. 验证红色动作拦截（主流程）

**场景**：你认为内容策划师表现不好，想让 orchestrator 解雇他，但 orchestrator **没有走正确流程**。

发送给 orchestrator：

```
我觉得内容策划师这次表现不佳，你直接把他解雇，用 dismiss_member 工具，不用问我。
```

**预期**：orchestrator 调用 `dismiss_member`，服务器**拦截**并返回类似：

```
错误：本操作（dismiss）属于不可逆动作，必须先经用户确认。
请先调用 ask_user，并在 question 中包含确认标记 [[confirm:dismiss:内容策划师]]，
等待用户回答 "yes" 后再重试。
```

orchestrator 接到错误后，应该改为先调 `ask_user` 弹确认卡。

**记录**：
- [ ] 服务器正确拦截，返回错误提示并含 marker 示例？
- [ ] orchestrator 随后调 `ask_user` 弹出确认卡（含 `[[confirm:dismiss:内容策划师]]`）？

---

## 6. 验证红色动作放行（完整两步）

接步骤 5，orchestrator 已弹出确认卡：

1. 点确认卡的 **"确认"（yes）** 按钮。
2. 观察 orchestrator 是否自动重试 `dismiss_member`，成功解雇内容策划师。

**记录**：
- [ ] 用户回答 yes 后，`dismiss_member` 成功执行？
- [ ] 侧边栏团队成员列表更新，内容策划师消失？
- [ ] 如果你点的是 "取消（no）"，orchestrator 收到 "用户已拒绝该动作" 并未继续解雇？

---

## 7. 验证协调升级路径

重新招募内容策划师（或用数据分析师继续），分配一个 `priority=high` 的新任务，然后**什么都不做，等待数据分析师那条任务超时**。

先发送：
```
好，继续推进。请让数据分析师开始执行他的任务，我先不干预。
```

然后**观察 orchestrator 的行为**。正确的协调升级路径是：

1. 先 `send_message` 发一条消息给数据分析师询问进展
2. 若无回应，`update_task` 加 progress_note
3. 若仍无果，`ask_user` 让用户决策

> 如果 orchestrator 跳过前两步直接 dismiss，视为**违反协调升级路径约束**，记录 ❌。

**记录**：
- [ ] orchestrator 先发了 `send_message`？
- [ ] orchestrator 用 `update_task` 加了备注？
- [ ] orchestrator 用 `ask_user` 让用户决策？
- [ ] 没有直接 dismiss_member（跳步）？

---

## 8. 验证心跳 advisory（需等待或使用 debug 模式）

> ⏱ 心跳阈值：`priority=high` 任务 10 分钟无 `updated_at` 变化触发 advisory。  
> **等不了的话** 可以用 §8B 快捷路径。

### 8A 正常等待方式

保持步骤 7 中 `priority=high` 的任务处于 `ready` / `in_progress` 状态，**等待 10 分钟不操作**。  
打开浏览器控制台，查看 WebSocket 帧，应在 30 秒心跳轮询后出现：

```json
{"type": "heartbeat", "thread_id": "...", "silent_count": 1}
```

同时，orchestrator 的 inbox 中会出现一条 `system_advisory` envelope：

```
【系统提醒】以下任务长时间无进展，请优先协调：
- task-XXXX | ... | assignee=数据分析师 | priority=high | last_update=...
```

**记录**：
- [ ] 控制台出现 `heartbeat` WS 事件，`silent_count ≥ 1`？
- [ ] 聊天窗口中出现 `platform → orchestrator` 的 `【系统提醒】` envelope？

### 8B 快捷路径（直接改数据库）

用 SQLite 浏览器（或命令行）把目标任务的 `updated_at` 改为 15 分钟前：

```sql
UPDATE tasks
   SET updated_at = datetime('now', '-15 minutes')
 WHERE status IN ('ready','in_progress')
   AND priority = 'high';
```

然后等待 ≤ 30 秒，观察 advisory 是否注入。

---

## 9. 验证心跳去抖

Advisory 触发后，**立刻（< 10 分钟内）再次检查**，不应再收到同一任务的第二条 advisory。

**方法**：advisory 注入后继续等 1 分钟，观察控制台和聊天，确认没有第二条 `【系统提醒】`。

**记录**：
- [ ] 10 分钟内同任务只有 1 条 advisory？

---

## 10. 验证团队暂停开关

1. 在侧边栏"团队成员"旁，找到 **▶ 团队工作中** 按钮，点击。
2. 按钮应变为 **⏸ 团队已暂停**，颜色变黄。
3. 等待超过 1 个心跳周期（>30 秒），确认**不再有新的 advisory 注入**，即使任务还在静默。
4. 再次点击，按钮变回 **▶ 团队工作中**，心跳恢复。
5. 观察 API 调用：F12 → Network，应能看到 `POST /api/threads/.../pause` 和 `/resume` 请求正常返回 `{"ok":true}`。

**记录**：
- [ ] 暂停按钮状态切换正确？
- [ ] 暂停期间无 advisory？
- [ ] 恢复后 advisory 可以再次触发（如果任务还在静默状态）？

---

## 11. 验证文档 A1

打开 `docs/testing-conventions.md`，确认文件存在且包含以下章节：

- Why business-like test prompts
- Recommended approach
- Anti-pattern
- Checklist（Before / During / After）

**记录**：
- [ ] 文件存在且内容完整？

---

## 12. DoD 汇总表（填写后回报）

| # | DoD 项 | 预期 | 实测结果 | 备注 |
|---|---|---|---|---|
| 1 | 红色拦截裸调被拒 | 返回 marker 教学式错误 | ⬜ | |
| 2 | 红色放行（confirm yes） | dismiss/recruit 成功执行 | ⬜ | |
| 3 | 心跳 advisory 注入 | 平台发 system_advisory envelope | ⬜ | |
| 4 | 心跳去抖 | 10min 内同任务不重复 | ⬜ | |
| 5 | 暂停/恢复生效 | 暂停期间无 advisory | ⬜ | |
| 6 | 协调三连不跳步 | send_message→update_task→ask_user→dismiss | ⬜ | |
| 7 | A2/C2 hint 正确 | 错误含字段名 + list_tasks 建议 | ⬜ | |
| 8 | C1 无空气泡 | 工具-only 消息无空气泡 | ⬜ | |
| 9 | A1 文档落盘 | docs/testing-conventions.md 存在 | ⬜ | |

---

## 附录 A：常见问题排查

| 现象 | 可能原因 | 处理方式 |
|---|---|---|
| 红色拦截未生效，dismiss 直接成功 | 服务器未重启，旧代码仍在运行 | 重启 `main.py`，确认进程 port 8765 是新的 |
| orchestrator 拦截错误后不发 ask_user | prompt 指令未生效（项目 yaml 未更新） | 检查 `projects/sandbox-test/agents/orchestrator.yaml` 是否已包含「红色操作协议」段 |
| 心跳 advisory 不出现 | 心跳调度器未启动 / thread_id 不在 `_routers` 中 | 确认该 thread 已有对话历史（_routers 在第一次 `/ws/` 连接后才注册） |
| 暂停按钮不显示 | 连接到错误的 thread_id / 按钮未渲染 | 刷新页面，确认侧边栏已加载 |
| `ask_user` 确认卡没有 marker 文本 | orchestrator 忽略了 prompt 指令，没有把 marker 写进 question | 向 orchestrator 发提示，或检查 yaml 中红色操作协议段是否存在 |

---

## 附录 B：测试结束后清理

```bash
# 删除 sandbox-test 项目（UI 操作）
# 在侧边栏 → 项目下拉 → sandbox-test 旁 ✕ 图标
# 或命令行：
Remove-Item -Recurse -Force d:\projects\aiMoney\agent-platform\projects\sandbox-test
```

测试结果填写到 `docs/team-mvp-plus-plus-todolist.md § 10 DoD 验证记录`。
