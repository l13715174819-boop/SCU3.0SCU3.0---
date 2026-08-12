# -*- coding: utf-8 -*-
"""
W1 层：w1_layer/ledger_runtime.py — 账本运行时状态
===================================================
v3 核心修复：账本实例（余额/历史/哈希链）归 W1 层，不归 D 层。
守卫审计 W1 账本 = 同层操作 = 免审，消除自指死循环。

包含：
  - 余额（_balance）
  - 历史记录（_history）+ 哈希链
  - 税率覆写表（_tax_factor_overrides）
  - 反馈计数（_feedback_counts）
  - 保底余额机制（防死锁）
"""
import os
import sys
import json
import html
import hashlib
import threading
import logging
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timedelta

# 导入 D 层定义（依赖方向：W1 依赖 D，合法）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from d_layer.axioms import (
    INITIAL_BUDGET, MIN_BALANCE, MAX_SINGLE_TRANSACTION, MIN_TRANSACTION,
    AUTO_REPLENISH_AMOUNT, BASE_TAX_RATES, LAYER_DEPTH_FACTOR, STATE_FACTORS,
)
from d_layer.ledger_base import LedgerBase

logger = logging.getLogger("SCU3.w1.ledger")


class LedgerRuntime(LedgerBase):
    """账本运行时实现（W1 层）

    D 层定义接口，W1 层实现并持有运行时状态。
    守卫层（W1）审计此账本 = 同层操作 = 免审。
    """

    def __init__(self, initial_budget: float = INITIAL_BUDGET,
                 store_path: str = "SCU3_data/ledger.json"):
        self.store_path = store_path
        self._lock = threading.RLock()  # 可重入锁，支持嵌套调用
        self._balance = float(initial_budget)
        self._history: List[Dict[str, Any]] = []
        self._hash_chain: str = "genesis"
        self._total_in: float = float(initial_budget)
        self._total_out: float = 0.0
        self._system_state: str = "stable"
        # W1 层运行时状态
        self._tax_factor_overrides: Dict[str, Dict[str, Any]] = {}
        self._feedback_counts: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ─── 持久化 ────────────────────────────────────

    def _load(self):
        if not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._balance = float(data.get("balance", INITIAL_BUDGET))
            self._history = data.get("history", [])
            self._hash_chain = data.get("hash_chain", "genesis")
            self._total_in = float(data.get("total_in", self._balance))
            self._total_out = float(data.get("total_out", 0.0))
            self._system_state = data.get("system_state", "stable")
            self._tax_factor_overrides = data.get("tax_factor_overrides", {})
            fc = data.get("feedback_counts", {})
            self._feedback_counts = {
                k: {**v, "users": set(v.get("users", []))}
                for k, v in fc.items()
            }
            logger.info(f"账本加载成功: 余额 {self._balance:.2f}E")
        except Exception as e:
            logger.warning(f"账本加载失败: {e}")

    def _save(self):
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        fc_serializable = {
            k: {**v, "users": list(v.get("users", set()))}
            for k, v in self._feedback_counts.items()
        }
        data = {
            "balance": self._balance,
            "history": self._history[-500:],
            "hash_chain": self._hash_chain,
            "total_in": self._total_in,
            "total_out": self._total_out,
            "system_state": self._system_state,
            "tax_factor_overrides": self._tax_factor_overrides,
            "feedback_counts": fc_serializable,
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ─── 哈希链 ────────────────────────────────────

    def _compute_hash(self, entry: Dict[str, Any]) -> str:
        payload = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(
            (self._hash_chain + payload).encode("utf-8")
        ).hexdigest()

    def _append_entry(self, entry: Dict[str, Any]):
        """追加历史记录并更新哈希链"""
        entry["hash"] = self._compute_hash(entry)
        self._hash_chain = entry["hash"]
        self._history.append(entry)

    # ─── 保底余额（防死锁 + 限频）────────────────────────

    # 限频参数：1小时内最多补充5次，防止滥用
    _REPLENISH_MAX_PER_HOUR = 5
    _REPLENISH_WINDOW_SECONDS = 3600
    _replenish_timestamps: List[float] = []

    def _ensure_min_balance(self):
        """余额低于保底值时自动补充（独立通道，不走 A2 审计）

        P0修复：增加限频机制，1小时内最多补充5次。
        超过限频则拒绝补充，防止保底机制被滥用。
        """
        if self._balance >= MIN_BALANCE:
            return

        # 限频检查：清理1小时前的记录，检查当前窗口内的补充次数
        now = datetime.now().timestamp()
        self._replenish_timestamps = [
            ts for ts in self._replenish_timestamps
            if now - ts < self._REPLENISH_WINDOW_SECONDS
        ]
        if len(self._replenish_timestamps) >= self._REPLENISH_MAX_PER_HOUR:
            logger.error(
                f"🚨 保底补充限频: 1小时内已达上限 {self._REPLENISH_MAX_PER_HOUR} 次，拒绝补充"
            )
            return  # 拒绝补充，调用方将因余额不足而拒绝操作

        # 执行补充
        self._balance += AUTO_REPLENISH_AMOUNT
        self._total_in += AUTO_REPLENISH_AMOUNT
        self._replenish_timestamps.append(now)
        entry = {
            "type": "auto_replenish",
            "amount": AUTO_REPLENISH_AMOUNT,
            "balance_after": round(self._balance, 4),
            "reason": f"保底余额补充（低于 {MIN_BALANCE}E，第{len(self._replenish_timestamps)}/{self._REPLENISH_MAX_PER_HOUR}次）",
            "timestamp": datetime.now().isoformat(),
        }
        self._append_entry(entry)
        logger.warning(
            f"⚠️ 保底补充 {AUTO_REPLENISH_AMOUNT}E (余额 {self._balance:.2f}E, "
            f"窗口内第{len(self._replenish_timestamps)}/{self._REPLENISH_MAX_PER_HOUR}次)"
        )

    # ─── 支付熵税（A2 公理核心）────────────────────

    def pay_tax(self, operation: str = "query", layer: str = "M",
                reason: str = "", custom_factor: float = 1.0,
                op_id: str = "", pattern_key: str = "") -> Tuple[bool, str, Dict[str, Any]]:
        # 查询税率覆写（W1 层运行时状态）
        if pattern_key:
            custom_factor *= self.get_tax_factor(pattern_key)

        # 五维计算：base × depth × state × custom
        base_rate = BASE_TAX_RATES.get(operation, 1.0)
        layer_depth = LAYER_DEPTH_FACTOR.get(layer, 1.0)
        state_factor = STATE_FACTORS.get(self._system_state, 1.0)
        tax = round(base_rate * layer_depth * state_factor * custom_factor, 4)

        if tax < MIN_TRANSACTION:
            return True, "免税", {"tax": 0, "op_id": op_id, "breakdown": {
                "base": base_rate, "depth": layer_depth,
                "state": state_factor, "custom": custom_factor
            }}

        with self._lock:
            if tax > MAX_SINGLE_TRANSACTION:
                return False, f"单笔超限: {tax}", {"tax_required": tax}
            if self._balance < tax:
                self._ensure_min_balance()
                if self._balance < tax:
                    return False, f"余额不足: {self._balance:.2f} < {tax:.2f}", {
                        "tax_required": tax, "balance": self._balance
                    }
            self._balance -= tax
            self._total_out += tax
            entry = {
                "type": "tax", "amount": tax, "op_id": op_id,
                "operation": operation, "layer": layer,
                "balance_after": round(self._balance, 4),
                "reason": html.escape(str(reason))[:200],
                "timestamp": datetime.now().isoformat(),
            }
            self._append_entry(entry)
            self._save()
            return True, f"已支付 {tax}E (余额 {self._balance:.2f})", {
                "tax": tax, "op_id": op_id, "balance": self._balance,
                "breakdown": {
                    "base": base_rate, "depth": layer_depth,
                    "state": state_factor, "custom": custom_factor
                }
            }

    # ─── 退款补偿 ────────────────────────────────

    def refund(self, amount: float, reason: str = "", op_id: str = ""):
        with self._lock:
            self._balance += amount
            self._total_in += amount
            entry = {
                "type": "refund", "amount": round(amount, 4),
                "op_id": op_id,
                "balance_after": round(self._balance, 4),
                "reason": html.escape(str(reason))[:200],
                "timestamp": datetime.now().isoformat(),
            }
            self._append_entry(entry)
            self._save()

    # ─── 充值（独立通道）────────────────────────

    def replenish(self, amount: float, auth_token: str = "",
                  reason: str = "") -> Tuple[bool, str]:
        expected = os.environ.get("SCU3_LEDGER_AUTH", "")
        if expected and auth_token != expected:
            return False, "鉴权失败"
        if amount <= 0 or amount > MAX_SINGLE_TRANSACTION:
            return False, f"金额非法: {amount}"
        with self._lock:
            self._balance += amount
            self._total_in += amount
            entry = {
                "type": "replenish", "amount": round(amount, 4),
                "balance_after": round(self._balance, 4),
                "reason": html.escape(str(reason))[:200],
                "timestamp": datetime.now().isoformat(),
            }
            self._append_entry(entry)
            self._save()
            return True, f"已充值 {amount}E (余额 {self._balance:.2f})"

    # ─── 税率覆写（W1 层，周期审计写入）────────────

    def set_tax_factor_override(self, pattern_key: str, factor: float,
                                expiry_hours: float = 24.0,
                                source: str = "daily_audit"):
        with self._lock:
            self._tax_factor_overrides[pattern_key] = {
                "factor": float(factor),
                "expiry": (datetime.now() + timedelta(hours=expiry_hours)).isoformat(),
                "source": source,
                "set_at": datetime.now().isoformat(),
            }
            self._save()

    def get_tax_factor(self, pattern_key: str) -> float:
        if not pattern_key:
            return 1.0
        with self._lock:
            entry = self._tax_factor_overrides.get(pattern_key)
            if not entry:
                return 1.0
            expiry = datetime.fromisoformat(entry.get("expiry", ""))
            if datetime.now() > expiry:
                del self._tax_factor_overrides[pattern_key]
                self._save()
                return 1.0
            return float(entry.get("factor", 1.0))

    # ─── 反馈计数（实时聚合，user_id 去重）──────────

    def record_feedback(self, pattern_key: str, user_id: str, kind: str) -> Dict[str, Any]:
        with self._lock:
            if pattern_key not in self._feedback_counts:
                self._feedback_counts[pattern_key] = {"up": 0, "down": 0, "users": set()}
            entry = self._feedback_counts[pattern_key]
            if user_id and user_id in entry["users"]:
                return {"deduplicated": True, "counts": {"up": entry["up"], "down": entry["down"]}}
            if user_id:
                entry["users"].add(user_id)
            if kind == "up":
                entry["up"] += 1
            else:
                entry["down"] += 1
            self._save()
            return {"deduplicated": False, "counts": {"up": entry["up"], "down": entry["down"]}}

    def get_feedback_aggregate(self, pattern_key: str) -> Dict[str, Any]:
        with self._lock:
            entry = self._feedback_counts.get(pattern_key, {"up": 0, "down": 0, "users": set()})
            up, down = entry["up"], entry["down"]
            net = up - down
            if net > 0:
                factor = max(0.7, 1.0 - 0.05 * min(net, 6))
            elif net < 0:
                factor = min(1.5, 1.0 + 0.05 * min(-net, 10))
            else:
                factor = 1.0
            return {
                "pattern_key": pattern_key, "up": up, "down": down, "net": net,
                "suggested_factor": round(factor, 4),
                "user_count": len(entry.get("users", set())),
            }

    def get_all_feedback_patterns(self) -> List[str]:
        with self._lock:
            return list(self._feedback_counts.keys())

    # ─── 系统状态 ────────────────────────────────

    def set_system_state(self, state: str):
        with self._lock:
            if state in STATE_FACTORS:
                old = self._system_state
                self._system_state = state
                logger.info(f"系统状态: {old} → {state}")

    # ─── 查询 ────────────────────────────────────

    def balance(self) -> float:
        with self._lock:
            return self._balance

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "balance": round(self._balance, 4),
                "total_in": round(self._total_in, 4),
                "total_out": round(self._total_out, 4),
                "history_count": len(self._history),
                "overrides_count": len(self._tax_factor_overrides),
                "feedback_patterns": len(self._feedback_counts),
                "system_state": self._system_state,
            }

    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._history[-limit:])
