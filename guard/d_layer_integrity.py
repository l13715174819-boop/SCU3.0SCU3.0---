# -*- coding: utf-8 -*-
"""
guard/d_layer_integrity.py — D层完整性校验
============================================
原则一落地：D层只放代码定义。
- 启动时校验D层文件哈希
- 每小时定期校验
- A1校验拒绝任何写D层操作
"""
import os
import sys
import json
import time
import hashlib
import logging
import threading
from typing import Dict, Tuple, List

logger = logging.getLogger("scu2.guard.d_integrity")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D_LAYER_DIR = os.path.join(BASE_DIR, "d_layer")
MANIFEST_PATH = os.path.join(D_LAYER_DIR, "MANIFEST.json")


class DLayerIntegrityChecker:
    """D层完整性校验器

    安全增强（P2修复）：
      - 基线hash从MANIFEST.expected_hashes读取（固化），而非首运计算
      - 校验失败时熔断：启动阶段拒绝启动，运行期进入只读降级模式
    """

    # 只读降级模式标记（运行期校验失败时置为True，禁止所有写操作）
    _readonly_mode = False

    def __init__(self):
        self._manifest = self._load_manifest()
        self._baseline_hashes = self._load_baseline_hashes()
        self._last_check_time = 0
        self._check_interval = 3600  # 每小时校验1次
        self._lock = threading.Lock()
        self._daemon_thread = None

    def _load_manifest(self) -> Dict:
        """加载D层清单"""
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"D层清单加载失败: {e}")
            return {}

    def _load_baseline_hashes(self) -> Dict[str, str]:
        """从MANIFEST.expected_hashes加载固化的基线hash

        P2修复：优先使用MANIFEST中固化的expected_hashes，
        若不存在则回退到首运计算（向后兼容）并发出告警。
        """
        expected = self._manifest.get("expected_hashes", {})
        if expected:
            logger.info(f"D层基线hash从MANIFEST.expected_hashes加载 ({len(expected)}个文件)")
            return expected
        # 回退：首运计算（兼容旧MANIFEST），并告警提示固化
        logger.warning("⚠️ MANIFEST缺少expected_hashes，回退到首运计算模式（建议运行 update_manifest_hashes() 固化基线）")
        return self._compute_runtime_hashes()

    def _compute_runtime_hashes(self) -> Dict[str, str]:
        """计算当前D层文件hash（用于初始化或更新基线）"""
        hashes = {}
        allowed = [f["path"] for f in self._manifest.get("allowed_files", [])]
        if not allowed:
            for fname in os.listdir(D_LAYER_DIR):
                if fname.endswith(".py"):
                    allowed.append(fname)
        for fname in allowed:
            fpath = os.path.join(D_LAYER_DIR, fname)
            if os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    hashes[fname] = hashlib.sha256(f.read()).hexdigest()
        return hashes

    def update_manifest_hashes(self) -> bool:
        """更新MANIFEST.expected_hashes为当前文件hash（仅用于合法升级后重新固化基线）

        Returns: True=更新成功
        """
        try:
            current = self._compute_runtime_hashes()
            self._manifest["expected_hashes"] = current
            self._baseline_hashes = current
            with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
                json.dump(self._manifest, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ MANIFEST.expected_hashes已更新 ({len(current)}个文件)")
            return True
        except Exception as e:
            logger.error(f"更新MANIFEST失败: {e}")
            return False

    def is_readonly_mode(self) -> bool:
        """检查是否处于只读降级模式"""
        return self._readonly_mode

    def verify_integrity(self) -> Tuple[bool, str, Dict]:
        """校验D层完整性

        Returns:
            (passed, msg, details)
        """
        details = {"checked_files": [], "missing_files": [], "tampered_files": [],
                   "extra_files": []}

        # 1. 检查清单中的文件是否存在且哈希匹配
        for fname, baseline_hash in self._baseline_hashes.items():
            fpath = os.path.join(D_LAYER_DIR, fname)
            if not os.path.exists(fpath):
                details["missing_files"].append(fname)
                logger.error(f"❌ D层文件缺失: {fname}")
                continue
            with open(fpath, "rb") as f:
                current_hash = hashlib.sha256(f.read()).hexdigest()
            if current_hash != baseline_hash:
                details["tampered_files"].append(
                    {"file": fname, "expected": baseline_hash[:16], "actual": current_hash[:16]})
                logger.error(f"❌ D层文件被篡改: {fname}")
            else:
                details["checked_files"].append(fname)

        # 2. 检查是否有清单外的.py文件（防止私自添加）
        allowed = set(f["path"] for f in self._manifest.get("allowed_files", []))
        for fname in os.listdir(D_LAYER_DIR):
            if fname.endswith(".py") and fname not in allowed and fname != "__init__.py":
                details["extra_files"].append(fname)
                logger.warning(f"⚠️ D层有未清单文件: {fname}")

        # 3. 检查D层文件是否包含禁止的运行时状态
        forbidden_patterns = [
            "self._balance", "self._history", "self._hash_chain",
            "self._tax_factor_overrides", "self._feedback_counts",
            "threading.Lock()", "threading.RLock()",
        ]
        for fname in self._baseline_hashes:
            fpath = os.path.join(D_LAYER_DIR, fname)
            if not os.path.exists(fpath):
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            for pattern in forbidden_patterns:
                if pattern in content:
                    return False, f"D层违规: {fname} 包含禁止的运行时状态 '{pattern}'", details

        # 汇总结果
        if details["missing_files"] or details["tampered_files"]:
            return False, f"D层完整性校验失败: {len(details['missing_files'])}缺失, {len(details['tampered_files'])}篡改", details

        self._last_check_time = time.time()
        return True, f"D层完整性校验通过 ({len(details['checked_files'])}个文件)", details

    def check_a1_violation(self, target: str, action: str, file_path: str = "") -> Tuple[bool, str]:
        """A1校验：拒绝任何写D层操作

        Args:
            target: 目标CUF层
            action: 动作名
            file_path: 涉及文件路径

        Returns:
            (allowed, msg)
        """
        if target == "D" and action in ("modify", "write", "patch", "base_modify", "delete"):
            return False, f"A1 违规: 禁止对D层执行写操作 (action={action}, file={file_path})"
        return True, "A1 通过"

    def start_periodic_check(self):
        """启动定期校验守护线程

        P2修复：校验失败时进入只读降级模式（_readonly_mode=True），
        而非仅记日志。只读模式下所有写操作应被拒绝。
        """
        if self._daemon_thread and self._daemon_thread.is_alive():
            return

        def _run():
            while True:
                time.sleep(self._check_interval)
                ok, msg, details = self.verify_integrity()
                if not ok:
                    # P2修复：熔断 — 进入只读降级模式
                    self._readonly_mode = True
                    logger.error(f"🚨 D层完整性告警(已进入只读降级模式): {msg}")
                    logger.error(f"   详情: {details}")
                    # 只读模式下继续监测，不退出进程（保留只读服务能力）

        self._daemon_thread = threading.Thread(target=_run, daemon=True, name="d-integrity-checker")
        self._daemon_thread.start()
        logger.info(f"D层完整性校验守护线程已启动 (间隔 {self._check_interval}s, 失败熔断=只读降级)")

    def get_status(self) -> Dict:
        """获取校验器状态"""
        return {
            "enabled": True,
            "files_monitored": len(self._baseline_hashes),
            "last_check_time": self._last_check_time,
            "check_interval": self._check_interval,
            "daemon_alive": self._daemon_thread.is_alive() if self._daemon_thread else False,
            "readonly_mode": self._readonly_mode,
            "baseline_source": "MANIFEST.expected_hashes" if self._manifest.get("expected_hashes") else "runtime_computed",
        }


# 全局单例
_checker: DLayerIntegrityChecker = None


def get_checker() -> DLayerIntegrityChecker:
    """获取D层完整性校验器单例"""
    global _checker
    if _checker is None:
        _checker = DLayerIntegrityChecker()
    return _checker


def verify_on_startup() -> Tuple[bool, str]:
    """启动时校验（入口调用）

    P2修复：校验失败时拒绝启动（熔断），而非仅记日志继续运行。
    """
    checker = get_checker()
    ok, msg, details = checker.verify_integrity()
    if ok:
        logger.info(f"✅ {msg}")
        # 启动定期校验
        checker.start_periodic_check()
    else:
        # P2修复：启动阶段熔断 — 拒绝启动
        logger.error(f"🚨🚨 D层完整性校验失败，拒绝启动（熔断）: {msg}")
        logger.error(f"   详情: {details}")
        logger.error(f"   如果是合法升级，请先运行 update_manifest_hashes() 重新固化基线后再启动")
    return ok, msg


def is_readonly_mode() -> bool:
    """检查系统是否处于只读降级模式（供其他模块调用拦截写操作）"""
    return get_checker().is_readonly_mode()
