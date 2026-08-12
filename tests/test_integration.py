# -*- coding: utf-8 -*-
"""
tests/test_integration.py — 集成测试（端到端流程）
=================================================
测试SCU3完整数据流：感知→守卫→记忆→执行→守卫→认知→元认知→过滤→输出
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestEndToEndFlow(unittest.TestCase):
    """端到端流程测试"""

    def test_process_request_normal(self):
        """正常请求流程"""
        from server import process_request
        result = process_request("你好", "test_user")
        self.assertTrue(result["success"])
        self.assertIn("response", result)
        self.assertIn("op_id", result)
        self.assertIn("pattern_key", result)
        self.assertIn("cuf_traces", result)

    def test_process_request_with_tool(self):
        """工具调用流程"""
        from server import process_request
        result = process_request("计算 3+5", "test_user")
        self.assertTrue(result["success"])
        self.assertIn("response", result)

    def test_cuf_traces_present(self):
        """CUF审计轨迹存在"""
        from server import process_request
        result = process_request("测试", "test_user")
        self.assertIsInstance(result["cuf_traces"], list)

    def test_balance_present(self):
        """余额信息存在"""
        from server import process_request
        result = process_request("测试", "test_user")
        self.assertIn("balance", result)
        self.assertGreaterEqual(result["balance"], 0)

    def test_filter_warnings_field(self):
        """过滤警告字段存在"""
        from server import process_request
        result = process_request("测试", "test_user")
        self.assertIn("filter_warnings", result)

    def test_elapsed_ms_present(self):
        """耗时字段存在"""
        from server import process_request
        result = process_request("测试", "test_user")
        self.assertIn("elapsed_ms", result)
        self.assertGreater(result["elapsed_ms"], 0)


class TestSameLayerBypass(unittest.TestCase):
    """同层免审测试（原则三）"""

    def test_same_layer_bypass_logged(self):
        """同层免审有日志"""
        from d_layer.axioms import Operation
        from guard.firewall import CUFGuard
        guard = CUFGuard(ledger=None, whitelist=None)
        op = Operation(source="W1", target="W1", action="query", op_id="t1")
        ok, msg, details = guard.check(op)
        self.assertTrue(ok)
        # 检查same_layer_bypass标记
        axiom_logs = details.get("axioms_checked", [])
        same_layer_log = [a for a in axiom_logs if a.get("axiom") == "same_layer"]
        self.assertTrue(len(same_layer_log) > 0)
        self.assertTrue(same_layer_log[0].get("same_layer_bypass"))


class TestRefundMechanism(unittest.TestCase):
    """补偿退款测试"""

    def test_refund_on_failure(self):
        """业务失败触发退款"""
        from guard.firewall import CUFGuard
        guard = CUFGuard(ledger=None, whitelist=None)
        # 模拟登记待退款
        guard._pending_refunds["op_test"] = 1.5
        # 触发退款
        tax = guard.refund_on_failure("op_test", "测试失败")
        self.assertEqual(tax, 1.5)
        # 重复退款应返回0
        tax2 = guard.refund_on_failure("op_test", "重复退款")
        self.assertEqual(tax2, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
