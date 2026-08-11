# -*- coding: utf-8 -*-
"""
tests/run_all_tests.py — 统一测试运行器
======================================
运行所有测试并生成报告。
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run_all_tests():
    """运行所有测试"""
    # 发现所有测试
    loader = unittest.TestLoader()
    test_dir = os.path.dirname(__file__)
    suite = loader.discover(test_dir, pattern="test_*.py")

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    start_time = time.time()
    result = runner.run(suite)
    elapsed = time.time() - start_time

    # 生成报告
    print("\n" + "=" * 60)
    print("测试报告汇总")
    print("=" * 60)
    print(f"运行测试数: {result.testsRun}")
    print(f"成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    print(f"耗时: {elapsed:.2f}s")
    print(f"结果: {'✓ 全部通过' if result.wasSuccessful() else '✗ 有失败/错误'}")

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
