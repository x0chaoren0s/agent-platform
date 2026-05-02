# 对话自动命名 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically name conversations based on content via LLM, with manual re-trigger support.

**Architecture:** Add `auto_rename` flag to conversations table; reuse existing ARK LLM client in `summarizer.py` to generate names; trigger once after N messages on WebSocket `agent_done`; new API for manual re-trigger; frontend button and event handler.

**Tech Stack:** Python/FastAPI, SQLite (aiosqlite), ARK API (Doubao), vanilla JS

---

### Task 1: ConversationStore — auto_rename 字段

**Files:**
- Modify: `core/conversation_store.py:48-71`

- [ ] **Step 1: 在 init_db 中添加 auto_rename 列**

在 `conversation_store.py` 的 `init_db()` 方法中，在已有的 `ALTER TABLE` 迁移之后，追加向后兼容的列添加语句：

```python
try:
    await db.execute(
        f"ALTER TABLE {_TABLE} ADD COLUMN auto_rename INTEGER NOT NULL DEFAULT 1"
    )
except aiosqlite.OperationalError:
    pass
```

注意：放在现有的 `is_paused` 列迁移之后（第 65-70 行之间）。

- [ ] **Step 2: 添加 set_auto_rename / get_auto_rename 方法**

在 `ConversationStore` 类中，`rename()` 方法之后添加：

```python
async def set_auto_rename(self, thread_id: str, enabled: bool) -> None:
    await self._ensure_ready()
    async with aiosqlite.connect(self._db_path) as db:
        await db.execute(
            f"UPDATE {_TABLE} SET auto_rename = ? WHERE thread_id = ?",
            (1 if enabled else 0, thread_id),
        )
        await db.commit()

async def get_auto_rename(self, thread_id: str) -> bool:
    await self._ensure_ready()
    async with aiosqlite.connect(self._db_path) as db:
        async with db.execute(
            f"SELECT auto_rename FROM {_TABLE} WHERE thread_id = ?",
            (thread_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return True  # default for non-existent rows
    return bool(row[0])
```

- [ ] **Step 3: 验证**

快速检查文件语法：
```bash
python -c "import ast; ast.parse(open('core/conversation_store.py').read()); print('ok')"
```

- [ ] **Step 4: 提交**

```bash
git add core/conversation_store.py
git commit -m "feat(conversation): add auto_rename column and accessors"
```

---

### Task 2: Summarizer — 新增 auto_name_conversation 函数

**Files:**
- Modify: `core/summarizer.py`

- [ ] **Step 1: 添加 auto_naming prompt 和函数**

在 `summarizer.py` 末尾，`consolidate_to_context` 之后添加：

```python
_AUTO_NAME_SYSTEM = """\
你是一个对话命名助手。根据以下对话内容，用 6-15 个字概括对话主题。
直接输出名称，不要解释、不要加标点、不要加引号。
"""

async def auto_name_conversation(envelopes: list[dict]) -> str | None:
    """Generate a concise Chinese name for a conversation based on its content.

    Returns the generated name string, or None on failure.
    """
    if not envelopes:
        return None
    # Use first ~10 meaningful exchanges (user→agent rounds)
    text = _envelopes_to_text(envelopes[:20])
    if not text.strip():
        return None
    try:
        client = _build_llm_client()
        resp = await client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _AUTO_NAME_SYSTEM},
                {"role": "user", "content": f"对话内容：\n\n{text}"},
            ],
            temperature=0.3,
            max_tokens=50,
        )
        name = (resp.choices[0].message.content or "").strip().strip('"').strip("'").strip()
        if not name or len(name) > 30:
            return None
        return name
    except Exception as exc:
        err_text = str(exc).lower()
        if "insufficient balance" in err_text or "error code: 402" in err_text:
            logger.warning("auto_name_conversation skipped: model balance insufficient")
            return None
        logger.exception("auto_name_conversation failed")
        return None
```

- [ ] **Step 2: 暴露到模块 __all__（如果存在）**

检查 `summarizer.py` 是否有 `__all__`，若有则添加 `"auto_name_conversation"`。

- [ ] **Step 3: 验证**

```bash
python -c "import ast; ast.parse(open('core/summarizer.py').read()); print('ok')"
```

- [ ] **Step 4: 提交**

```bash
git add core/summarizer.py
git commit -m "feat(summarizer): add auto_name_conversation function"
```

---

### Task 3: main.py — 自动命名触发 + API + 广播

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 导入 auto_name_conversation**

在 `main.py` 现有的 `from core import summarizer as summarizer_mod`（第 58 行）之后保持，我们通过 `summarizer_mod.auto_name_conversation` 调用。

- [ ] **Step 2: 新增全局状态**

在 `_question_stores` 之后（第 88 行附近）添加：

```python
_auto_named: set[str] = set()  # thread_ids that have received first auto-name
```

- [ ] **Step 3: 添加 _should_auto_name 和 _auto_name_conversation 辅助函数**

在 `_on_conversation_disconnect` 函数之前（第 1690 行附近）添加：

```python
def _should_auto_name(thread_id: str, router: MessageRouter) -> bool:
    """Check if a conversation should receive its first auto-name."""
    if thread_id in _auto_named:
        return False
    log = router.get_global_log()
    # Count non-system messages
    msg_count = sum(
        1 for env in log
        if env.get("sender", "") not in ("", "system", "platform")
    )
    if msg_count < 5:
        return False
    return True


async def _auto_name_conversation(thread_id: str) -> None:
    """Generate and apply an auto-name for a conversation, then broadcast."""
    router = _routers.get(thread_id)
    if router is None:
        return
    # Double-check guard
    if thread_id in _auto_named:
        return
    _auto_named.add(thread_id)

    # Check auto_rename flag
    if _conv_store is not None:
        allowed = await _conv_store.get_auto_rename(thread_id)
        if not allowed:
            return

    envelopes = router.get_recent_envelopes(20)
    name = await summarizer_mod.auto_name_conversation(envelopes)
    if not name:
        return

    if _conv_store is not None:
        await _conv_store.rename(thread_id, name)

    await _ws_broadcast(thread_id, {
        "type": "conversation_renamed",
        "thread_id": thread_id,
        "name": name,
    })
    logger.info("Auto-named conversation %s → %s", thread_id, name)
```

