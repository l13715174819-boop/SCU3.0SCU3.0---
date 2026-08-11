# -*- coding: utf-8 -*-
"""
tests/test_a4.py — A4公理校验测试
=================================
原则二落地测试：A4只管依赖方向，不管数据流方向

测试覆盖：
  1. 数据流动作不受A4约束（query/tool_call/layer_jump）
  2. 依赖类动作（import/modify/patch）受A4约束
  3. 依赖方向反向（D→W2）被A4拦截
  4. 依赖方向正向（W2→D）被A4放行
  5. 白名单动作（self_modify/tool_call/check/inspect）反向放行
  6. 依赖扫描器能发现违规import
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from d_layer.axioms import Operation
from guard.firewall import CUFGuard, DEPENDENCY_ACTIONS, DATAFLOW_ACTIONS


class TestA4DataFlowExemption(unittest.TestCase):
    """测试1：数据流动作不受A4约束"""

    def setUp(self):
        self.guard = CUFGuard(ledger=None, whitelist=None)

    def test_query_action_exempt_from_a4(self):
        """query 动作不受A4约束"""
        op = Operation(source="D", target="W2", action="query", op_id="t1")
        ok, msg, _ = self.guard.check(op)
        # query是只读，D→W2数据流，A4应跳过
        self.assertTrue(ok, f"query应放行: {msg}")

    def test_tool_call_action_exempt_from_a4(self):
        """tool_call 动作不受A4约束"""
        op = Operation(source="D", target="W2", action="tool_call", op_id="t2")
        ok, msg, _ = self.guard.check(op)
        self.assertTrue(ok, f"tool_call应放行: {msg}")

    def test_layer_jump_action_exempt_from_a4(self):
        """layer_jump 动作不受A4约束"""
        op = Operation(source="D", target="W2", action="layer_jump", op_id="t3")
        ok, msg, _ = self.guard.check(op)
        self.assertTrue(ok, f"layer_jump应放行: {msg}")

    def test_check_action_exempt_from_a4(self):
        """check 动作不受A4约束"""
        op = Operation(source="D", target="W2", action="check", op_id="t4")
        ok, msg, _ = self.guard.check(op)
        self.assertTrue(ok, f"check应放行: {msg}")


class TestA4DependencyCheck(unittest.TestCase):
    """测试2：依赖类动作受A4约束"""

    def setUp(self):
        self.guard = CUFGuard(ledger=None, whitelist=None)

    def test_modify_action_subject_to_a4(self):
        """modify 动作受A4约束"""
        self.assertIn("modify", DEPENDENCY_ACTIONS)

    def test_import_action_subject_to_a4(self):
        """import 动作受A4约束"""
        self.assertIn("import", DEPENDENCY_ACTIONS)

    def test_patch_action_subject_to_a4(self):
        """patch 动作受A4约束"""
        self.assertIn("patch", DEPENDENCY_ACTIONS)

    def test_dataflow_actions_not_in_dependency(self):
        """数据流动作不在依赖类集合中"""
        for action in DATAFLOW_ACTIONS:
            self.assertNotIn(action, DEPENDENCY_ACTIONS,
                             f"{action}不应在DEPENDENCY_ACTIONS中")


class TestA4ReverseDirection(unittest.TestCase):
    """测试3：依赖方向反向被拦截"""

    def setUp(self):
        self.guard = CUFGuard(ledger=None, whitelist=None)

    def test_d_to_w2_modify_blocked(self):
        """D→W2 modify 被A4拦截（底层依赖顶层）"""
        op = Operation(source="D", target="W2", action="modify", op_id="t5")
        ok, msg, _ = self.guard.check(op)
        # modify对D层 → 也会被A1拦截，但A4应先检查
        self.assertFalse(ok, f"D→W2 modify应被拦截: {msg}")

    def test_m_to_w1_modify_blocked(self):
        """M→W2 modify 被A4拦截"""
        op = Operation(source="M", target="W2", action="modify", op_id="t6")
        ok, msg, _ = self.guard.check(op)
        self.assertFalse(ok, f"M→W2 modify应被拦截: {msg}")


class TestA4ForwardDirection(unittest.TestCase):
    """测试4：依赖方向正向放行"""

    def setUp(self):
        self.guard = CUFGuard(ledger=None, whitelist=None)

    def test_w2_to_d_modify_allowed_by_a4(self):
        """W2→D modify A4放行（但A1会拦截写D层）"""
        # 注意：A4对W2→D是正向，会放行；但A1会拦截写D层
        # 这里直接测试A4逻辑
        op = Operation(source="W2", target="D", action="modify", op_id="t7")
        ok_a4, msg_a4 = self.guard._check_a4(op)
        self.assertTrue(ok_a4, f"W2→D modify A4应放行（正向）: {msg_a4}")

    def test_w1_to_m_modify_allowed_by_a4(self):
        """W1→M modify A4放行（正向）"""
        op = Operation(source="W1", target="M", action="modify", op_id="t8")
        ok_a4, msg_a4 = self.guard._check_a4(op)
        self.assertTrue(ok_a4, f"W1→M modify A4应放行（正向）: {msg_a4}")


class TestA4Whitelist(unittest.TestCase):
    """测试5：白名单动作反向放行"""

    def setUp(self):
        self.guard = CUFGuard(ledger=None, whitelist=None)

    def test_self_modify_in_whitelist(self):
        """self_modify 在A4白名单中"""
        from d_layer.axioms import A4_WHITELIST_ACTIONS
        self.assertIn("self_modify", A4_WHITELIST_ACTIONS)

    def test_tool_call_in_whitelist(self):
        """tool_call 在A4白名单中"""
        from d_layer.axioms import A4_WHITELIST_ACTIONS
        self.assertIn("tool_call", A4_WHITELIST_ACTIONS)


class TestDependencyScanner(unittest.TestCase):
    """测试6：依赖扫描器"""

    def test_scanner_can_run(self):
        """扫描器能运行"""
        from tools.dependency_scanner import scan_directory, check_a4_violation
        root = os.path.join(os.path.dirname(__file__), "..")
        results = scan_directory(root)
        self.assertIn("total_files", results)
        self.assertIn("violations", results)
        self.assertGreater(results["total_files"], 0)

    def test_scanner_detects_reverse_dependency(self):
        """扫描器能检测反向依赖"""
        from tools.dependency_scanner import check_a4_violation
        # 模拟 D→W2 import
        violation = {"src_layer": "d_layer", "tgt_layer": "w2_layer",
                     "module": "w2_layer.perception", "file": "test.py", "line": 1}
        is_violation, reason = check_a4_violation(violation)
        self.assertTrue(is_violation, "D→W2应被检测为违规")
        self.assertIn("A4违规", reason)

    def test_scanner_allows_forward_dependency(self):
        """扫描器放行正向依赖"""
        from tools.dependency_scanner import check_a4_violation
        # 模拟 W2→D import
        violation = {"src_layer": "w2_layer", "tgt_layer": "d_layer",
                     "module": "d_layer.axioms", "file": "test.py", "line": 1}
        is_violation, reason = check_a4_violation(violation)
        self.assertFalse(is_violation, "W2→D应被放行")

    def test_scanner_no_violations_in_scu2(self):
        """SCU2代码库无A4违规"""
        from tools.dependency_scanner import scan_directory
        root = os.path.join(os.path.dirname(__file__), "..")
        results = scan_directory(root)
        self.assertTrue(results["passed"], f"SCU2有A4违规: {results['violations']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
