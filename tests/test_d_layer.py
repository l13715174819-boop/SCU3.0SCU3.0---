# -*- coding: utf-8 -*-
"""
tests/test_d_layer.py — D层完整性测试（原则一）
===============================================
测试D层只放代码定义，运行时状态归W1层。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from guard.d_layer_integrity import get_checker, verify_on_startup


class TestDLayerIntegrity(unittest.TestCase):
    """D层完整性校验"""

    def setUp(self):
        self.checker = get_checker()

    def test_startup_verification_passes(self):
        """启动时D层校验通过"""
        ok, msg = verify_on_startup()
        self.assertTrue(ok, f"D层校验失败: {msg}")

    def test_a1_rejects_write_to_d(self):
        """A1拒绝写D层"""
        ok, msg = self.checker.check_a1_violation("D", "modify", "axioms.py")
        self.assertFalse(ok)
        self.assertIn("A1 违规", msg)

    def test_a1_rejects_patch_to_d(self):
        """A1拒绝patch D层"""
        ok, msg = self.checker.check_a1_violation("D", "patch", "contracts.py")
        self.assertFalse(ok)

    def test_a1_allows_query_w1(self):
        """A1放行W1层查询"""
        ok, msg = self.checker.check_a1_violation("W1", "query", "")
        self.assertTrue(ok)

    def test_a1_allows_tool_call_w2(self):
        """A1放行W2层工具调用"""
        ok, msg = self.checker.check_a1_violation("W2", "tool_call", "")
        self.assertTrue(ok)

    def test_d_layer_no_runtime_state(self):
        """D层文件不包含运行时状态"""
        ok, msg, _ = self.checker.verify_integrity()
        self.assertTrue(ok, f"D层包含运行时状态: {msg}")

    def test_checker_status(self):
        """校验器状态正常"""
        status = self.checker.get_status()
        self.assertGreater(status["files_monitored"], 0)
        self.assertTrue(status["enabled"])


class TestDLayerManifest(unittest.TestCase):
    """D层清单测试"""

    def test_manifest_exists(self):
        """MANIFEST.json存在"""
        manifest_path = os.path.join(
            os.path.dirname(__file__), "..", "d_layer", "MANIFEST.json")
        self.assertTrue(os.path.exists(manifest_path))

    def test_manifest_has_allowed_files(self):
        """清单包含允许的文件列表"""
        import json
        manifest_path = os.path.join(
            os.path.dirname(__file__), "..", "d_layer", "MANIFEST.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertIn("allowed_files", manifest)
        self.assertGreater(len(manifest["allowed_files"]), 0)

    def test_manifest_has_forbidden_list(self):
        """清单包含禁止项列表"""
        import json
        manifest_path = os.path.join(
            os.path.dirname(__file__), "..", "d_layer", "MANIFEST.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertIn("forbidden_in_d_layer", manifest)
        # 禁止项中应包含运行时余额相关条目
        forbidden = manifest["forbidden_in_d_layer"]
        self.assertTrue(any("余额" in item or "balance" in item for item in forbidden),
                        f"禁止项应包含余额相关条目: {forbidden}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
