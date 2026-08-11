# -*- coding: utf-8 -*-
"""Qwen2.5-7B vs 3B 对比测试"""
import urllib.request, json, time

API_BASE = "http://localhost:8000"
HEADERS = {"X-API-Key": "scu2_dev_key_2026", "Content-Type": "application/json"}
ADMIN_HEADERS = {"X-API-Key": "scu2_admin_key_2026", "Content-Type": "application/json"}

def api_post(path, data, timeout=180):
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(data).encode("utf-8"),
        headers=HEADERS,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

print("=" * 60)
print("Qwen2.5-7B-Instruct 对比测试（vs 3B 基线）")
print("=" * 60)

# 1. 模型状态
print("\n[1] 当前模型状态")
req = urllib.request.Request(f"{API_BASE}/local-model/status", headers=ADMIN_HEADERS)
with urllib.request.urlopen(req, timeout=10) as r:
    s = json.loads(r.read().decode("utf-8")).get("status", {})
    print(f"  模型: {s.get('model_name')}")
    print(f"  设备: {s.get('device')}")
    print(f"  量化: {s.get('quantization')}")
    print(f"  GPU空闲显存: {s.get('gpu_memory_free_gb')}GB")

# 2. 对话测试（与3B相同的测试题）
print("\n[2] 对话测试")
test_cases = [
    ("你好，请用一句话介绍自己。", "自我介绍", "3B: 我是SCU2 v5.0...（流畅但简单）"),
    ("什么是向量数据库？", "知识问答", "3B: 向量数据库是一种用于存储...（正确）"),
    ("用Python写一个hello world", "代码生成", "3B: print('Hello World')（正确）"),
    ("1+1等于几？", "简单推理", "3B: 1+1等于2（正确）"),
    # 7B 优势场景
    ("用Python实现二分查找，要求处理空列表和重复元素", "复杂代码", "3B: 基本版本（无边界处理）"),
    ("解释什么是闭包，并给出一个实际应用场景", "概念+应用", "3B: 概念正确但应用场景笼统"),
    ("一个房间有3个开关控制隔壁房间的3盏灯，你只能进入隔壁房间一次，如何确定每个开关对应哪盏灯？", "逻辑推理", "3B: 可能答不对"),
    ("SCU2 v5.0有哪些新功能？", "RAG问答", "3B: 列出4项（正确）"),
]

results = []
for prompt, tag, baseline in test_cases:
    print(f"\n  [{tag}] 用户: {prompt}")
    print(f"  3B基线: {baseline}")
    try:
        start = time.time()
        r = api_post("/chat", {"prompt": prompt}, timeout=180)
        elapsed = time.time() - start
        response = r.get("response", "")
        print(f"  7B回答: {response[:400]}")
        print(f"  [elapsed={elapsed:.2f}s, success={r.get('success')}]")
        results.append((tag, elapsed, r.get("success", False), response))
    except Exception as e:
        print(f"  [ERROR] {e}")
        results.append((tag, 0, False, str(e)))

# 3. 流式测试
print("\n[3] 流式生成测试")
try:
    req = urllib.request.Request(
        f"{API_BASE}/chat/stream",
        data=json.dumps({"prompt": "用100字解释什么是RAG及其工作原理"}).encode("utf-8"),
        headers=HEADERS,
        method="POST",
    )
    start = time.time()
    chunks = []
    with urllib.request.urlopen(req, timeout=120) as r:
        for line in r:
            line = line.decode("utf-8").strip()
            if line.startswith("data: "):
                data = json.loads(line[6:])
                t = data.get("type")
                if t == "chunk":
                    chunks.append(data.get("content", ""))
                elif t == "meta":
                    print(f"  meta: mode={data.get('mode')}")
                elif t == "done":
                    elapsed = time.time() - start
                    full = "".join(chunks)
                    print(f"  7B流式: {full[:400]}")
                    print(f"  [chunks={len(chunks)}, elapsed={elapsed:.2f}s]")
                    break
except Exception as e:
    print(f"  [ERROR] {e}")

# 4. 统计
print("\n[4] 统计对比")
success_count = sum(1 for _, _, s, _ in results if s)
avg_latency = sum(l for _, l, s, _ in results if s) / max(success_count, 1)
print(f"  7B 成功率: {success_count}/{len(results)}")
print(f"  7B 平均延迟: {avg_latency:.2f}s")
print(f"  3B 平均延迟: 3.83s (基线)")
print(f"  延迟变化: {(avg_latency - 3.83) / 3.83 * 100:+.1f}%")

# 最终模型状态
req = urllib.request.Request(f"{API_BASE}/local-model/status", headers=ADMIN_HEADERS)
with urllib.request.urlopen(req, timeout=10) as r:
    s = json.loads(r.read().decode("utf-8")).get("status", {})
    print(f"\n  最终GPU空闲显存: {s.get('gpu_memory_free_gb')}GB")
    print(f"  模型调用次数: {s.get('call_count')}")
    print(f"  成功率: {s.get('success_rate')}%")

print("\n" + "=" * 60)
print("7B 对比测试完成！")
print("=" * 60)
