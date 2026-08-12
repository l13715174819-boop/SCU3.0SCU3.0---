# -*- coding: utf-8 -*-
"""阶段1门槛验证脚本"""
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    print("=" * 60)
    print("SCU3 阶段1 门槛验证")
    print("=" * 60)

    results = []

    # 门槛1: D层完整性
    print("\n[门槛1] D层完整性校验 + A1拒绝写D层")
    from guard.d_layer_integrity import get_checker, verify_on_startup
    ok1, msg1 = verify_on_startup()
    checker = get_checker()
    ok1b, _ = checker.check_a1_violation("D", "modify", "axioms.py")
    gate1 = ok1 and not ok1b
    print(f"  D层校验: {'✓' if ok1 else '✗'} {msg1}")
    print(f"  A1拒绝写D层: {'✓' if not ok1b else '✗'}")
    print(f"  门槛1: {'✓ 通过' if gate1 else '✗ 失败'}")
    results.append(("门槛1: D层完整性", gate1))

    # 门槛2: A4正确性
    print("\n[门槛2] A4数据流豁免 + 依赖校验")
    from tools.dependency_scanner import scan_directory
    scan_results = scan_directory(".")
    gate2 = scan_results["passed"]
    print(f"  A4扫描: 扫描{scan_results['total_files']}个文件, {len(scan_results['violations'])}个违规")
    print(f"  门槛2: {'✓ 通过' if gate2 else '✗ 失败'}")
    results.append(("门槛2: A4正确性", gate2))

    # 门槛3: 守卫点清晰
    print("\n[门槛3] 守卫点文档化 + 同层免审日志")
    doc_path = os.path.join(os.path.dirname(__file__), "docs", "guard_points.md")
    gate3 = os.path.exists(doc_path)
    print(f"  守卫点文档: {'✓ 存在' if gate3 else '✗ 缺失'}")
    from guard.firewall import CUFGuard
    from d_layer.axioms import Operation
    guard = CUFGuard(ledger=None, whitelist=None)
    op = Operation(source="W1", target="W1", action="query", op_id="gate3")
    _, _, details = guard.check(op)
    has_bypass = any(a.get("same_layer_bypass") for a in details.get("axioms_checked", []))
    gate3 = gate3 and has_bypass
    print(f"  同层免审日志: {'✓' if has_bypass else '✗'}")
    print(f"  门槛3: {'✓ 通过' if gate3 else '✗ 失败'}")
    results.append(("门槛3: 守卫点清晰", gate3))

    # 门槛4: 内容过滤
    print("\n[门槛4] 内容过滤强制调用 + 40+规则")
    from guard.content_filter import ContentFilter
    cf = ContentFilter()
    rule_count = len(cf.SENSITIVE_PATTERNS)
    gate4 = rule_count >= 40
    print(f"  规则数: {rule_count} ({'✓' if gate4 else '✗'} 需≥40)")
    # 测试强制调用
    import inspect
    from server import _build_response
    source = inspect.getsource(_build_response)
    has_filter = "content_filter.filter" in source
    gate4 = gate4 and has_filter
    print(f"  _build_response强制调用: {'✓' if has_filter else '✗'}")
    print(f"  门槛4: {'✓ 通过' if gate4 else '✗ 失败'}")
    results.append(("门槛4: 内容过滤", gate4))

    # 门槛5: 测试框架
    print("\n[门槛5] 单元+集成+安全+性能测试")
    test_files = []
    for f in os.listdir(os.path.join(os.path.dirname(__file__), "tests")):
        if f.startswith("test_") and f.endswith(".py"):
            test_files.append(f)
    has_unit = any("d_layer" in f or "tool_guard" in f for f in test_files)
    has_integration = any("integration" in f for f in test_files)
    has_security = any("a4" in f for f in test_files)
    has_performance = any("performance" in f for f in test_files)
    gate5 = has_unit and has_integration and has_security and has_performance
    print(f"  单元测试: {'✓' if has_unit else '✗'}")
    print(f"  集成测试: {'✓' if has_integration else '✗'}")
    print(f"  安全测试: {'✓' if has_security else '✗'}")
    print(f"  性能测试: {'✓' if has_performance else '✗'}")
    print(f"  测试文件数: {len(test_files)}")
    print(f"  门槛5: {'✓ 通过' if gate5 else '✗ 失败'}")
    results.append(("门槛5: 测试框架", gate5))

    # 汇总
    print("\n" + "=" * 60)
    print("门槛验证汇总")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        print(f"  {name}: {'✓ 通过' if passed else '✗ 失败'}")
        if not passed:
            all_passed = False

    print(f"\n{'✅ 阶段1门槛全部通过，可进入阶段2' if all_passed else '❌ 阶段1门槛未全部通过，需修复'}")
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
