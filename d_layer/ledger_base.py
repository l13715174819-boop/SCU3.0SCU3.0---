# -*- coding: utf-8 -*-
"""
D 层：d_layer/ledger_base.py — 账本类结构定义（只读代码）
==========================================================
D 层只定义 EntropyLedger 的类结构和方法签名。
实例化后的账本（余额、历史、哈希链）属于 W1 层运行时状态。
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass


class LedgerBase(ABC):
    """账本抽象基类（D 层定义，W1 层实现）

    D 层只定义"账本应该有什么方法"，不包含任何运行时状态。
    具体的 _balance、_history、_hash_chain 在 W1 层的子类中实现。
    """

    @abstractmethod
    def pay_tax(self, operation: str, layer: str, reason: str = "",
                custom_factor: float = 1.0, op_id: str = "",
                pattern_key: str = "") -> Tuple[bool, str, Dict[str, Any]]:
        """支付熵税（A2 公理）"""
        pass

    @abstractmethod
    def refund(self, amount: float, reason: str = "", op_id: str = ""):
        """退款补偿（业务失败时反向记账）"""
        pass

    @abstractmethod
    def replenish(self, amount: float, auth_token: str = "",
                  reason: str = "") -> Tuple[bool, str]:
        """充值（独立通道，免 A2 审计，需鉴权）"""
        pass

    @abstractmethod
    def balance(self) -> float:
        """查询余额"""
        pass

    @abstractmethod
    def stats(self) -> Dict[str, Any]:
        """查询统计"""
        pass

    @abstractmethod
    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """查询历史"""
        pass

    @abstractmethod
    def set_tax_factor_override(self, pattern_key: str, factor: float,
                                expiry_hours: float = 24.0,
                                source: str = "daily_audit"):
        """设置税率覆写（W1 层运行时状态）"""
        pass

    @abstractmethod
    def get_tax_factor(self, pattern_key: str) -> float:
        """查询税率覆写"""
        pass

    @abstractmethod
    def record_feedback(self, pattern_key: str, user_id: str, kind: str) -> Dict[str, Any]:
        """记录反馈（实时聚合）"""
        pass

    @abstractmethod
    def get_feedback_aggregate(self, pattern_key: str) -> Dict[str, Any]:
        """获取反馈聚合"""
        pass
