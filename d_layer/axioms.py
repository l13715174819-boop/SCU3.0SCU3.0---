# -*- coding: utf-8 -*-
"""
D 层：d_layer/axioms.py — 公理与常量定义（只读·不可运行时修改）
================================================================
D 层只放代码定义，不放运行时状态。
账本实例（余额/历史/哈希链）在 W1 层，D 层只定义"账本长什么样"。
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, Set


class Axiom(str, Enum):
    """四公理"""
    A1_BASELINE = "A1"    # 基线不可变性：W2/M 禁止修改 D 层代码文件
    A2_ENTROPY = "A2"     # 熵税经济性：跨层操作必须支付熵税
    A3_CONTRACT = "A3"    # 契约闭环性：高危操作必须携带四契约
    A4_HIERARCHY = "A4"   # 层级单向性：依赖方向 D←M←W1←W2（不管数据流）


class Contract(str, Enum):
    """四契约"""
    OBSERVATION = "observation"
    SAMPLING = "sampling"
    SIGNING = "signing"
    SYNTHESIS = "synthesis"


# ═══ 层级定义 ═══
VALID_LAYERS: Set[str] = {"D", "M", "W1", "W2"}
LAYER_ORDER: Dict[str, int] = {"D": 0, "M": 1, "W1": 2, "W2": 3}
LAYER_NAMES: Dict[str, str] = {
    "D": "基线层", "M": "元认知层", "W1": "工作层1", "W2": "工作层2",
}

# ═══ A4 白名单动作（允许依赖方向反向的动作）═══
# 注意：A4 只管依赖方向（import 关系），不管数据流方向
A4_WHITELIST_ACTIONS: Set[str] = {
    "self_modify",     # 自修改（需 A3 契约）
    "tool_call",       # 工具调用
    "check",           # 检查
    "inspect",         # 审视
    "replenish",       # 充值（独立通道）
    "daily_audit",     # 周期审计（M→W1，同层免审）
}

# ═══ A3 高危动作（必须携带四契约）═══
HIGH_RISK_ACTIONS: Set[str] = {
    "self_modify", "skill_crystallize", "code_patch", "base_modify",
}

# ═══ 只读动作（可归入白名单）═══
READONLY_ACTIONS: Set[str] = {"check", "inspect", "query", "read"}

# ═══ 基础税率表（D 层常量，运行时不可修改）═══
BASE_TAX_RATES: Dict[str, float] = {
    "query": 0.5, "tool_call": 1.0, "layer_jump": 2.0,
    "check": 0.2, "inspect": 0.2, "read": 0.2,
    "write": 3.0, "modify": 5.0, "replenish": 0.0,
    "daily_audit": 0.0,
}

# ═══ 层级深度因子（D 层常量）═══
LAYER_DEPTH_FACTOR: Dict[str, float] = {
    "D": 1.5, "M": 1.2, "W1": 1.0, "W2": 0.8,
}

# ═══ 系统状态因子（D 层常量）═══
STATE_FACTORS: Dict[str, float] = {
    "stable": 1.0, "degraded": 1.5, "critical": 2.0, "emergency": 3.0,
}

# ═══ 经济常量（D 层常量）═══
INITIAL_BUDGET = 1000.0
MIN_BALANCE = 10.0           # 保底余额
MAX_SINGLE_TRANSACTION = 1000.0
MIN_TRANSACTION = 0.01
AUTO_REPLENISH_AMOUNT = 100.0
MAX_TRANSACTION_PER_SECOND = 50  # 限频


def normalize_layer(layer: str) -> str:
    """层标识符归一化"""
    if not isinstance(layer, str):
        return ""
    return layer.strip().upper()


def is_valid_layer(layer: str) -> bool:
    return normalize_layer(layer) in VALID_LAYERS


@dataclass
class Operation:
    """操作描述（跨层操作的统一描述符）"""
    source: str                              # 源 CUF 层
    target: str                              # 目标 CUF 层
    action: str                              # 动作名
    user_id: str = ""                        # 用户标识
    op_id: str = ""                          # 操作ID（用于补偿）
    file_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    tax_custom_factor: float = 1.0           # 自定义税率因子
    pattern_key: str = ""                    # 模式标识（供反馈系统用）


@dataclass
class TaxBreakdown:
    """熵税明细"""
    base_rate: float
    layer_depth: float
    state_factor: float
    custom_factor: float
    final_tax: float
    op_id: str = ""
