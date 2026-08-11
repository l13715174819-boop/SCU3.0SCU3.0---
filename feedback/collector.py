# -*- coding: utf-8 -*-
"""
feedback/collector.py — 反馈收集器（修复 WARN #6）
====================================================
独立中间层：user_id 去重 + 限频 + 实时聚合
账本只负责最终记录，不直接处理用户反馈。
"""
import threading
import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger("scu2.feedback")


class FeedbackCollector:
    """反馈收集器"""

    def __init__(self, ledger=None):
        self.ledger = ledger
        self._lock = threading.Lock()
        self._rate_limit: Dict[str, Dict[str, float]] = {}
        self.rate_limit_seconds = 60.0  # 单用户单 pattern 限频

    def collect(self, user_id: str, pattern_key: str, kind: str) -> Dict[str, Any]:
        """收集反馈（去重 + 限频）"""
        if kind not in ("up", "down"):
            return {"error": f"非法反馈类型: {kind}"}
        if not pattern_key:
            return {"error": "pattern_key 不能为空"}
        if not user_id:
            return {"error": "user_id 不能为空"}

        # 限频
        now = datetime.now().timestamp()
        with self._lock:
            if user_id not in self._rate_limit:
                self._rate_limit[user_id] = {}
            last_ts = self._rate_limit[user_id].get(pattern_key, 0)
            if now - last_ts < self.rate_limit_seconds:
                remaining = int(self.rate_limit_seconds - (now - last_ts))
                return {"error": f"限频: 请 {remaining}s 后再操作", "rate_limited": True}
            self._rate_limit[user_id][pattern_key] = now

        # 委托账本记录（账本内部做 user_id 去重）
        if self.ledger:
            result = self.ledger.record_feedback(pattern_key, user_id, kind)
            agg = self.ledger.get_feedback_aggregate(pattern_key)
            return {
                "kind": kind, "pattern_key": pattern_key,
                "deduplicated": result.get("deduplicated", False),
                "aggregate": agg,
                "message": self._build_message(kind, agg),
            }
        return {"error": "账本未初始化"}

    def _build_message(self, kind: str, agg: Dict[str, Any]) -> str:
        net = agg.get("net", 0)
        factor = agg.get("suggested_factor", 1.0)
        if kind == "up":
            if factor < 1.0:
                return f"已点赞（净 +{net}），建议税率 x{factor}（待周期审计生效）"
            return f"已点赞（净 +{net}），税率暂无调整"
        else:
            if factor > 1.0:
                return f"已点踩（净 {net}），建议税率 x{factor}（待周期审计生效）"
            return f"已点踩（净 {net}），税率暂无调整"

    def get_aggregate(self, pattern_key: str) -> Dict[str, Any]:
        if self.ledger:
            return self.ledger.get_feedback_aggregate(pattern_key)
        return {}
