# -*- coding: utf-8 -*-
"""
test_multi_agent_modes.py — 多Agent双模式测试
==============================================
验证线程模式、进程模式、混合模式都能正常工作

运行：python test_multi_agent_modes.py
"""
import os
import sys
import time
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("scu2.test.modes")

from m_layer.multi_agent import (
    MultiAgentCoordinator,
    quick_thread_agents,
    quick_process_agents,
    quick_mixed_agents,
    get_multi_agent_coordinator,
)


def test_thread_mode():
    """测试1：线程模式"""
    print("\n" + "=" * 60)
    print("  测试1：线程模式（mode=thread）")
    print("=" * 60)

    subtasks = [
        {"subtask": "计算 3+5*2 的结果", "specialty": "general"},
        {"subtask": "获取当前时间", "specialty": "general"},
    ]

    start = time.time()
    result = quick_thread_agents(subtasks)
    elapsed = time.time() - start

    print(f"\n模式: {result.get('mode', 'thread')}")
    print(f"总数: {result['total_subtasks']}, 成功: {result['completed']}, 失败: {result['failed']}")
    print(f"耗时: {result.get('elapsed_ms', 0):.0f}ms (wall: {elapsed*1000:.0f}ms)")
    print(f"\n汇总:\n{result.get('summary', '')}")

    assert result["completed"] == 2, f"期望2个成功，实际{result['completed']}"
    assert result["mode"] == "thread"
    print("\n✓ 线程模式测试通过")
    return True


def test_process_mode():
    """测试2：进程模式"""
    print("\n" + "=" * 60)
    print("  测试2：进程模式（mode=process）")
    print("=" * 60)

    subtasks = [
        {"subtask": "计算 10*10 的结果", "specialty": "general"},
    ]

    start = time.time()
    result = quick_process_agents(subtasks)
    elapsed = time.time() - start

    print(f"\n模式: {result.get('mode', 'process')}")
    print(f"总数: {result['total_subtasks']}, 成功: {result['completed']}, 失败: {result['failed']}")
    print(f"耗时: {result.get('elapsed_ms', 0):.0f}ms (wall: {elapsed*1000:.0f}ms)")
    print(f"\n汇总:\n{result.get('summary', '')}")

    # 检查是否有 pid 字段（进程模式的标志）
    for sid, res in result.get("results", {}).items():
        if "pid" in res:
            print(f"  子代理 {sid} 运行在 PID={res['pid']}")

    assert result["completed"] == 1, f"期望1个成功，实际{result['completed']}"
    print("\n✓ 进程模式测试通过")
    return True


def test_mixed_mode():
    """测试3：混合模式"""
    print("\n" + "=" * 60)
    print("  测试3：混合模式（部分线程+部分进程）")
    print("=" * 60)

    subtasks = [
        {"subtask": "计算 1+1", "specialty": "general", "isolation": "thread"},
        {"subtask": "获取时间", "specialty": "general", "isolation": "process"},
    ]

    start = time.time()
    result = quick_mixed_agents(subtasks)
    elapsed = time.time() - start

    print(f"\n模式: {result.get('mode', 'mixed')}")
    print(f"总数: {result['total_subtasks']}, 成功: {result['completed']}, 失败: {result['failed']}")
    print(f"耗时: {result.get('elapsed_ms', 0):.0f}ms (wall: {elapsed*1000:.0f}ms)")
    print(f"\n汇总:\n{result.get('summary', '')}")

    # 验证两种隔离模式都出现了
    isolations = set()
    for sid, res in result.get("results", {}).items():
        iso = res.get("isolation", "?")
        isolations.add(iso)
        print(f"  子代理 {sid}: isolation={iso}" + (f", PID={res.get('pid')}" if "pid" in res else ""))

    assert "thread" in isolations, "缺少线程模式任务"
    assert "process" in isolations, "缺少进程模式任务"
    print("\n✓ 混合模式测试通过（线程+进程并存）")
    return True


def test_dependency():
    """测试4：依赖关系（DAG）"""
    print("\n" + "=" * 60)
    print("  测试4：依赖关系（A→C, B→C）")
    print("=" * 60)

    coord = MultiAgentCoordinator(mode="thread")
    a_id = coord.assign_subtask("计算 2+3", specialty="general", subtask_id="A")
    b_id = coord.assign_subtask("计算 4*5", specialty="general", subtask_id="B")
    coord.assign_subtask("汇总上述两个计算结果", specialty="writing",
                         depends_on=[a_id, b_id], subtask_id="C")

    result = coord.execute_all()

    print(f"\n模式: {result.get('mode')}")
    print(f"总数: {result['total_subtasks']}, 成功: {result['completed']}, 失败: {result['failed']}")
    print(f"\n汇总:\n{result.get('summary', '')}")

    assert result["completed"] == 3, f"期望3个成功，实际{result['completed']}"
    print("\n✓ 依赖关系测试通过")
    return True


def test_invalid_mode():
    """测试5：无效模式"""
    print("\n" + "=" * 60)
    print("  测试5：无效模式处理")
    print("=" * 60)

    try:
        coord = MultiAgentCoordinator(mode="invalid")
        print("✗ 应该抛出异常但未抛出")
        return False
    except ValueError as e:
        print(f"✓ 正确捕获无效模式异常: {e}")
        return True


def main():
    print("=" * 60)
    print("  SCU2 多Agent双模式测试")
    print("=" * 60)

    tests = [
        ("线程模式", test_thread_mode),
        ("进程模式", test_process_mode),
        ("混合模式", test_mixed_mode),
        ("依赖关系", test_dependency),
        ("无效模式", test_invalid_mode),
    ]

    results = []
    for name, test_fn in tests:
        try:
            passed = test_fn()
            results.append((name, passed))
        except Exception as e:
            print(f"\n✗ {name} 测试异常: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # 汇总
    print("\n" + "=" * 60)
    print("  测试汇总")
    print("=" * 60)
    passed_count = sum(1 for _, p in results if p)
    for name, passed in results:
        print(f"  [{'✓' if passed else '✗'}] {name}")
    print(f"\n  通过: {passed_count}/{len(results)}")
    print("=" * 60)

    return 0 if passed_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