- [ ] **Step 4: 在 WebSocket agent_done 事件中触发**

在 `websocket_chat()` 中，找到 `agent_done` 事件处理分支（第 1651-1653 行），在其后追加：

```python
elif event.get("type") == "agent_done":
    agent_name = event.get("agent", "")
    full_reply_by_agent.pop(agent_name, None)
    # 新增：触发首次自动命名
    if _conv_store and _should_auto_name(thread_id, router):
        asyncio.create_task(_auto_name_conversation(thread_id))
```

- [ ] **Step 5: 添加手动 AI 重命名 API**

在 `rename_conversation`（第 986 行）之后添加：

```python
@app.post("/api/conversations/{thread_id}/auto-name")
async def auto_rename_conversation(thread_id: str):
    """Manually trigger AI auto-naming for a conversation."""
    if _conv_store is None:
        raise HTTPException(status_code=503, detail="ConversationStore not initialized")
    router = _routers.get(thread_id)
    if router is None:
        raise HTTPException(status_code=404, detail="对话不存在或未激活")
    envelopes = router.get_recent_envelopes(20)
    name = await summarizer_mod.auto_name_conversation(envelopes)
    if not name:
        raise HTTPException(status_code=502, detail="AI 命名生成失败，请稍后重试")
    await _conv_store.rename(thread_id, name)
    # Manual AI rename does NOT change auto_rename flag
    await _ws_broadcast(thread_id, {
        "type": "conversation_renamed",
        "thread_id": thread_id,
        "name": name,
    })
    return {"ok": True, "name": name}
```

- [ ] **Step 6: 手动改名时禁用自动命名**

在 `rename_conversation`（第 986-997 行）中，成功 rename 后添加：

```python
# 用户手动改名 → 禁用自动命名
await _conv_store.set_auto_rename(thread_id, False)
```

找到现有代码：
```python
ok = await _conv_store.rename(thread_id, new_name)
if not ok:
    raise HTTPException(status_code=404, detail="对话不存在")
return {"ok": True, "thread_id": thread_id, "name": new_name}
```

改为：
```python
ok = await _conv_store.rename(thread_id, new_name)
if not ok:
    raise HTTPException(status_code=404, detail="对话不存在")
await _conv_store.set_auto_rename(thread_id, False)
return {"ok": True, "thread_id": thread_id, "name": new_name}
```

- [ ] **Step 7: 验证**

```bash
python -c "import ast; ast.parse(open('main.py').read()); print('ok')"
```

- [ ] **Step 8: 提交**

```bash
git add main.py
git commit -m "feat(main): add auto-naming trigger, API endpoint, and broadcast"
```

---

### Task 4: 前端 UI — AI 重命名按钮 + 事件处理

**Files:**
- Modify: `web/index.html`

- [ ] **Step 1: 在对话列表中添加 AI 重命名按钮**

在 `renderConversations` 函数中，找到现有的重命名按钮（第 816 行附近）：

```html
<button class="conv-act-btn" title="重命名" onclick="renameConversation('${c.thread_id}','${escHtml(c.name)}',event)">✎</button>
```

在其后添加 AI 重命名按钮：

```html
<button class="conv-act-btn" title="AI 重命名" onclick="autoRenameConversation('${c.thread_id}',event)">🤖</button>
```

- [ ] **Step 2: 在 deleteConversation 之前添加 autoRenameConversation 函数**

在 `renameConversation` 函数后面（第 885 行附近）添加：

```javascript
async function autoRenameConversation(thread_id, evt) {
  evt.stopPropagation();
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(thread_id)}/auto-name`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      const conv = allConversations.find(c => c.thread_id === thread_id);
      if (conv) conv.name = data.name;
      renderConversations(allConversations);
      showToast('已重命名为：' + data.name);
    } else {
      showToast(data.detail || 'AI 命名失败');
    }
  } catch (e) {
    showToast('请求失败');
  }
}
```

- [ ] **Step 3: 处理 conversation_renamed WebSocket 事件**

在 WebSocket `onmessage` 处理中（第 2188 行 `thread_paused` 事件附近），添加：

```javascript
if (ev.type === 'conversation_renamed') {
  const conv = allConversations.find(c => c.thread_id === ev.thread_id);
  if (conv) {
    conv.name = ev.name;
    renderConversations(allConversations);
  }
  return;
}
```

- [ ] **Step 4: 验证—重启服务器并测试**

```bash
# 确保在项目根目录
python main.py &
sleep 2
curl -s http://localhost:8765/api/conversations | python -c "import sys,json; d=json.load(sys.stdin); print('conversations:', len(d.get('conversations',[])))"
```

手动测试步骤：
1. 打开 http://localhost:8765
2. 选择一个 agent，发送消息
3. 发送 5+ 条消息后，观察侧边栏对话名称是否自动更新
4. 点击 🤖 按钮测试手动 AI 重命名
5. 手动改名后确认 🤖 不再自动触发

- [ ] **Step 5: 提交**

```bash
git add web/index.html
git commit -m "feat(ui): add AI rename button and conversation_renamed event handling"
```
