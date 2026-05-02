# 对话自动命名功能设计

## 概述

对话创建时默认名称为"对话 main-xxxxx"，对用户不友好。本功能通过 LLM 自动摘要对话内容，生成有意义的主题名称，类似 Cursor 的自动命名体验。

## 数据模型变更

### conversations 表新增字段

```sql
ALTER TABLE conversations ADD COLUMN auto_rename INTEGER NOT NULL DEFAULT 1;
```

- `auto_rename = 1`（默认）：允许 AI 自动命名
- 用户手动 rename 改名时设为 `0`，表示用户接管命名权
- AI 重命名不受此字段影响（走不同 API 路径）

### ConversationStore 新增方法

- `async def set_auto_rename(thread_id: str, enabled: bool) -> None`
- `async def get_auto_rename(thread_id: str) -> bool`

### 运行时内存状态

`main.py` 中新增 `_auto_named: set[str]`，记录已执行首次自动命名的 thread_id，防止重复自动触发。

## LLM 命名调用

### 位置

复用 `core/summarizer.py` 中的 LLM 客户端基础设施（ARK API / Doubao 模型）。

### 新增函数

```python
async def auto_name_conversation(envelopes: list[dict]) -> str | None:
```

- 取前 10 条完整轮次消息，用 `_envelopes_to_text()` 转为文本
- LLM prompt 要求生成 6-15 字中文主题名，直接输出名称不解释
- 温度 0.3，max_tokens 50
- 异常时静默失败，返回 None

## 首次自动命名触发

在 WebSocket 消息循环中，当收到 `agent_done` 事件时判断：

```python
if _conv_store and _should_auto_name(thread_id):
    asyncio.create_task(_auto_name_conversation(thread_id))
```

`_should_auto_name()` 条件：
1. thread_id 不在 `_auto_named` 集合中（未执行过首次自动命名）
2. `auto_rename` 为 true（未被手动改名禁用）
3. 对话日志消息数 >= 5 条（不含系统消息）

满足全部条件则异步调用 LLM → 更新数据库 → WebSocket 广播 `conversation_renamed` 事件。

## API 接口

### 手动 AI 重命名

```
POST /api/conversations/{thread_id}/auto-name
```

- 不检查 `_auto_named` 集合
- 读取对话最新 envelopes，调用 LLM 生成名称
- 更新数据库（不改变 `auto_rename` 字段）
- 广播 `conversation_renamed` 事件
- 返回 `{"ok": True, "name": "新名称"}`

### 手动改名时禁用自动命名

修改现有 `PATCH /api/conversations/{thread_id}/name`，在成功 rename 后调用 `set_auto_rename(thread_id, False)`。

## WebSocket 事件

新增事件类型供前端监听：

```json
{
  "type": "conversation_renamed",
  "thread_id": "main-abc12",
  "name": "星火万物岗位调研"
}
```

## 前端 UI 变更

### 侧边栏对话列表

在现有的重命名按钮（✎）旁新增 AI 重命名按钮（🤖）：

```html
<button class="conv-act-btn" title="AI 重命名" onclick="autoRenameConversation('${c.thread_id}',event)">🤖</button>
```

### 新增函数

```javascript
async function autoRenameConversation(thread_id, evt) {
  evt.stopPropagation();
  const res = await fetch(`/api/conversations/${encodeURIComponent(thread_id)}/auto-name`, { method: 'POST' });
  const data = await res.json();
  if (data.ok) {
    const conv = allConversations.find(c => c.thread_id === thread_id);
    if (conv) conv.name = data.name;
    renderConversations(allConversations);
    showToast('已重命名为：' + data.name);
  }
}
```

### 事件监听

在 WebSocket `onmessage` 中处理 `conversation_renamed` 事件，更新对应对话的名称显示。

## 错误处理

- LLM 调用失败（余额不足、超时等）：静默忽略，不影响对话流程，日志记录
- 并发触发：`_auto_named` 集合在触发前加入，防止重复
- 空内容：对话为空或只有系统消息时跳过

## 涉及文件

| 文件 | 改动 |
|---|---|
| `core/conversation_store.py` | 新增 `auto_rename` 字段操作方法 |
| `core/summarizer.py` | 新增 `auto_name_conversation()` 函数 |
| `main.py` | 新增触发逻辑、API 端点、广播事件 |
| `web/index.html` | 新增 AI 重命名按钮、事件处理 |
