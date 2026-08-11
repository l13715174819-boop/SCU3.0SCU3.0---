# -*- coding: utf-8 -*-
"""
SCU2 全功能测试 · 3轮 (v2 - 修正断言)
覆盖所有14个API端点 + 13种工具 + RAG + SSE + 前端
"""
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from collections import defaultdict

BASE = "http://127.0.0.1:8300"


def post(path, body, timeout=30):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, {"_raw": raw}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


def get(path, timeout=10, raw=False):
    encoded = urllib.parse.quote(path, safe="/?=&")
    try:
        with urllib.request.urlopen(BASE + encoded, timeout=timeout) as r:
            content = r.read().decode("utf-8")
            if raw:
                return r.status, content
            try:
                return r.status, json.loads(content)
            except json.JSONDecodeError:
                return r.status, {"_raw": content}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


def delete(path, timeout=10):
    encoded = urllib.parse.quote(path, safe="/?=&")
    req = urllib.request.Request(BASE + encoded, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return 0, {"error": str(e)}


def stream(path, body, timeout=60):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "text/event-stream"})
    events = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
                            try:
                                events.append(json.loads(line[5:].strip()))
                            except json.JSONDecodeError:
                                pass
    except Exception as e:
        events.append({"type": "error", "error": str(e)})
    return events


