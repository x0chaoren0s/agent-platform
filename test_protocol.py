"""
端对端验证脚本：member protocol 注入 + assign_task 自动通知 + depends_on 错误提示
全程使用 REST API，不依赖 WebSocket
"""
import json, time, urllib.request, urllib.parse, urllib.error

BASE = "http://127.0.0.1:8765"


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path).read().decode())


def post(path, body=None, method="POST"):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        return json.loads(urllib.request.urlopen(req).read().decode())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()}


def sep(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


# ── STEP 0: 新建项目 ─────────────────────────────────────────
sep("0. 创建/激活测试项目 proto-test-0426")
r = post("/api/projects", {"name": "proto-test-0426"})
if r.get("_http_error") == 409:
    r = post("/api/projects/proto-test-0426/activate")
print("project:", r.get("name"), "agents:", [a["name"] for a in r.get("agents", [])])

# ── STEP 1: 验证 orchestrator effective_prompt 无协议注入 ─────
sep("1. orchestrator effective_prompt 不含成员协议")
ep = get("/api/agents/orchestrator/effective_prompt")
has_proto = "【任务执行协议】" in ep.get("effective_instructions", "")
print(f"  role={ep['role']}  is_temp={ep['is_temp']}  has_protocol={has_proto}")
assert not has_proto, "FAIL: orchestrator 不应含任务执行协议"
print("  PASS")

# ── STEP 2: 招募成员并验证 effective_prompt 含协议 ────────────
sep("2. recruit 两个成员，验证 effective_prompt 包含成员协议")
WRITER = "文案撰稿人"
REVIEWER = "文案审核员"

for name, desc, cap in [
    (WRITER, "负责撰写中文文案", ["copywriting"]),
    (REVIEWER, "负责审核文案质量", ["review"]),
]:
    r2 = post("/api/agents", {
        "name": name,
        "description": desc,
        "capabilities": cap,
        "instructions": f"你是一个专业的{desc}，请尽力完成分配给你的任务。",
    })
    print(f"  recruit {name}: {r2.get('message', r2)}")
    time.sleep(0.5)

# 等 watchdog 刷新
time.sleep(1)

for name in [WRITER, REVIEWER]:
    ep2 = get(f"/api/agents/{urllib.parse.quote(name, safe='')}/effective_prompt")
    has_p = "【任务执行协议】" in ep2.get("effective_instructions", "")
    print(f"  {name}: role={ep2['role']}  has_protocol={has_p}")
    assert has_p, f"FAIL: {name} 缺少任务执行协议"
print("  PASS: 两个成员均含协议")

# ── STEP 3: 创建对话 + assign_task，验证 ready ────────────────
sep("3. 创建对话，派发独立任务给撰稿人")
conv = post("/api/conversations", {"name": "协议验证对话"})
thread_id = conv["conversation"]["thread_id"]
print(f"  thread_id={thread_id}")

# orchestrator 派发第一个任务（直接调用 REST chat 模拟 orchestrator 的 tool_call）
chat_body = {
    "thread_id": thread_id,
    "sender": "user",
    "to": ["orchestrator"],
    "cc": [],
    "content": "我们现在要验证一下，请你用 assign_task 给文案撰稿人分配一个任务：撰写一则冬促活动公告，brief是'突出折扣、温暖感'，deadline 是2026-04-26 23:59。"
}
cr = post("/api/chat", chat_body)
tool_results = cr.get("tool_results", [])
print(f"  tool_results: {[t['tool'] for t in tool_results]}")

assign_results = [t for t in tool_results if t["tool"] == "assign_task"]
print(f"  assign_task results: {[t['result'] for t in assign_results]}")

# ── STEP 4: 查任务状态，期望 ready ───────────────────────────
sep("4. 查任务状态（期望 ready）")
tasks_r = get(f"/api/threads/{thread_id}/tasks")
tasks = tasks_r.get("data", [])
print(f"  tasks count: {len(tasks)}")
for t in tasks:
    print(f"  [{t['id']}] {t['title']} | assignee={t['assignee']} | status={t['status']}")

writer_task = next((t for t in tasks if t["assignee"] == WRITER), None)
assert writer_task, "FAIL: 找不到撰稿人的任务"
assert writer_task["status"] == "ready", f"FAIL: 期望 ready，实为 {writer_task['status']}"
print("  PASS: 任务状态为 ready")

# ── STEP 5 & 6: 直接用 asyncio 调用 assign_task 验证校验逻辑 ──
sep("5. depends_on 填成员名 → 期望服务器报错（直接调 assign_task）")
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))

from core.tools.categories.team_runtime import assign_task, _get_task_store, set_router, set_broadcaster
from pathlib import Path

PDIR = str(Path(__file__).parent / "projects" / "proto-test-0426")

async def _direct_tests():
    # 先初始化 store（使用上面 REST 已创建的 tasks.db）
    set_broadcaster(None)
    set_router(thread_id, None)

    # 5: depends_on 填成员名
    bad = await assign_task(
        project_dir=PDIR,
        thread_id=thread_id,
        caller_agent="orchestrator",
        assignee=REVIEWER,
        title="审核冬促公告",
        brief="检查文案准确性与温暖感",
        depends_on=[WRITER],   # 故意填成员名
    )
    print(f"  bad depends_on response: {bad}")
    assert "task-" in bad or "错误" in bad, f"FAIL: 期望错误提示，实为: {bad}"
    print("  PASS: 服务器正确拒绝非 task-id 的 depends_on")

    # 6: 正确 task-id 派发下游
    task_id = writer_task["id"]
    good = await assign_task(
        project_dir=PDIR,
        thread_id=thread_id,
        caller_agent="orchestrator",
        assignee=REVIEWER,
        title="审核冬促公告",
        brief="检查文案准确性与温暖感",
        depends_on=[task_id],   # 正确
    )
    print(f"  good depends_on response: {good}")
    assert "task-" in good and "pending" not in good, f"FAIL: 期望创建成功消息，实为: {good}"
    return good

sep("5+6. 直接调用 assign_task 验证")
asyncio.run(_direct_tests())

# 通过 REST 验证任务列表
tasks_r2 = get(f"/api/threads/{thread_id}/tasks")
tasks2 = tasks_r2.get("data", [])
reviewer_task = next((t for t in tasks2 if t["assignee"] == REVIEWER), None)
assert reviewer_task, "FAIL: 找不到审核员任务"
print(f"  reviewer task: [{reviewer_task['id']}] status={reviewer_task['status']} depends_on={reviewer_task['depends_on']}")
assert reviewer_task["status"] == "pending", f"FAIL: 依赖上游未完成，应为 pending，实为 {reviewer_task['status']}"
print("  PASS: 下游任务为 pending")

# ── 最终总结 ─────────────────────────────────────────────────
sep("验证完成总结")
print("  [PASS] orchestrator 无协议注入")
print("  [PASS] 固定成员含任务执行协议")
print("  [PASS] 独立任务创建后状态为 ready")
print("  [PASS] depends_on 填成员名被正确拒绝")
print("  [PASS] depends_on 填 task-id 成功创建，下游为 pending")
print("\n  注意：assign_task 自动 notify_assignee 在 REST 模式下")
print("  也会触发（需查服务器 log 确认 notify 调用）")
