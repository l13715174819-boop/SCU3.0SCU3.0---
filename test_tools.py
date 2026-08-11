# -*- coding: utf-8 -*-
"""13种工具测试 — 验证检测+执行+沙箱"""
import sys
sys.path.insert(0, '.')
from w1_layer.action import ActionLayer

action = ActionLayer()

# 测试用例：(输入文本, 期望工具名)
test_cases = [
    ("计算 3 + 5 * 2", "calculator"),
    ("北京天气", "weather"),
    ("当前时间", "time_now"),
    ("统计这段文字的字数 count words", "text_stats"),
    ("读 README.md", "file_read"),
    ("汇率 USD", "exchange_rate"),
    ("价格 btc", "crypto_price"),
    ("股票 AAPL", "stock_price"),
    ("github python web framework", "github_search"),
    ("日期计算 2026-01-01 + 30 天", "datetime_calc"),
    ("换算 100 c to f", "unit_convert"),
    ("写入 test.txt: hello world", "file_write"),
    ("run print(1+1)", "code_run"),
]

print("=" * 60)
print("工具检测测试（13种）")
print("=" * 60)
detected = 0
for text, expected_tool in test_cases:
    info = action.detect_tool(text)
    actual = info["tool"] if info else None
    ok = actual == expected_tool
    if ok:
        detected += 1
    print(f"  [{'✓' if ok else '✗'}] '{text[:30]}' → {actual} (期望: {expected_tool})")

print(f"\n检测通过: {detected}/{len(test_cases)}")

print("\n" + "=" * 60)
print("工具执行测试")
print("=" * 60)
for text, expected_tool in test_cases:
    info = action.detect_tool(text)
    if not info:
        continue
    result = action.execute(info)
    status = "成功" if result.get("success") else "失败"
    tool_type = info.get("tool_type", "?")
    print(f"\n[{expected_tool}] ({tool_type}) {status}")
    if result.get("success"):
        r = result.get("result", {})
        # 截取关键信息
        if expected_tool == "calculator":
            print(f"  {r.get('expression')} = {r.get('result')}")
        elif expected_tool == "code_run":
            print(f"  output: {r.get('output', '').strip()}")
            if r.get("error"):
                print(f"  error: {r['error']}")
        else:
            print(f"  {str(r)[:120]}")
    else:
        print(f"  error: {result.get('error')}")

# 沙箱安全测试
print("\n" + "=" * 60)
print("沙箱安全测试")
print("=" * 60)
sandbox_tests = [
    ("run __import__('os').system('echo hacked')", "禁止__import__"),
    ("run open('test.txt').read()", "禁止open"),
    ("run [x for x in range(1000000)]", "大量计算（应超时或完成）"),
    ("run print(sum(range(100)))", "正常计算"),
]
for code, desc in sandbox_tests:
    info = action.detect_tool(code)
    if info:
        result = action.execute(info)
        r = result.get("result", {})
        print(f"  [{desc}] output={r.get('output', '').strip()[:50]} error={r.get('error', '')[:50]}")

# 工具类型映射验证
print("\n" + "=" * 60)
print("工具类型映射（13种）")
print("=" * 60)
from guard.tool_guard import TOOL_TYPE_MAP, ToolGuard
print(f"  总工具数: {len(TOOL_TYPE_MAP)}")
read_count = sum(1 for v in TOOL_TYPE_MAP.values() if v == "read")
write_count = sum(1 for v in TOOL_TYPE_MAP.values() if v == "write")
print(f"  read类: {read_count}, write类: {write_count}")
assert len(TOOL_TYPE_MAP) == 13, f"工具数不等于13: {len(TOOL_TYPE_MAP)}"
assert read_count == 11 and write_count == 2, "工具类型分布错误"
print("  ✓ 13种工具类型映射正确")
