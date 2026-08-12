# -*- coding: utf-8 -*-
"""
guard/whitelist.py — 只读白名单管理
=====================================
修复 #3：归档需四契约
修复 #8：TTL 过期 + 代码哈希校验
"""
import os
import json
import logging
import threading
from typing import Dict, Any, Tuple, List
from datetime import datetime, timedelta

logger = logging.getLogger("SCU3.guard.whitelist")
DEFAULT_TTL_HOURS = 24


class WhitelistManager:
    """白名单管理器 — 审计后归档 + TTL + 哈希校验"""

    def __init__(self, store_path: str = "SCU3_data/whitelist.json"):
        self.store_path = store_path
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                self._entries = json.load(f)
        except Exception as e:
            logger.warning(f"白名单加载失败: {e}")

    def _save(self):
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, ensure_ascii=False, indent=2)

    def add(self, action: str, source: str, target: str,
            contracts: Dict[str, Any] = None,
            code_hash: str = "",
            ttl_hours: float = DEFAULT_TTL_HOURS) -> Tuple[bool, str]:
        """归档（需四契约，修复 #3）"""
        from d_layer.axioms import Contract
        if not contracts or not isinstance(contracts, dict):
            return False, "A3 违规: 契约缺失（归档需四契约）"
        for c in Contract:
            if c.value not in contracts:
                return False, f"A3 违规: 缺少契约 {c.value}"
        pk = f"{action}:{source}>{target}"
        with self._lock:
            self._entries[pk] = {
                "action": action, "source": source, "target": target,
                "expiry": (datetime.now() + timedelta(hours=ttl_hours)).isoformat(),
                "code_hash": code_hash, "contracts": contracts,
                "added_at": datetime.now().isoformat(),
            }
            self._save()
        logger.info(f"白名单归档: {pk} (TTL={ttl_hours}h)")
        return True, f"已归档: {pk}"

    def contains(self, pattern_key: str, code_hash: str = "") -> bool:
        """检查白名单是否包含该模式

        P0修复：增加code_hash校验，防止代码变更绕过白名单。
        如果白名单条目存储了code_hash且传入的hash不匹配，则拒绝。

        Args:
            pattern_key: 模式键
            code_hash: 可选的代码哈希，用于验证白名单条目的代码一致性
        """
        with self._lock:
            entry = self._entries.get(pattern_key)
            if not entry:
                return False
            # TTL过期检查
            if datetime.now() > datetime.fromisoformat(entry.get("expiry", "")):
                del self._entries[pattern_key]
                self._save()
                return False
            # P0修复：代码哈希校验
            stored_hash = entry.get("code_hash", "")
            if stored_hash and code_hash and code_hash != stored_hash:
                logger.warning(
                    f"🚨 白名单哈希不匹配: {pattern_key} "
                    f"(expected={stored_hash[:12]}..., got={code_hash[:12]}...)"
                )
                return False
            return True

    def list_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"pattern_key": k, **v} for k, v in self._entries.items()]

    def cleanup_expired(self) -> int:
        count = 0
        with self._lock:
            now = datetime.now()
            expired = [k for k, v in self._entries.items()
                       if datetime.fromisoformat(v.get("expiry", "")) < now]
            for k in expired:
                del self._entries[k]
                count += 1
            if count > 0:
                self._save()
        return count
