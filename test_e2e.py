# -*- coding: utf-8 -*-
"""SCU3 端到端实测 — 3+3+3验证"""
import json
import urllib.request
import urllib.parse
import urllib.error

BASE = "http://127.0.0.1:8300"


def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    # URL编码（处理中文参数）
    encoded = urllib.parse.quote(path, safe="/?=&")
    with urllib.request.urlopen(BASE + encoded, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def stream(path, body):
    """SSE流式读取"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"}
    )
    events = []
    with urllib.request.urlopen(req, timeout=60) as r:
        buf = ""
        while True:
            chunk = r.read(1024).decode("utf-8")
            if not chunk:
                break
            buf += chunk
            while "\n\n" in buf:
                event_str, buf = buf.split("\n\n", 1)
                for line in event_str.split("\n"):
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:].strip()))
    return events


print("=" * 70)
print("SCU3 端到端实测 · 3+3+3验证")
print("=" * 70)

# ════════════════════════════════════════════════════════
# 对话类（3项）
# ════════════════════════════════════════════════════════
print("\n── 对话类（3项）──")

# 1. 计算器工具
r = post("/chat", {"prompt": "计算 3+5*2", "user_id": "test_user"})
print(f"\n[1] 计算器工具")
print(f"    输入: 计算 3+5*2")
print(f"    回复: {r.get('response', '')}")
print(f"    pattern: {r.get('pattern_key', '')} | 余额: {r.get('balance', '')}E | 耗时: {r.get('elapsed_ms', '')}ms")
traces = r.get("cuf_traces", [])
for t in traces:
    print(f"    守卫[{t['guard']}]: {'✓' if t['passed'] else '✗'} 税={t.get('tax', 0)}E")

# 2. 时间工具
r = post("/chat", {"prompt": "当前时间", "user_id": "test_user"})
print(f"\n[2] 时间工具")
print(f"    输入: 当前时间")
print(f"    回复: {r.get('response', '')}")
print(f"    pattern: {r.get('pattern_key', '')} | 耗时: {r.get('elapsed_ms', '')}ms")

# 3. 普通对话（LLM）
r = post("/chat", {"prompt": "你好，请用一句话介绍你自己", "user_id": "test_user"})
resp = r.get("response", "")
print(f"\n[3] 普通对话（LLM）")
print(f"    输入: 你好，请用一句话介绍你自己")
print(f"    回复: {resp[:120]}{'...' if len(resp) > 120 else ''}")
print(f"    pattern: {r.get('pattern_key', '')} | 耗时: {r.get('elapsed_ms', '')}ms")

# ════════════════════════════════════════════════════════
# 知识库类（3项）
# ════════════════════════════════════════════════════════
print("\n\n── 知识库类（3项）──")

# 4. 添加知识
r = post("/knowledge/add", {"content": "SCU3采用v3架构，三维度分离：数据流、权限层、守卫横切。D层只读，账本归W1，无死循环。", "source": "架构文档"})
print(f"\n[4] 添加知识文档")
print(f"    success: {r.get('success', False)} | doc_id: {r.get('doc_id', '')}")

# 5. 检索知识
r = get("/knowledge/search?q=SCU3架构&top_k=3")
results = r.get("results", [])
print(f"\n[5] 检索知识（关键词：SCU3架构）")
print(f"    结果数: {len(results)}")
for i, doc in enumerate(results):
    print(f"    #{doc['id']} score={doc['score']} | {doc['content'][:50]}...")

# 6. 知识列表
r = get("/knowledge/list?limit=5")
docs = r.get("documents", [])
print(f"\n[6] 知识文档列表")
print(f"    总数: {len(docs)}")
for d in docs:
    print(f"    #{d['id']} | {d['content'][:40]}...")

# ════════════════════════════════════════════════════════
# 系统类（3项）
# ════════════════════════════════════════════════════════
print("\n\n── 系统类（3项）──")

# 7. 状态
r = get("/status")
print(f"\n[7] 系统状态")
print(f"    架构: {r.get('arch', '')} v{r.get('version', '')}")
print(f"    余额: {r.get('balance', '')}E | 历史: {r.get('stats', {}).get('history_count', 0)}")
print(f"    白名单: {r.get('whitelist_count', 0)} | 反馈Pattern: {r.get('stats', {}).get('feedback_patterns', 0)}")

# 8. 前端页面
try:
    with urllib.request.urlopen(BASE + "/", timeout=5) as resp:
        html = resp.read().decode("utf-8")
    has_title = "标准计算单元" in html or "SCU" in html
    has_chat = "chatInput" in html or "sendChat" in html
    size_kb = len(html) // 1024
    print(f"\n[8] 前端页面")
    print(f"    大小: {size_kb}KB | 标题: {'✓' if has_title else '✗'} | 聊天UI: {'✓' if has_chat else '✗'}")
except Exception as e:
    print(f"\n[8] 前端页面: ✗ {e}")

# 9. SSE流式
print(f"\n[9] SSE流式聊天")
print(f"    输入: 用一句话解释什么是量子计算")
try:
    events = stream("/chat/stream", {"prompt": "用一句话解释什么是量子计算", "user_id": "test_user"})
    meta = next((e for e in events if e.get("type") == "meta"), {})
    chunks = [e for e in events if e.get("type") == "chunk"]
    done = next((e for e in events if e.get("type") == "done"), {})
    full_text = "".join(c.get("content", "") for c in chunks)
    print(f"    模式: {meta.get('mode', '')} | 事件数: {len(events)} (meta+{len(chunks)}chunks+done)")
    print(f"    流式回复: {full_text[:150]}{'...' if len(full_text) > 150 else ''}")
    print(f"    完成: {'✓' if done else '✗'}")
except Exception as e:
    print(f"    ✗ 错误: {e}")

print("\n" + "=" * 70)
print("3+3+3 实测完成")
print("=" * 70)
