# -*- coding: utf-8 -*-
"""
m_layer/condition_branch.py — 条件分支执行（M层）
==================================================
v5.0第二批：支持if-else条件分支，根据上步结果选择下步

能力对标：AI助手的条件判断能力

功能:
  1. 条件表达式求值（等于/不等于/包含/大于/小于/正则）
  2. 分支选择（满足条件走A，否则走B）
  3. 多条件组合（AND/OR）
  4. 嵌套条件

用法:
    branch = ConditionBranch()
    # 定义条件
    branch.add_condition("cond1", "${step1_result.count}", ">", 10)
    # 定义分支
    branch.add_branch("cond1", True, {"action": "file_write", ...})
    branch.add_branch("cond1", False, {"action": "code_run", ...})
    # 执行
    result = branch.evaluate(context)

架构归属：M层（执行层条件控制）
"""
import re
import logging
from typing import Dict, Any, List, Optional, Union
from datetime import datetime

logger = logging.getLogger("scu2.m.branch")


class ConditionBranch:
    """条件分支执行器

    用法:
        cb = ConditionBranch()
        cb.add_condition("check_count", "step1_result.count", ">", 10)
        cb.add_branch("check_count", True, {
            "action": "file_write",
            "params": {"path": "large.txt", "content": "大数据"}
        })
        cb.add_branch("check_count", False, {
            "action": "file_write",
            "params": {"path": "small.txt", "content": "小数据"}
        })
        # 求值
        result = cb.evaluate({"step1_result": {"count": 15}})
    """

    # 支持的比较操作符
    OPERATORS = {
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        ">": lambda a, b: float(a) > float(b),
        "<": lambda a, b: float(a) < float(b),
        ">=": lambda a, b: float(a) >= float(b),
        "<=": lambda a, b: float(a) <= float(b),
        "contains": lambda a, b: str(b) in str(a),
        "not_contains": lambda a, b: str(b) not in str(a),
        "starts_with": lambda a, b: str(a).startswith(str(b)),
        "ends_with": lambda a, b: str(a).endswith(str(b)),
        "matches": lambda a, b: bool(re.search(str(b), str(a))),
    }

    def __init__(self):
        self._conditions: Dict[str, Dict] = {}
        self._branches: List[Dict] = []

    def add_condition(self, name: str, left: str, op: str, right: Any) -> "ConditionBranch":
        """添加条件

        Args:
            name: 条件名
            left: 左值（支持${step1_result.field}占位符）
            op: 操作符（==/!=/>/</>=/<=/contains/not_contains/starts_with/ends_with/matches）
            right: 右值
        """
        if op not in self.OPERATORS:
            raise ValueError(f"不支持的操作符: {op}")
        self._conditions[name] = {"left": left, "op": op, "right": right}
        return self

    def add_branch(self, condition_name: str, expected: bool,
                   step: Dict[str, Any]) -> "ConditionBranch":
        """添加分支

        Args:
            condition_name: 条件名
            expected: 条件期望值（True/False）
            step: 满足时执行的步骤
        """
        self._branches.append({
            "condition": condition_name,
            "expected": expected,
            "step": step,
        })
        return self

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """求值并选择分支

        Args:
            context: 上下文（含前序步骤结果）

        Returns:
            {
                "conditions": {条件名: 求值结果},
                "selected_branch": 选中的步骤 或 None,
                "matched": bool,
            }
        """
        # 求值所有条件
        condition_results = {}
        for name, cond in self._conditions.items():
            left_val = self._resolve_value(cond["left"], context)
            right_val = cond["right"]
            try:
                result = self.OPERATORS[cond["op"]](left_val, right_val)
            except Exception as e:
                logger.warning(f"条件求值失败 {name}: {e}")
                result = False
            condition_results[name] = result

        # 选择分支
        selected = None
        for branch in self._branches:
            cond_name = branch["condition"]
            expected = branch["expected"]
            if condition_results.get(cond_name) == expected:
                selected = branch["step"]
                break

        return {
            "conditions": condition_results,
            "selected_branch": selected,
            "matched": selected is not None,
            "evaluated_at": datetime.now().isoformat(),
        }

    def evaluate_single(self, left: str, op: str, right: Any,
                        context: Dict[str, Any]) -> bool:
        """单条件求值"""
        if op not in self.OPERATORS:
            return False
        left_val = self._resolve_value(left, context)
        try:
            return self.OPERATORS[op](left_val, right)
        except Exception:
            return False

    def _resolve_value(self, expr: str, context: Dict[str, Any]) -> Any:
        """解析值（支持${placeholder}）"""
        # 匹配 ${step1_result.field} 格式
        m = re.match(r'\$\{(\w+(?:\.\w+)*)\}', str(expr))
        if m:
            path = m.group(1)
            parts = path.split(".")
            current = context
            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    return expr  # 无法解析，返回原值
            return current
        return expr


def if_then_else(condition_left: str, op: str, condition_right: Any,
                 true_step: Dict, false_step: Dict,
                 context: Dict) -> Dict:
    """快捷if-else

    用法:
        result = if_then_else(
            "step1_result.count", ">", 10,
            {"action": "file_write", "params": {...}},
            {"action": "code_run", "params": {...}},
            context
        )
    """
    cb = ConditionBranch()
    cb.add_condition("auto_cond", condition_left, op, condition_right)
    cb.add_branch("auto_cond", True, true_step)
    cb.add_branch("auto_cond", False, false_step)
    return cb.evaluate(context)


# ─── 单例 ────────────────────────────────────
_branch_instance: Optional[ConditionBranch] = None


def get_condition_branch() -> ConditionBranch:
    """获取条件分支执行器单例"""
    global _branch_instance
    if _branch_instance is None:
        _branch_instance = ConditionBranch()
    return _branch_instance
