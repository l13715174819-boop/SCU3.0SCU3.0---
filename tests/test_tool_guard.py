# -*- coding: utf-8 -*-
"""
tests/test_tool_guard.py — 工具守卫测试（原则四）
=================================================
测试工具调用独立守卫，按read/write定税。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from guard.tool_guard import ToolGuard, TOOL_TYPE_MAP, TAX_RATES


class TestToolTypeMap(unittest.TestCase):
    """工具类型映射测试"""

    def test_13_tools_registered(self):
        """13种工具全部注册"""
        self.assertEqual(len(TOOL_TYPE_MAP), 13)

    def test_read_tools_count(self):
        """只读工具11种"""
        read_count = sum(1 for v in TOOL_TYPE_MAP.values() if v == "read")
        self.assertEqual(read_count, 11)

    def test_write_tools_count(self):
        """写操作工具2种"""
        write_count = sum(1 for v in TOOL_TYPE_MAP.values() if v == "write")
        self.assertEqual(write_count, 2)

    def test_calculator_is_read(self):
        """calculator是只读"""
        self.assertEqual(TOOL_TYPE_MAP["calculator"], "read")

    def test_file_write_is_write(self):
        """file_write是写操作"""
        self.assertEqual(TOOL_TYPE_MAP["file_write"], "write")

    def test_code_run_is_write(self):
        """code_run是写操作"""
        self.assertEqual(TOOL_TYPE_MAP["code_run"], "write")

    def test_weather_is_read(self):
        """weather是只读"""
        self.assertEqual(TOOL_TYPE_MAP["weather"], "read")


class TestToolGuardCheck(unittest.TestCase):
    """工具守卫审计测试"""

    def setUp(self):
        self.guard = ToolGuard(ledger=None)

    def test_read_tool_passes_without_ledger(self):
        """只读工具无账本时放行"""
        ok, msg, detail = self.guard.check("calculator", op_id="t1")
        self.assertTrue(ok)

    def test_write_tool_passes_without_ledger(self):
        """写工具无账本时放行"""
        ok, msg, detail = self.guard.check("file_write", op_id="t2")
        self.assertTrue(ok)

    def test_unknown_tool_defaults_to_read(self):
        """未知工具默认read"""
        tool_type = self.guard.get_tool_type("unknown_tool")
        self.assertEqual(tool_type, "read")

    def test_is_readonly_true_for_read(self):
        """is_readonly对只读工具返回True"""
        self.assertTrue(self.guard.is_readonly("calculator"))
        self.assertTrue(self.guard.is_readonly("weather"))

    def test_is_readonly_false_for_write(self):
        """is_readonly对写工具返回False"""
        self.assertFalse(self.guard.is_readonly("file_write"))
        self.assertFalse(self.guard.is_readonly("code_run"))

    def test_status(self):
        """状态查询正常"""
        status = self.guard.get_status()
        self.assertEqual(status["total_tools"], 13)
        self.assertEqual(status["read_tools"], 11)
        self.assertEqual(status["write_tools"], 2)


class TestTaxRates(unittest.TestCase):
    """税率配置测试"""

    def test_read_tax_low(self):
        """只读税率低"""
        self.assertEqual(TAX_RATES["read"], 0.2)

    def test_write_tax_high(self):
        """写税率高"""
        self.assertEqual(TAX_RATES["write"], 3.0)

    def test_write_tax_higher_than_read(self):
        """写税率高于只读"""
        self.assertGreater(TAX_RATES["write"], TAX_RATES["read"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
