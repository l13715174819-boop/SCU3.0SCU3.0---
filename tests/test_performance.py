# -*- coding: utf-8 -*-
"""
tests/test_performance.py — 性能基线测试
=========================================
验证SCU3基本性能指标。
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestResponseLatency(unittest.TestCase):
    """响应延迟测试"""

    def test_single_request_under_500ms(self):
        """单次请求延迟 < 500ms"""
        from server import process_request
        start = time.time()
        process_request("测试", "perf_user")
        elapsed_ms = (time.time() - start) * 1000
        self.assertLess(elapsed_ms, 500, f"请求延迟 {elapsed_ms:.0f}ms 超过 500ms")

    def test_10_requests_under_3s(self):
        """10次请求总耗时 < 3s"""
        from server import process_request
        start = time.time()
        for i in range(10):
            process_request(f"测试{i}", "perf_user")
        elapsed = time.time() - start
        self.assertLess(elapsed, 3.0, f"10次请求耗时 {elapsed:.2f}s 超过 3s")


class TestGuardPerformance(unittest.TestCase):
    """守卫性能测试"""

    def test_guard_check_under_10ms(self):
        """守卫检查延迟 < 10ms"""
        from d_layer.axioms import Operation
        from guard.firewall import CUFGuard
        guard = CUFGuard(ledger=None, whitelist=None)
        op = Operation(source="W2", target="W1", action="query", op_id="perf1")

        start = time.time()
        guard.check(op)
        elapsed_ms = (time.time() - start) * 1000
        self.assertLess(elapsed_ms, 10, f"守卫检查 {elapsed_ms:.2f}ms 超过 10ms")

    def test_100_guard_checks_under_500ms(self):
        """100次守卫检查 < 500ms"""
        from d_layer.axioms import Operation
        from guard.firewall import CUFGuard
        guard = CUFGuard(ledger=None, whitelist=None)

        start = time.time()
        for i in range(100):
            op = Operation(source="W2", target="W1", action="query", op_id=f"perf{i}")
            guard.check(op)
        elapsed_ms = (time.time() - start) * 1000
        self.assertLess(elapsed_ms, 500, f"100次检查 {elapsed_ms:.0f}ms 超过 500ms")


class TestFilterPerformance(unittest.TestCase):
    """内容过滤性能测试"""

    def test_filter_under_5ms(self):
        """内容过滤 < 5ms"""
        from guard.content_filter import ContentFilter
        cf = ContentFilter()
        text = "这是一段普通文本，用于测试过滤性能。" * 10

        start = time.time()
        cf.filter(text)
        elapsed_ms = (time.time() - start) * 1000
        self.assertLess(elapsed_ms, 5, f"过滤 {elapsed_ms:.2f}ms 超过 5ms")

    def test_filter_long_text_under_50ms(self):
        """长文本过滤 < 50ms"""
        from guard.content_filter import ContentFilter
        cf = ContentFilter()
        text = "测试文本 " * 1000  # 约4KB

        start = time.time()
        cf.filter(text)
        elapsed_ms = (time.time() - start) * 1000
        self.assertLess(elapsed_ms, 50, f"长文本过滤 {elapsed_ms:.2f}ms 超过 50ms")


class TestDLayerIntegrityPerformance(unittest.TestCase):
    """D层校验性能测试"""

    def test_integrity_check_under_100ms(self):
        """D层完整性校验 < 100ms"""
        from guard.d_layer_integrity import get_checker
        checker = get_checker()

        start = time.time()
        checker.verify_integrity()
        elapsed_ms = (time.time() - start) * 1000
        self.assertLess(elapsed_ms, 100, f"校验 {elapsed_ms:.2f}ms 超过 100ms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
