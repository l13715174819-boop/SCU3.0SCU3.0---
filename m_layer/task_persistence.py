# -*- coding: utf-8 -*-
"""
m_layer/task_persistence.py — 任务状态持久化（M层）
====================================================
v5.0第一批：任务中断后可恢复

功能:
  1. 保存任务执行状态到磁盘
  2. 任务崩溃/中断后可恢复
  3. 支持暂停/恢复执行
  4. 任务检查点（checkpoint）

架构归属：M层（任务管理层）
"""
import os
import json
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("SCU3.m.persist")


class TaskPersistence:
    """任务状态持久化管理器

    用法:
        persist = TaskPersistence()
        # 保存检查点
        persist.save_checkpoint(task_id, plan, step_index, step_context)
        # 恢复
        state = persist.load_checkpoint(task_id)
        # 列出可恢复的任务
        resumable = persist.list_resumable()
    """

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, "SCU3_data")
        self._data_dir = os.path.join(data_dir, "checkpoints")
        os.makedirs(self._data_dir, exist_ok=True)

        self._lock = threading.Lock()

    def save_checkpoint(self, task_id: str, plan: Dict, current_step: int,
                        step_context: Dict, status: str = "running") -> bool:
        """保存检查点

        Args:
            task_id: 任务ID
            plan: 执行计划
            current_step: 当前步骤索引
            step_context: 步骤间上下文
            status: running/paused/completed/failed
        """
        checkpoint = {
            "task_id": task_id,
            "plan": plan,
            "current_step": current_step,
            "step_context": step_context,
            "status": status,
            "saved_at": datetime.now().isoformat(),
        }

        path = os.path.join(self._data_dir, f"{task_id}.json")
        try:
            with self._lock:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(checkpoint, ensure_ascii=False, indent=2))
            logger.info(f"检查点已保存: {task_id} (step={current_step}, status={status})")
            return True
        except Exception as e:
            logger.warning(f"保存检查点失败: {e}")
            return False

    def load_checkpoint(self, task_id: str) -> Optional[Dict]:
        """加载检查点"""
        path = os.path.join(self._data_dir, f"{task_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.loads(f.read())
        except Exception as e:
            logger.warning(f"加载检查点失败: {e}")
            return None

    def list_resumable(self) -> List[Dict]:
        """列出可恢复的任务"""
        resumable = []
        if not os.path.exists(self._data_dir):
            return resumable

        for fname in os.listdir(self._data_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._data_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cp = json.loads(f.read())
                if cp.get("status") in ("running", "paused"):
                    resumable.append({
                        "task_id": cp["task_id"],
                        "current_step": cp["current_step"],
                        "status": cp["status"],
                        "saved_at": cp["saved_at"],
                        "goal": cp.get("plan", {}).get("goal", ""),
                        "total_steps": len(cp.get("plan", {}).get("steps", [])),
                    })
            except Exception:
                continue

        resumable.sort(key=lambda x: x["saved_at"], reverse=True)
        return resumable

    def delete_checkpoint(self, task_id: str) -> bool:
        """删除检查点"""
        path = os.path.join(self._data_dir, f"{task_id}.json")
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def update_status(self, task_id: str, status: str) -> bool:
        """更新任务状态"""
        cp = self.load_checkpoint(task_id)
        if not cp:
            return False
        cp["status"] = status
        cp["updated_at"] = datetime.now().isoformat()
        path = os.path.join(self._data_dir, f"{task_id}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(cp, ensure_ascii=False, indent=2))
            return True
        except Exception:
            return False


# ─── 单例 ────────────────────────────────────
_persist_instance: Optional[TaskPersistence] = None


def get_task_persistence() -> TaskPersistence:
    """获取任务持久化管理器单例"""
    global _persist_instance
    if _persist_instance is None:
        _persist_instance = TaskPersistence()
    return _persist_instance
