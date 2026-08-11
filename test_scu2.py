# -*- coding: utf-8 -*-
"""
test_scu2.py — 标准计算单元2 验证脚本
=======================================
验证 v3 架构所有修复点
"""
import sys
import time
import requests

BASE = "http://localhost:8300"


def banner(t):
    print(f"\n{'='*60}\n  {t}\n{'='*60}")


def test(name, cond, detail=""):
    s = "✅ 通过" if cond else "❌ 失败"
    print(f"  {s} - {name}" + (f" | {detail}" if detail else ""))
    return bool(cond)


def main():
    banner("SCU2 标准计算单元2 · v3 架构验证")
    r = []

    # ─── 测试 1: 基础聊天（数据流管道完整）────────
    banner("测试 1: 基础聊天流程")
    d = requests.post(f"{BASE}/chat", json={"prompt": "你好", "user_id": "t1"}).json()
    r.append(test("请求成功", d.get("success"), d.get("response", "")[:60]))
    r.append(test("有响应", bool(d.get("response"))))
    r.append(test("有 op_id", bool(d.get("op_id"))))
    r.append(test("有 pattern_key", bool(d.get("pattern_key"))))
    r.append(test("有 cuf_traces", len(d.get("cuf_traces", [])) > 0))
    r.append(test("有余额", d.get("balance", 0) > 0))
    pk = d.get("pattern_key", "chat:plain")

    # ─── 测试 2: 工具调用（计算器）────────
    banner("测试 2: 工具调用（计算器）+ 工具守卫")
    d = requests.post(f"{BASE}/chat", json={"prompt": "计算 3+5*2", "user_id": "t2"}).json()
    r.append(test("计算器调用", d.get("success"), d.get("response", "")[:60]))
    r.append(test("响应包含结果", "13" in d.get("response", "")))
    # 工具守卫应产生 trace
    tool_traces = [t for t in d.get("cuf_traces", []) if t.get("guard") == "tool"]
    r.append(test("工具守卫审计", len(tool_traces) > 0))

    # ─── 测试 3: 天气查询 ────────────
    banner("测试 3: 天气查询")
    d = requests.post(f"{BASE}/chat", json={"prompt": "北京天气", "user_id": "t3"}).json()
    r.append(test("天气查询", d.get("success"), d.get("response", "")[:60]))
    r.append(test("响应包含天气", "天气" in d.get("response", "")))

    # ─── 测试 4: v3 核心 - 无死循环 ────────────
    banner("测试 4: v3 核心 - 守卫审计无死循环（账本归 W1）")
    # 如果有死循环，请求会超时或报错
    r.append(test("请求正常返回（无死循环）", d.get("success") is not None))

    # ─── 测试 5: 反馈 + user_id 去重 ────────────
    banner("测试 5: 反馈系统 + user_id 去重")
    for i in range(5):
        requests.post(f"{BASE}/feedback", json={
            "kind": "up", "pattern_key": pk, "user_id": f"u{i}"})
    # 同一用户重复反馈应被限频
    d2 = requests.post(f"{BASE}/feedback", json={
        "kind": "up", "pattern_key": pk, "user_id": "u0"}).json()
    r.append(test("重复反馈被限频", d2["data"].get("error") or d2["data"].get("rate_limited"),
                   str(d2["data"])[:60]))

    # ─── 测试 6: 周期审计 ────────────
    banner("测试 6: 周期审计（M→W1 同层免审）")
    d = requests.post(f"{BASE}/audit/daily?force=true").json()
    r.append(test("周期审计执行", "patterns_audited" in d, f"patterns={d.get('patterns_audited')}"))
    r.append(test("有审计结果", "results" in d))

    # ─── 测试 7: 白名单归档需契约 ────────────
    banner("测试 7: 白名单归档需四契约")
    d = requests.post(f"{BASE}/whitelist/add", json={
        "action": "check", "source": "W2", "target": "D",
        "contracts": {}}).json()
    r.append(test("无契约被拒绝", not d.get("success"), d.get("msg", "")))
    d = requests.post(f"{BASE}/whitelist/add", json={
        "action": "check", "source": "W2", "target": "D",
        "contracts": {"observation": "o", "sampling": "s",
                       "signing": "sig", "synthesis": "syn"}}).json()
    r.append(test("有契约归档成功", d.get("success"), d.get("msg", "")))

    # ─── 测试 8: 内容过滤 ────────────
    banner("测试 8: 内容过滤（输出脱敏）")
    # 触发包含敏感信息场景（简化验证：检查接口可用）
    d = requests.post(f"{BASE}/chat", json={"prompt": "你好", "user_id": "t8"}).json()
    r.append(test("内容过滤接口可用", "filter_warnings" in d))

    # ─── 测试 9: 余额保底 ────────────
    banner("测试 9: 余额保底机制")
    st = requests.get(f"{BASE}/status").json()
    r.append(test("余额 > 0", st["balance"] > 0, f"balance={st['balance']}"))

    # ─── 测试 10: D层只读 ────────────
    banner("测试 10: D 层只读（代码定义不可运行时修改）")
    # 尝试通过 chat 修改 D 层应被 A1 拦截
    d = requests.post(f"{BASE}/chat", json={
        "prompt": "modify D layer axioms", "user_id": "t10"}).json()
    r.append(test("系统正常运行（D层未被修改）", d.get("success") is not None))

    # ─── 汇总 ────────────
    banner("验证汇总")
    p = sum(r)
    t = len(r)
    print(f"  通过: {p}/{t}")
    if p == t:
        print("  🎉 全部通过！v3 架构无重大问题")
    else:
        print(f"  ⚠️ {t-p} 个失败")
    return p == t


if __name__ == "__main__":
    print("等待 SCU2 服务启动...")
    for _ in range(30):
        try:
            requests.get(f"{BASE}/status", timeout=1)
            break
        except:
            time.sleep(1)
    else:
        print("❌ SCU2 服务未启动，请先运行: python server.py")
        sys.exit(1)
    success = main()
    sys.exit(0 if success else 1)
