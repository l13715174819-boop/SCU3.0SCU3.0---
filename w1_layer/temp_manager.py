# -*- coding: utf-8 -*-
"""
w1_layer/temp_manager.py — 临时资源生命周期管理（W1层）
========================================================
阶段4第一批：Agent执行任务时产生的临时文件/目录，用完自动删除

能力对标：AI助手"写脚本→执行→清理临时文件"的资源管理环节

功能：
  1. 注册临时文件/目录（任务开始时）
  2. 自动清理（任务完成/失败时）
  3. 保留机制（标记为需保留的不删除）
  4. 清理报告（记录删除了什么）

安全：
  - 只能清理 sandbox 目录内的文件（防误删系统文件）
  - 路径校验（防目录遍历）
  - 删除前确认路径在允许范围内

架构归属：W1层（记忆/资源管理层）
"""
import os
import shutil
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("scu2.w1.temp")


class TempManager:
    """临时资源管理器

    用法:
        tm = TempManager()
        # 注册临时文件
        tm.register("task_001", "/path/to/temp_file.txt")
        tm.register("task_001", "/path/to/temp_dir/", is_dir=True)
        # 任务完成，清理
        report = tm.cleanup("task_001")
        # 或保留某些文件
        tm.preserve("task_001", "/path/to/keep.txt")
        report = tm.cleanup("task_001")
    """

    def __init__(self, sandbox_dir: Optional[str] = None):
        if sandbox_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            sandbox_dir = os.path.join(base, "scu2_data", "sandbox")
        self._sandbox_dir = os.path.abspath(sandbox_dir)
        os.makedirs(self._sandbox_dir, exist_ok=True)

        self._lock = threading.Lock()
        # task_id → {paths: [{path, is_dir, preserve}], created_at}
        self._registry: Dict[str, Dict[str, Any]] = {}
        # 清理历史
        self._history: List[Dict] = []

    def register(self, task_id: str, path: str, is_dir: bool = False) -> bool:
        """注册临时资源

        Args:
            task_id: 任务ID
            path: 文件/目录路径（相对sandbox或绝对路径）
            is_dir: 是否是目录

        Returns:
            是否注册成功
        """
        full_path = self._safe_path(path)
        if not full_path:
            logger.warning(f"拒绝注册越界路径: {path}")
            return False

        with self._lock:
            if task_id not in self._registry:
                self._registry[task_id] = {
                    "paths": [],
                    "created_at": datetime.now().isoformat(),
                }
            # 避免重复注册
            existing = {p["path"] for p in self._registry[task_id]["paths"]}
            if full_path not in existing:
                self._registry[task_id]["paths"].append({
                    "path": full_path,
                    "is_dir": is_dir,
                    "preserve": False,
                    "registered_at": datetime.now().isoformat(),
                })
                logger.info(f"注册临时资源: task={task_id}, path={full_path}")
            return True

    def preserve(self, task_id: str, path: str) -> bool:
        """标记某文件为保留（清理时不删除）"""
        full_path = self._safe_path(path)
        if not full_path:
            return False

        with self._lock:
            if task_id not in self._registry:
                return False
            for p in self._registry[task_id]["paths"]:
                if p["path"] == full_path:
                    p["preserve"] = True
                    logger.info(f"标记保留: {full_path}")
                    return True
        return False

    def cleanup(self, task_id: str, force: bool = False) -> Dict[str, Any]:
        """清理指定任务的所有临时资源

        Args:
            task_id: 任务ID
            force: 强制清理（包括标记保留的）

        Returns:
            {
                "task_id": ...,
                "deleted": [删除的文件列表],
                "preserved": [保留的文件列表],
                "errors": [错误信息],
                "deleted_count": int,
            }
        """
        report = {
            "task_id": task_id,
            "deleted": [],
            "preserved": [],
            "errors": [],
            "deleted_count": 0,
            "cleaned_at": datetime.now().isoformat(),
        }

        with self._lock:
            if task_id not in self._registry:
                report["errors"].append(f"任务 {task_id} 无注册资源")
                return report

            entries = self._registry[task_id]["paths"]

        for entry in entries:
            path = entry["path"]
            is_dir = entry["is_dir"]
            preserve = entry["preserve"] and not force

            # 安全检查：路径必须在sandbox内
            if not self._is_in_sandbox(path):
                report["errors"].append(f"拒绝清理越界路径: {path}")
                continue

            if preserve:
                report["preserved"].append(path)
                continue

            try:
                if not os.path.exists(path):
                    # 文件已不存在，跳过
                    continue
                if is_dir:
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                report["deleted"].append(path)
                report["deleted_count"] += 1
                logger.info(f"已清理: {path}")
            except Exception as e:
                report["errors"].append(f"清理失败 {path}: {e}")
                logger.warning(f"清理失败 {path}: {e}")

        # 从注册表移除
        with self._lock:
            if task_id in self._registry:
                del self._registry[task_id]
            # 记录历史
            self._history.append(report)
            if len(self._history) > 100:
                self._history.pop(0)

        logger.info(f"任务 {task_id} 清理完成: 删除{report['deleted_count']}个, "
                    f"保留{len(report['preserved'])}个, 错误{len(report['errors'])}个")
        return report

    def cleanup_all(self, force: bool = False) -> Dict[str, Any]:
        """清理所有任务的临时资源（紧急清理）"""
        with self._lock:
            task_ids = list(self._registry.keys())

        total_report = {
            "tasks_cleaned": 0,
            "total_deleted": 0,
            "total_errors": 0,
            "details": [],
        }
        for tid in task_ids:
            r = self.cleanup(tid, force=force)
            total_report["tasks_cleaned"] += 1
            total_report["total_deleted"] += r["deleted_count"]
            total_report["total_errors"] += len(r["errors"])
            total_report["details"].append(r)
        return total_report

    def list_temp_resources(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        """列出当前注册的临时资源"""
        with self._lock:
            if task_id:
                return {
                    "task_id": task_id,
                    "resources": self._registry.get(task_id, {}).get("paths", []),
                }
            return {
                "tasks": {
                    tid: {"paths": v["paths"], "created_at": v["created_at"]}
                    for tid, v in self._registry.items()
                }
            }

    def get_history(self, limit: int = 20) -> List[Dict]:
        """获取清理历史"""
        with self._lock:
            return list(reversed(self._history[-limit:]))

    def _safe_path(self, path: str) -> Optional[str]:
        """安全路径检查：确保路径在sandbox目录内"""
        if os.path.isabs(path):
            full = os.path.abspath(path)
        else:
            full = os.path.abspath(os.path.join(self._sandbox_dir, path))

        if not self._is_in_sandbox(full):
            return None
        return full

    def _is_in_sandbox(self, full_path: str) -> bool:
        """检查路径是否在sandbox目录内"""
        try:
            common = os.path.commonpath([full_path, self._sandbox_dir])
            return common == self._sandbox_dir
        except ValueError:
            return False


# ─── 单例 ────────────────────────────────────
_tm_instance: Optional[TempManager] = None


def get_temp_manager() -> TempManager:
    """获取临时资源管理器单例"""
    global _tm_instance
    if _tm_instance is None:
        _tm_instance = TempManager()
    return _tm_instance
