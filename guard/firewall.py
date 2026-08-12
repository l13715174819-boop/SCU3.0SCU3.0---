# -*- coding: utf-8 -*-
"""
guard/firewall.py — CUF 守卫层（横切·跨层审计）
================================================
v3 核心修复：
  - A4 只管依赖方向（D←M←W1←W2），不管数据流方向
  - 守卫审计 W1 账本 = 同层操作 = 免审（无死循环）
  - 跨层数据流（W2→W1, W1→M）需经守卫扣税

守卫层不是 CUF 四层之一，而是横切在数据流管道上的检查器。
"""
import os
import sys
import logging
from typing import Tuple, Dict, Any, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from d_layer.axioms import (
    Axiom, Contract, VALID_LAYERS, LAYER_ORDER,
    normalize_layer, A4_WHITELIST_ACTIONS, HIGH_RISK_ACTIONS,
    READONLY_ACTIONS, Operation,
)
# 原则一落地：集成D层完整性校验
from guard.d_layer_integrity import get_checker as get_d_checker

logger = logging.getLogger("SCU3.guard.firewall")

# A4 校验范围：只管依赖类动作，不管数据流动作（原则二落地）
DEPENDENCY_ACTIONS = {"import", "modify", "patch", "base_modify", "delete"}
DATAFLOW_ACTIONS = {"query", "tool_call", "check", "inspect", "layer_jump",
                    "read", "write", "submit_stimulus", "cognitive_cycle"}


