# -*- coding: utf-8 -*-
"""
D层：d_layer/contracts.py — 契约详细规范（只读代码定义）
==========================================================
四契约的完整定义。A3公理要求高危操作必须携带完整四契约。
"""
from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict


class Contract(str, Enum):
    """四契约（A3公理要求）"""
    OBSERVATION = "observation"   # 观察契约
    SAMPLING = "sampling"         # 采样契约
    SIGNING = "signing"           # 签名契约
    SYNTHESIS = "synthesis"       # 综合契约


@dataclass
class ContractSpec:
    """契约规范定义"""
    name: str
    description: str
    required_fields: list
    validation_rule: str


CONTRACT_SPECS: Dict[str, ContractSpec] = {
    Contract.OBSERVATION.value: ContractSpec(
        name="观察契约",
        description="记录操作的观察上下文（时间、操作者、环境）",
        required_fields=["timestamp", "observer", "context"],
        validation_rule="timestamp必须是ISO格式，observer非空"
    ),
    Contract.SAMPLING.value: ContractSpec(
        name="采样契约",
        description="记录数据采样方法与样本范围",
        required_fields=["method", "sample_size", "scope"],
        validation_rule="method非空，sample_size≥1"
    ),
    Contract.SIGNING.value: ContractSpec(
        name="签名契约",
        description="操作签名，确保不可抵赖",
        required_fields=["signature", "signer", "public_key"],
        validation_rule="signature非空，signer非空"
    ),
    Contract.SYNTHESIS.value: ContractSpec(
        name="综合契约",
        description="综合判断与决策依据",
        required_fields=["decision", "reasoning", "confidence"],
        validation_rule="decision非空，confidence∈[0,1]"
    ),
}


def validate_contracts(contracts: Dict[str, Any]) -> tuple:
    """校验四契约完整性（D层只读函数）

    Returns:
        (passed, msg)
    """
    if not isinstance(contracts, dict) or not contracts:
        return False, "契约缺失（空dict）"
    for c in Contract:
        if c.value not in contracts:
            return False, f"缺少契约: {c.value}"
        val = contracts[c.value]
        if val is None or val == "" or val == {} or val == []:
            return False, f"契约为空: {c.value}"
    return True, "四契约完整"


def validate_contract_detail(contracts: Dict[str, Any]) -> tuple:
    """校验四契约详细字段（D层只读函数）

    Returns:
        (passed, msg, details)
    """
    ok, msg = validate_contracts(contracts)
    if not ok:
        return False, msg, {}
    details = {}
    for c in Contract:
        spec = CONTRACT_SPECS[c.value]
        val = contracts[c.value]
        if not isinstance(val, dict):
            details[c.value] = f"⚠️ {c.value}不是dict结构"
            continue
        missing = [f for f in spec.required_fields if f not in val]
        if missing:
            return False, f"契约 {c.value} 缺少字段: {missing}", details
        details[c.value] = f"✓ {spec.name}字段完整"
    return True, "四契约详细校验通过", details
