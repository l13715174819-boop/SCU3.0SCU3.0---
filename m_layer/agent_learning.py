# -*- coding: utf-8 -*-
"""
m_layer/agent_learning.py — Agent执行经验沉淀（M层）
=====================================================
阶段4第三批：从执行历史中学习，积累经验供下次复用

能力对标：AI助手"完成任务后积累经验，下次遇到类似任务更高效"

功能:
  1. 从TaskExecutor历史中提取成功/失败模式
  2. 按目标类型分类积累经验
  3. 高效工具组合识别（什么任务用什么工具链最快）
  4. 失败模式记录（避免重复犯错）
  5. 经验沉淀到RAG知识库 + 本地状态文件

架构归属：M层（元认知层的学习进化）
依赖：w1_layer/knowledge_store, w1_layer/temp_manager持久化
"""
import os
import json
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("scu2.m.agent_learn")


class AgentLearningEngine:
    """Agent执行经验学习引擎

    用法:
        engine = AgentLearningEngine()
        # 从执行器历史中学习
        report = engine.learn_from_history(executor.get_history())
        # 查询相似任务的经验
        exp = engine.query_experience("分析文件词频")
    """

    STATE_FILE = "agent_learning.json"

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, "scu2_data")
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._state_path = os.path.join(data_dir, self.STATE_FILE)

        self._lock = threading.Lock()
        # 经验库：goal_pattern → {success_count, fail_count, best_strategy, avg_time}
        self._experiences: Dict[str, Dict] = {}
        # 工具组合统计：tool_sequence → {count, success_rate, avg_time}
        self._tool_combos: Dict[str, Dict] = {}
        # 失败模式：error_pattern → {count, last_seen, context}
        self._failure_patterns: Dict[str, Dict] = {}

        self._load_state()

    def learn_from_history(self, history: List[Dict]) -> Dict[str, Any]:
        """从执行历史中学习

        Args:
            history: TaskExecutor.get_history() 返回的历史列表

        Returns:
            学习报告
        """
        report = {
            "learned_at": datetime.now().isoformat(),
            "records_processed": 0,
            "new_experiences": 0,
            "new_combos": 0,
            "new_failures": 0,
            "errors": [],
        }

        with self._lock:
            for record in history:
                try:
                    goal = record.get("goal", "")
                    if not goal:
                        continue

                    # 提取目标模式（简化：取关键词）
                    goal_pattern = self._extract_pattern(goal)
                    success = record.get("success", False)
                    elapsed = record.get("elapsed_ms", 0)

                    # 更新经验库
                    if goal_pattern not in self._experiences:
                        self._experiences[goal_pattern] = {
                            "pattern": goal_pattern,
                            "sample_goal": goal,
                            "success_count": 0,
                            "fail_count": 0,
                            "total_time": 0,
                            "best_strategy": None,
                            "last_seen": datetime.now().isoformat(),
                        }
                        report["new_experiences"] += 1

                    exp = self._experiences[goal_pattern]
                    if success:
                        exp["success_count"] += 1
                    else:
                        exp["fail_count"] += 1
                    exp["total_time"] += elapsed
                    exp["last_seen"] = datetime.now().isoformat()
                    exp["avg_time"] = exp["total_time"] / (exp["success_count"] + exp["fail_count"])

                    # 更新工具组合统计
                    steps_total = record.get("steps_total", 0)
                    steps_done = record.get("steps_done", 0)
                    combo_key = f"{steps_total}步_{steps_done}成"
                    if combo_key not in self._tool_combos:
                        self._tool_combos[combo_key] = {
                            "count": 0,
                            "success_count": 0,
                            "total_time": 0,
                        }
                        report["new_combos"] += 1
                    combo = self._tool_combos[combo_key]
                    combo["count"] += 1
                    if success:
                        combo["success_count"] += 1
                    combo["total_time"] += elapsed
                    combo["success_rate"] = combo["success_count"] / combo["count"]
                    combo["avg_time"] = combo["total_time"] / combo["count"]

                    # 记录失败模式
                    if not success:
                        failure_key = goal_pattern
                        if failure_key not in self._failure_patterns:
                            self._failure_patterns[failure_key] = {
                                "count": 0,
                                "last_seen": datetime.now().isoformat(),
                                "sample_goal": goal,
                            }
                            report["new_failures"] += 1
                        self._failure_patterns[failure_key]["count"] += 1
                        self._failure_patterns[failure_key]["last_seen"] = datetime.now().isoformat()

                    report["records_processed"] += 1

                except Exception as e:
                    report["errors"].append(str(e))

            self._save_state()

        # 沉淀到RAG
        self._sink_to_rag()

        logger.info(f"Agent学习完成: 处理{report['records_processed']}条记录, "
                    f"新增{report['new_experiences']}个经验, "
                    f"{report['new_combos']}个组合, "
                    f"{report['new_failures']}个失败模式")
        return report

    def query_experience(self, goal: str) -> Dict[str, Any]:
        """查询类似任务的经验

        Args:
            goal: 当前任务目标

        Returns:
            {
                "has_experience": bool,
                "similar_pattern": str,
                "success_rate": float,
                "avg_time": float,
                "suggested_strategy": str,
                "known_failures": [...],
            }
        """
        pattern = self._extract_pattern(goal)

        with self._lock:
            # 精确匹配
            if pattern in self._experiences:
                exp = self._experiences[pattern]
                total = exp["success_count"] + exp["fail_count"]
                return {
                    "has_experience": True,
                    "similar_pattern": pattern,
                    "success_rate": exp["success_count"] / total if total > 0 else 0,
                    "avg_time": exp.get("avg_time", 0),
                    "total_runs": total,
                    "known_failures": self._failure_patterns.get(pattern, {}).get("count", 0),
                }

            # 模糊匹配（包含关键词）
            for p, exp in self._experiences.items():
                if any(kw in goal for kw in p.split("_")):
                    total = exp["success_count"] + exp["fail_count"]
                    return {
                        "has_experience": True,
                        "similar_pattern": p,
                        "success_rate": exp["success_count"] / total if total > 0 else 0,
                        "avg_time": exp.get("avg_time", 0),
                        "total_runs": total,
                        "known_failures": self._failure_patterns.get(p, {}).get("count", 0),
                    }

        return {
            "has_experience": False,
            "similar_pattern": None,
            "success_rate": 0,
            "avg_time": 0,
            "total_runs": 0,
            "known_failures": 0,
        }

    def get_stats(self) -> Dict[str, Any]:
        """获取学习统计"""
        with self._lock:
            return {
                "total_experiences": len(self._experiences),
                "total_combos": len(self._tool_combos),
                "total_failures": len(self._failure_patterns),
                "top_strategies": sorted(
                    [{"pattern": e["pattern"], "runs": e["success_count"] + e["fail_count"],
                      "success_rate": e["success_count"] / max(1, e["success_count"] + e["fail_count"])}
                     for e in self._experiences.values()],
                    key=lambda x: x["runs"], reverse=True
                )[:10],
            }

    def _extract_pattern(self, goal: str) -> str:
        """从目标中提取模式关键词"""
        # 简化：取前3个有意义词
        import re
        words = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', goal)
        keywords = [w for w in words if len(w) >= 2][:3]
        return "_".join(keywords) if keywords else goal[:20]

    def _sink_to_rag(self) -> None:
        """沉淀经验到RAG知识库"""
        try:
            from w1_layer.knowledge_store import get_store
            store = get_store()

            doc = "[Agent学习经验汇总]\n"
            doc += f"更新时间: {datetime.now().isoformat()}\n"
            doc += f"经验数: {len(self._experiences)}\n\n"

            for pattern, exp in list(self._experiences.items())[:20]:
                total = exp["success_count"] + exp["fail_count"]
                rate = exp["success_count"] / total if total > 0 else 0
                doc += f"模式: {pattern}\n"
                doc += f"  示例: {exp['sample_goal']}\n"
                doc += f"  执行{total}次, 成功率{rate:.0%}, 平均{exp.get('avg_time', 0):.0f}ms\n\n"

            store.add_document(doc, metadata={
                "source": "agent_learning",
                "type": "execution_experience",
            })
        except Exception as e:
            logger.warning(f"沉淀到RAG失败: {e}")

    def _load_state(self) -> None:
        """加载持久化状态"""
        if os.path.exists(self._state_path):
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    state = json.loads(f.read())
                self._experiences = state.get("experiences", {})
                self._tool_combos = state.get("tool_combos", {})
                self._failure_patterns = state.get("failure_patterns", {})
                logger.info(f"加载Agent学习状态: {len(self._experiences)}个经验")
            except Exception as e:
                logger.warning(f"加载学习状态失败: {e}")

    def _save_state(self) -> None:
        """持久化状态"""
        try:
            state = {
                "experiences": self._experiences,
                "tool_combos": self._tool_combos,
                "failure_patterns": self._failure_patterns,
                "saved_at": datetime.now().isoformat(),
            }
            with open(self._state_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存学习状态失败: {e}")


# ─── 单例 ────────────────────────────────────
_agent_learn_instance: Optional[AgentLearningEngine] = None


def get_agent_learning() -> AgentLearningEngine:
    """获取Agent学习引擎单例"""
    global _agent_learn_instance
    if _agent_learn_instance is None:
        _agent_learn_instance = AgentLearningEngine()
    return _agent_learn_instance
