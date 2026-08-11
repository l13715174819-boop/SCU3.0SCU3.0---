# -*- coding: utf-8 -*-
"""
m_layer/tool_preference.py — 工具使用偏好学习（M层）
=====================================================
阶段4第三批：学习工具使用偏好，优化工具选择

能力对标：AI助手"从经验中知道什么场景用什么工具最好"

功能:
  1. 记录每个工具的使用次数、成功率、平均耗时
  2. 按场景分类工具效果
  3. 推荐最优工具（给定场景）
  4. 淘汰低效工具组合

架构归属：M层（学习进化层）
"""
import os
import json
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("scu2.m.tool_pref")


class ToolPreferenceLearner:
    """工具使用偏好学习器

    用法:
        learner = ToolPreferenceLearner()
        # 记录使用
        learner.record("calculator", success=True, elapsed_ms=50, scenario="数学计算")
        # 查询推荐
        best = learner.recommend("数学计算")
    """

    STATE_FILE = "tool_preferences.json"

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, "scu2_data")
        self._data_dir = data_dir
        self._state_path = os.path.join(data_dir, self.STATE_FILE)

        self._lock = threading.Lock()
        # tool_name → {total, success, fail, total_time, scenarios: {scenario: count}}
        self._tool_stats: Dict[str, Dict] = {}
        # scenario → {tool: score}
        self._scenario_prefs: Dict[str, Dict[str, float]] = {}

        self._load_state()

    def record(self, tool: str, success: bool, elapsed_ms: float = 0,
               scenario: str = "default") -> None:
        """记录一次工具使用"""
        with self._lock:
            if tool not in self._tool_stats:
                self._tool_stats[tool] = {
                    "total": 0, "success": 0, "fail": 0,
                    "total_time": 0, "scenarios": {},
                }
            stats = self._tool_stats[tool]
            stats["total"] += 1
            if success:
                stats["success"] += 1
            else:
                stats["fail"] += 1
            stats["total_time"] += elapsed_ms
            stats["avg_time"] = stats["total_time"] / stats["total"]
            stats["success_rate"] = stats["success"] / stats["total"]

            # 场景记录
            stats["scenarios"][scenario] = stats["scenarios"].get(scenario, 0) + 1

            # 更新场景偏好（成功率+速度加权评分）
            if scenario not in self._scenario_prefs:
                self._scenario_prefs[scenario] = {}
            # 评分 = 成功率 * 0.7 + 速度分 * 0.3
            speed_score = max(0, 1 - elapsed_ms / 5000)  # 5秒得0分
            score = (1 if success else 0) * 0.7 + speed_score * 0.3
            # 滑动平均
            prev = self._scenario_prefs[scenario].get(tool, 0)
            self._scenario_prefs[scenario][tool] = prev * 0.7 + score * 0.3

            self._save_state()

    def recommend(self, scenario: str = "default", top_k: int = 3) -> List[Dict[str, Any]]:
        """推荐最优工具

        Args:
            scenario: 场景描述
            top_k: 返回前几个

        Returns:
            [{tool, score, success_rate, avg_time}, ...]
        """
        with self._lock:
            prefs = self._scenario_prefs.get(scenario, {})

            # 如果场景无记录，用全局成功率
            if not prefs:
                ranked = sorted(
                    [{"tool": t, "score": s["success_rate"],
                      "success_rate": s["success_rate"], "avg_time": s.get("avg_time", 0)}
                     for t, s in self._tool_stats.items()],
                    key=lambda x: x["score"], reverse=True
                )
                return ranked[:top_k]

            ranked = []
            for tool, score in sorted(prefs.items(), key=lambda x: x[1], reverse=True)[:top_k]:
                stats = self._tool_stats.get(tool, {})
                ranked.append({
                    "tool": tool,
                    "score": round(score, 3),
                    "success_rate": stats.get("success_rate", 0),
                    "avg_time": stats.get("avg_time", 0),
                    "total_uses": stats.get("total", 0),
                })
            return ranked

    def get_all_stats(self) -> Dict[str, Any]:
        """获取所有工具统计"""
        with self._lock:
            return {
                "tools": dict(self._tool_stats),
                "scenarios": dict(self._scenario_prefs),
                "total_tools_tracked": len(self._tool_stats),
            }

    def get_tool_stats(self, tool: str) -> Optional[Dict]:
        """获取单个工具统计"""
        with self._lock:
            return self._tool_stats.get(tool)

    def reset(self) -> None:
        """重置偏好"""
        with self._lock:
            self._tool_stats.clear()
            self._scenario_prefs.clear()
            self._save_state()

    def _load_state(self) -> None:
        if os.path.exists(self._state_path):
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    state = json.loads(f.read())
                self._tool_stats = state.get("tool_stats", {})
                self._scenario_prefs = state.get("scenario_prefs", {})
            except Exception as e:
                logger.warning(f"加载工具偏好失败: {e}")

    def _save_state(self) -> None:
        try:
            state = {
                "tool_stats": self._tool_stats,
                "scenario_prefs": self._scenario_prefs,
                "saved_at": datetime.now().isoformat(),
            }
            with open(self._state_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存工具偏好失败: {e}")


# ─── 单例 ────────────────────────────────────
_tool_pref_instance: Optional[ToolPreferenceLearner] = None


def get_tool_preference() -> ToolPreferenceLearner:
    """获取工具偏好学习器单例"""
    global _tool_pref_instance
    if _tool_pref_instance is None:
        _tool_pref_instance = ToolPreferenceLearner()
    return _tool_pref_instance
