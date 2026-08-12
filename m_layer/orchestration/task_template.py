# -*- coding: utf-8 -*-
"""
m_layer/task_template.py — 任务模板积累（M层）
===============================================
阶段4第三批：积累成功任务的执行模板，相似目标可复用

能力对标：AI助手"做过类似任务后形成模板，下次直接套用"

功能:
  1. 成功任务自动沉淀为模板
  2. 按目标类型分类存储
  3. 相似目标匹配推荐模板
  4. 模板评分（使用次数、成功率）

架构归属：M层（学习进化层）
"""
import os
import json
import logging
import threading
import re
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("SCU3.m.template")


class TaskTemplateManager:
    """任务模板管理器

    用法:
        mgr = TaskTemplateManager()
        # 从成功任务保存模板
        mgr.save_template(goal, plan, execution_report)
        # 查找匹配模板
        match = mgr.find_template("分析文件词频")
        if match:
            plan = match["plan"]  # 复用模板
    """

    STATE_FILE = "task_templates.json"

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, "SCU3_data")
        self._data_dir = data_dir
        self._state_path = os.path.join(data_dir, self.STATE_FILE)

        self._lock = threading.Lock()
        # template_id → {goal_pattern, sample_goal, plan, success_count, use_count, created_at}
        self._templates: Dict[str, Dict] = {}

        self._load_state()

    def save_template(self, goal: str, plan: Dict[str, Any],
                      execution_report: Dict[str, Any]) -> Optional[str]:
        """保存成功任务为模板

        Args:
            goal: 原始目标
            plan: 执行计划
            execution_report: 执行报告

        Returns:
            template_id 或 None
        """
        if not execution_report.get("success"):
            return None

        pattern = self._extract_pattern(goal)
        template_id = f"tpl_{pattern}_{datetime.now().strftime('%m%d%H%M')}"

        with self._lock:
            # 检查是否已有相似模板
            existing = self._find_similar(pattern)
            if existing:
                # 更新已有模板
                existing["success_count"] += 1
                existing["last_used"] = datetime.now().isoformat()
                existing["avg_elapsed"] = (
                    (existing.get("avg_elapsed", 0) * (existing["success_count"] - 1) +
                     execution_report.get("elapsed_ms", 0)) / existing["success_count"]
                )
                self._save_state()
                logger.info(f"更新模板: {existing['template_id']}")
                return existing["template_id"]

            # 新建模板
            template = {
                "template_id": template_id,
                "goal_pattern": pattern,
                "sample_goal": goal,
                "plan": {
                    "goal": plan.get("goal", goal),
                    "steps": plan.get("steps", []),
                    "cleanup_needed": plan.get("cleanup_needed", False),
                },
                "success_count": 1,
                "use_count": 0,
                "created_at": datetime.now().isoformat(),
                "last_used": datetime.now().isoformat(),
                "avg_elapsed": execution_report.get("elapsed_ms", 0),
            }
            self._templates[template_id] = template
            self._save_state()

        logger.info(f"保存模板: {template_id} (pattern={pattern})")
        return template_id

    def find_template(self, goal: str) -> Optional[Dict[str, Any]]:
        """查找匹配的模板

        Args:
            goal: 当前目标

        Returns:
            匹配的模板 或 None
        """
        pattern = self._extract_pattern(goal)

        with self._lock:
            # 精确匹配
            for tpl in self._templates.values():
                if tpl["goal_pattern"] == pattern:
                    return tpl

            # 模糊匹配
            best_match = None
            best_score = 0
            for tpl in self._templates.values():
                score = self._similarity(goal, tpl["sample_goal"])
                if score > best_score:
                    best_score = score
                    best_match = tpl

            # 相似度>0.3才返回
            if best_match and best_score > 0.3:
                return best_match

        return None

    def use_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """使用模板（增加使用计数）"""
        with self._lock:
            tpl = self._templates.get(template_id)
            if tpl:
                tpl["use_count"] += 1
                tpl["last_used"] = datetime.now().isoformat()
                self._save_state()
                return dict(tpl["plan"])
        return None

    def list_templates(self, limit: int = 20) -> List[Dict]:
        """列出所有模板"""
        with self._lock:
            sorted_tpls = sorted(
                self._templates.values(),
                key=lambda t: (t["use_count"], t["success_count"]),
                reverse=True
            )
            return [
                {
                    "template_id": t["template_id"],
                    "goal_pattern": t["goal_pattern"],
                    "sample_goal": t["sample_goal"],
                    "success_count": t["success_count"],
                    "use_count": t["use_count"],
                    "steps_count": len(t["plan"].get("steps", [])),
                    "avg_elapsed": t.get("avg_elapsed", 0),
                    "created_at": t["created_at"],
                }
                for t in sorted_tpls[:limit]
            ]

    def delete_template(self, template_id: str) -> bool:
        """删除模板"""
        with self._lock:
            if template_id in self._templates:
                del self._templates[template_id]
                self._save_state()
                return True
            return False

    def get_stats(self) -> Dict[str, Any]:
        """获取模板统计"""
        with self._lock:
            return {
                "total_templates": len(self._templates),
                "total_uses": sum(t["use_count"] for t in self._templates.values()),
                "most_used": max(self._templates.values(), key=lambda t: t["use_count"])
                             if self._templates else None,
            }

    def _find_similar(self, pattern: str) -> Optional[Dict]:
        """查找相似模式的模板"""
        for tpl in self._templates.values():
            if tpl["goal_pattern"] == pattern:
                return tpl
        return None

    def _similarity(self, s1: str, s2: str) -> float:
        """计算相似度（Jaccard）"""
        words1 = set(re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', s1.lower()))
        words2 = set(re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', s2.lower()))
        if not words1 or not words2:
            return 0
        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union)

    def _extract_pattern(self, goal: str) -> str:
        """提取目标模式"""
        words = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', goal)
        keywords = [w for w in words if len(w) >= 2][:3]
        return "_".join(keywords) if keywords else goal[:20]

    def _load_state(self) -> None:
        if os.path.exists(self._state_path):
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    self._templates = json.loads(f.read()).get("templates", {})
                logger.info(f"加载任务模板: {len(self._templates)}个")
            except Exception as e:
                logger.warning(f"加载模板失败: {e}")

    def _save_state(self) -> None:
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"templates": self._templates},
                                   ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存模板失败: {e}")


# ─── 单例 ────────────────────────────────────
_template_instance: Optional[TaskTemplateManager] = None


def get_template_manager() -> TaskTemplateManager:
    """获取模板管理器单例"""
    global _template_instance
    if _template_instance is None:
        _template_instance = TaskTemplateManager()
    return _template_instance