def run_round(round_num):
    print(f"\n{'='*70}")
    print(f"全功能测试 · 第{round_num}轮")
    print(f"{'='*70}")

    results = defaultdict(list)
    test_count = 0
    pass_count = 0

    def check(name, condition, detail=""):
        nonlocal test_count, pass_count
        test_count += 1
        if condition:
            pass_count += 1
        status = "PASS" if condition else "FAIL"
        results[name.split(".")[0]].append((name, status, detail))
        print(f"  [{status}] {name}: {detail}")

    # ═══ 1. 核心对话 ═══
    print(f"\n── 1. 核心对话 ──")

    s, r = post("/chat", {"prompt": "计算 3+5*2", "user_id": f"r{round_num}"})
    check("chat.计算器", s == 200 and r.get("success") and "13" in r.get("response", ""),
          f"resp={r.get('response', '')[:30]}")

    s, r = post("/chat", {"prompt": "当前时间", "user_id": f"r{round_num}"})
    check("chat.时间", s == 200 and r.get("success") and "2026" in r.get("response", ""),
          f"resp={r.get('response', '')[:30]}")

    s, r = post("/chat", {"prompt": "你好", "user_id": f"r{round_num}"})
    check("chat.普通对话", s == 200 and r.get("success") and len(r.get("response", "")) > 0,
          f"len={len(r.get('response', ''))}")

    events = stream("/chat/stream", {"prompt": "说一个字", "user_id": f"r{round_num}"})
    has_meta = any(e.get("type") == "meta" for e in events)
    has_chunk = any(e.get("type") == "chunk" for e in events)
    has_done = any(e.get("type") == "done" for e in events)
    check("chat.stream.SSE", has_meta and has_chunk and has_done,
          f"events={len(events)} chunks={sum(1 for e in events if e.get('type')=='chunk')}")

    # 前端（raw模式，不解析JSON）
    s, html = get("/", raw=True)
    check("frontend.首页", s == 200 and "<html" in html.lower(),
          f"size={len(html)} status={s}")

    # ═══ 2. 13种工具 ═══
    print(f"\n── 2. 13种工具 ──")
    # (prompt, 期望关键词)
    tool_tests = [
        ("计算 3+5*2", "calculator", "13"),
        ("北京天气", "weather", "天气"),
        ("当前时间", "time_now", "2026"),
        ("统计这段文字的字数 count words", "text_stats", "字符"),
        ("汇率 USD", "exchange_rate", "汇率"),
        ("价格 btc", "crypto_price", "BTC"),
        ("股票 AAPL", "stock_price", "Apple"),
        ("github python", "github_search", "GitHub"),
        ("日期计算 2026-01-01 + 30 天", "datetime_calc", "2026-01-31"),
        ("换算 100 c to f", "unit_convert", "212"),
        ("写入 readtest.txt: hello123", "file_write", "写入"),
        ("run print(1+1)", "code_run", "2"),
    ]
    for prompt, tool_name, keyword in tool_tests:
        s, r = post("/chat", {"prompt": prompt, "user_id": f"r{round_num}_t"})
        ok = s == 200 and r.get("success") and keyword in r.get("response", "")
        check(f"tool.{tool_name}", ok, f"resp={r.get('response', '')[:40]}")

    # file_read 单独测试
    s, r = post("/chat", {"prompt": "读 readtest.txt", "user_id": f"r{round_num}_t"})
    check("tool.file_read", s == 200 and r.get("success"),
          f"resp={r.get('response', '')[:40]}")

    # ═══ 3. 知识库RAG ═══
    print(f"\n── 3. 知识库RAG ──")

    s, r = post("/knowledge/add", {"content": f"第{round_num}轮测试文档-SCU2架构验证", "source": "test"})
    doc_id = r.get("doc_id", -1)
    check("knowledge.add", s == 200 and r.get("success") and doc_id > 0,
          f"doc_id={doc_id}")

    s, r = get("/knowledge/search?q=SCU2架构&top_k=3")
    results_count = len(r.get("results", []))
    check("knowledge.search", s == 200 and results_count > 0,
          f"results={results_count}")

    s, r = get("/knowledge/list?limit=10")
    doc_count = len(r.get("documents", []))
    check("knowledge.list", s == 200 and doc_count > 0,
          f"docs={doc_count}")

    s, r = get("/knowledge/status")
    # 兼容不同字段名
    doc_total = r.get("document_count", r.get("total_documents", r.get("count", 0)))
    check("knowledge.status", s == 200 and doc_total > 0,
          f"docs={doc_total} keys={list(r.keys())[:5]}")

    s, r = delete(f"/knowledge/{doc_id}")
    check("knowledge.delete", s == 200 and r.get("success"),
          f"deleted doc_id={doc_id}")

    # ═══ 4. 系统管理 ═══
    print(f"\n── 4. 系统管理 ──")

    s, r = get("/status")
    check("status.系统状态", s == 200 and r.get("arch") == "v3",
          f"arch={r.get('arch')} balance={r.get('balance')}")

    s, r = get("/history?limit=5")
    check("status.历史", s == 200 and len(r.get("history", [])) > 0,
          f"history={len(r.get('history', []))}")

    s, r = post("/feedback", {"kind": "up", "pattern_key": "chat:tool", "user_id": f"r{round_num}_fb"})
    check("feedback.反馈", s == 200 and r.get("success"),
          f"success={r.get('success')}")

    s, r = get("/whitelist/list")
    check("whitelist.列表", s == 200 and "entries" in r,
          f"entries={len(r.get('entries', []))}")

    s, r = post("/whitelist/add", {
        "action": "read", "source": "W2", "target": "W1",
        "contracts": {"observation": {}, "sampling": {}, "signing": {}, "synthesis": {}},
        "code_hash": f"hash_r{round_num}", "ttl_hours": 1
    })
    check("whitelist.添加", s == 200 and r.get("success"),
          f"success={r.get('success')}")

    s, r = post("/audit/daily?force=true", {})
    check("audit.审计", s == 200,
          f"status={s}")

    # ═══ 5. CUF守卫链 ═══
    print(f"\n── 5. CUF守卫链 ──")
    s, r = post("/chat", {"prompt": "计算 1+1", "user_id": f"r{round_num}_g"})
    traces = r.get("cuf_traces", [])
    check("guard.W2→W1", any(t["guard"] == "W2→W1" and t["passed"] for t in traces),
          f"passed={any(t['guard']=='W2→W1' and t['passed'] for t in traces)}")
    check("guard.工具守卫", any(t["guard"] == "tool" and t["passed"] for t in traces),
          f"passed={any(t['guard']=='tool' and t['passed'] for t in traces)}")
    check("guard.W1→M", any(t["guard"] == "W1→M" and t["passed"] for t in traces),
          f"passed={any(t['guard']=='W1→M' and t['passed'] for t in traces)}")
    check("guard.余额扣减", r.get("balance", 1000) < 1000,
          f"balance={r.get('balance')}")

    # 汇总
    print(f"\n── 第{round_num}轮汇总 ──")
    print(f"  总测试: {test_count} | 通过: {pass_count} | 失败: {test_count-pass_count} | 通过率: {pass_count/test_count*100:.1f}%")
    for cat, items in results.items():
        cat_pass = sum(1 for _, s, _ in items if s == "PASS")
        print(f"    {cat}: {cat_pass}/{len(items)}")

    return pass_count, test_count


print("=" * 70)
print("SCU2 全功能测试 · 3轮 (v2)")
print("=" * 70)

all_results = []
for i in range(1, 4):
    p, t = run_round(i)
    all_results.append((p, t))
    if i < 3:
        time.sleep(1)

print(f"\n{'='*70}")
print("3轮总汇")
print(f"{'='*70}")
total_pass = sum(p for p, _ in all_results)
total_test = sum(t for _, t in all_results)
print(f"  总测试: {total_test} | 总通过: {total_pass} | 通过率: {total_pass/total_test*100:.1f}%")
for i, (p, t) in enumerate(all_results, 1):
    print(f"  第{i}轮: {p}/{t} ({p/t*100:.1f}%)")
print("=" * 70)