class CUFGuard:
    """CUF 守卫层 — 跨层审计门禁

    只在"跨 CUF 层"时触发：
      - W2→W1（感知→记忆）
      - W1→M（执行→认知）
    同层流动（W1→W1, M→M）免审。
    """

    def __init__(self, ledger=None, whitelist=None):
        self.ledger = ledger          # W1 层账本实例
        self.whitelist = whitelist    # 白名单管理器
        self._pending_refunds: Dict[str, float] = {}
        self._d_checker = get_d_checker()  # D层完整性校验器

    def check(self, op: Operation) -> Tuple[bool, str, Dict[str, Any]]:
        """执行跨层审计"""
        details = {"axioms_checked": [], "op_id": op.op_id}

        # 前置：层标识符校验
        src = normalize_layer(op.source)
        tgt = normalize_layer(op.target)
        if src not in VALID_LAYERS:
            return False, f"非法 source 层: {op.source}", details
        if tgt not in VALID_LAYERS:
            return False, f"非法 target 层: {op.target}", details
        op.source, op.target = src, tgt

        # 同层免审（v3 核心：数据流同层不触发守卫）
        # 原则三落地：记录免审日志，便于审计追溯
        if src == tgt:
            details["axioms_checked"].append(
                {"axiom": "same_layer", "passed": True,
                 "msg": f"同层免审 ({src}→{tgt})", "same_layer_bypass": True})
            logger.debug(f"同层免审: {src}→{tgt} action={op.action} op_id={op.op_id}")
            return True, "同层免审", details

        # 白名单短路：只读操作已归档（P0修复：带code_hash校验）
        if self.whitelist and op.action in READONLY_ACTIONS:
            pk = f"{op.action}:{src}>{tgt}"
            code_hash = op.metadata.get("code_hash", "")
            if self.whitelist.contains(pk, code_hash=code_hash):
                details["axioms_checked"].append(
                    {"axiom": "whitelist", "passed": True, "msg": "白名单免审"})
                return True, "白名单免审免税", details

        # A1: 基线不可变性
        ok, msg = self._check_a1(op)
        details["axioms_checked"].append({"axiom": "A1", "passed": ok, "msg": msg})
        if not ok:
            return False, msg, details

        # A4: 层级单向性（只管依赖方向，不管数据流）
        ok, msg = self._check_a4(op)
        details["axioms_checked"].append({"axiom": "A4", "passed": ok, "msg": msg})
        if not ok:
            return False, msg, details

        # A3: 契约闭环性（高危动作）
        if op.action in HIGH_RISK_ACTIONS:
            ok, msg = self._check_a3(op)
            details["axioms_checked"].append({"axiom": "A3", "passed": ok, "msg": msg})
            if not ok:
                return False, msg, details

        # A2: 熵税经济性
        ok, msg, a2_detail = self._check_a2(op)
        details["axioms_checked"].append(
            {"axiom": "A2", "passed": ok, "msg": msg, "detail": a2_detail})
        if not ok:
            return False, msg, details

        return True, "全部公理通过", details

    def _check_a1(self, op: Operation) -> Tuple[bool, str]:
        """A1: D层不可变性（原则一落地）

        v3强化：集成D层完整性校验器
        - 拒绝任何对D层的写操作（modify/write/patch/base_modify/delete）
        - 校验D层文件未被篡改
        """
        # 1. 拒绝写D层
        ok, msg = self._d_checker.check_a1_violation(op.target, op.action, op.file_path)
        if not ok:
            return False, msg
        # 2. 实时校验D层完整性（首次每小时校验）
        ok_int, msg_int, _ = self._d_checker.verify_integrity()
        if not ok_int:
            return False, f"A1 违规: D层完整性校验失败 - {msg_int}"
        return True, "A1 通过"

    def _check_a2(self, op: Operation) -> Tuple[bool, str, Dict[str, Any]]:
        """A2: 跨层操作支付熵税

        v3 修复：守卫调用 W1 账本 = 同层操作（守卫属 W1），
        账本 pay_tax 本身不触发守卫审计，无死循环。
        """
        if not self.ledger:
            return True, "A2 跳过（无账本）", {}
        pattern_key = op.pattern_key or f"{op.action}:{op.source}>{op.target}"
        ok, msg, detail = self.ledger.pay_tax(
            operation=op.action, layer=op.source,
            reason=f"{op.source}->{op.target}:{op.action}",
            custom_factor=op.tax_custom_factor,
            op_id=op.op_id, pattern_key=pattern_key,
        )
        if not ok:
            return False, f"A2 违规: {msg}", detail
        # 登记待补偿
        tax = detail.get("tax", 0)
        if tax > 0 and op.op_id:
            self._pending_refunds[op.op_id] = tax
        return True, f"A2 通过: {msg}", detail

    def _check_a3(self, op: Operation) -> Tuple[bool, str]:
        """A3: 高危操作需四契约"""
        contracts = op.metadata.get("contracts", {})
        if not isinstance(contracts, dict) or not contracts:
            return False, "A3 违规: 契约缺失"
        for c in Contract:
            if c.value not in contracts:
                return False, f"A3 违规: 缺少契约 {c.value}"
        return True, "A3 通过: 四契约完整"

    def _check_a4(self, op: Operation) -> Tuple[bool, str]:
        """A4: 层级单向性（原则二落地：只管依赖方向 D←M←W1←W2）

        v3 修复：A4 只校验"依赖方向反向"（如 D import W2），
        不校验"数据流方向"（如 W2→D 是正常数据流）。
        跨层数据流由 A2 扣税，不由 A4 拦截。

        使用统一的 DEPENDENCY_ACTIONS 集合，避免硬编码字符串。
        """
        # 数据流类动作不受 A4 限制（原则二核心）
        if op.action not in DEPENDENCY_ACTIONS:
            return True, f"A4 跳过（数据流动作 {op.action} 不受 A4 约束）"

        src_order = LAYER_ORDER.get(op.source, 0)
        tgt_order = LAYER_ORDER.get(op.target, 0)
        # 依赖方向反向：底层试图依赖顶层 → 违规
        if src_order < tgt_order:
            if op.action not in A4_WHITELIST_ACTIONS:
                return False, (
                    f"A4 违规: {op.source}→{op.target} 依赖方向反向 "
                    f"(动作 '{op.action}' 不在白名单 {A4_WHITELIST_ACTIONS})"
                )
            return True, f"A4 通过: 白名单动作 '{op.action}'"
        return True, f"A4 通过: 依赖方向正向 ({op.source}→{op.target})"

    # ─── 补偿机制 ────────────────────────────────

    def refund_on_failure(self, op_id: str, reason: str = "") -> float:
        """业务失败时退款"""
        tax = self._pending_refunds.pop(op_id, 0.0)
        if tax > 0 and self.ledger:
            self.ledger.refund(tax, reason=f"业务失败补偿: {reason}", op_id=op_id)
            logger.info(f"补偿退款 {tax:.4f}E (op_id={op_id})")
        return tax
