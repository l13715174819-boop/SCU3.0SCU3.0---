# -*- coding: utf-8 -*-
"""
m_layer/self_learning.py — 自学习闭环引擎（M层）
====================================================
阶段2：自学习优化

闭环流程：
  1. 反馈收集：用户👍/👎反馈
  2. 反馈分析：分析负面反馈模式，识别低质量回复
  3. 知识沉淀：高赞回答自动入RAG知识库
  4. 提示词优化：根据反馈调整系统提示词权重
  5. 策略调整：调整工具使用策略

触发机制：
  - 周期触发（与元认知层daily_audit联动）
  - 阈值触发（负面反馈超过阈值时立即触发）
  - 手动触发（API调用）

架构归属：M层（元认知层的扩展，同层免审）
安全约束：
  - 沉淀知识需经内容过滤
  - 提示词优化有回滚机制
  - 所有变更记录到学习日志
"""
import os
import json
import time
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger("SCU3.m.learning")


class SelfLearningEngine:
    """自学习闭环引擎

    闭环：反馈 → 分析 → 沉淀/优化 → 验证 → 应用
    """

    # 学习日志路径
    LEARNING_LOG = "learning_log.json"

    # 提示词优化阈值
    NEGATIVE_THRESHOLD = 3  # 同一pattern负面反馈≥3次触发优化
    KNOWLEDGE_SINK_THRESHOLD = 5  # 同一pattern正面反馈≥5次触发知识沉淀

    # 可调提示词维度（仅调整权重，不改写核心提示词）
    PROMPT_DIMENSIONS = {
        "conciseness": 1.0,      # 简洁度权重
        "detail_level": 1.0,     # 详细度权重
        "technical": 1.0,        # 技术性权重
        "friendly": 1.0,         # 友好度权重
    }

    def __init__(self, ledger=None, knowledge_store=None, feedback_collector=None,
                 content_filter=None, data_dir: str = ""):
        self.ledger = ledger
        self.knowledge = knowledge_store
        self.feedback = feedback_collector
        self.content_filter = content_filter
        self.data_dir = data_dir or os.path.join(os.getcwd(), "SCU3_data")
        self._lock = threading.Lock()

        # 学习状态
        self._prompt_weights = dict(self.PROMPT_DIMENSIONS)
        self._sunk_knowledge = set()  # 已沉淀的知识指纹（防重复）
        self._learning_history: List[Dict] = []
        self._last_learning_time: Optional[datetime] = None

        # 加载持久化状态
        self._load_state()

    # ─── 状态持久化 ────────────────────────────────

    def _state_path(self) -> str:
        return os.path.join(self.data_dir, "self_learning_state.json")

    def _load_state(self):
        """加载学习状态"""
        path = self._state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._prompt_weights = state.get("prompt_weights", dict(self.PROMPT_DIMENSIONS))
            self._sunk_knowledge = set(state.get("sunk_knowledge", []))
            self._learning_history = state.get("learning_history", [])[-100:]  # 保留最近100条
            logger.info(f"自学习状态加载: 权重={self._prompt_weights}, 已沉淀={len(self._sunk_knowledge)}")
        except Exception as e:
            logger.warning(f"加载学习状态失败: {e}")

    def _save_state(self):
        """保存学习状态"""
        path = self._state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            state = {
                "prompt_weights": self._prompt_weights,
                "sunk_knowledge": list(self._sunk_knowledge)[-1000:],  # 保留最近1000个指纹
                "learning_history": self._learning_history[-100:],
                "last_learning_time": self._last_learning_time.isoformat() if self._last_learning_time else None,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存学习状态失败: {e}")

    # ─── 核心：学习闭环 ────────────────────────────────

    def learn(self, force: bool = False) -> Dict[str, Any]:
        """执行一轮自学习闭环

        流程：
        1. 收集反馈数据
        2. 分析负面反馈 → 优化提示词权重
        3. 分析正面反馈 → 沉淀知识到RAG
        4. 生成学习报告
        5. 持久化状态

        Args:
            force: 强制执行（忽略周期限制）

        Returns:
            学习报告
        """
        with self._lock:
            start_time = time.time()
            report = {
                "timestamp": datetime.now().isoformat(),
                "actions": [],
                "knowledge_sunk": 0,
                "prompts_adjusted": 0,
                "errors": [],
            }

            # 获取所有反馈pattern
            if not self.ledger:
                report["errors"].append("无账本实例")
                return report

            try:
                pattern_keys = self.ledger.get_all_feedback_patterns()
            except Exception as e:
                report["errors"].append(f"获取反馈失败: {e}")
                return report

            # ─── 步骤1：分析负面反馈，优化提示词 ───
            for pk in pattern_keys:
                try:
                    agg = self.ledger.get_feedback_aggregate(pk)
                    down_count = agg.get("down", 0)
                    up_count = agg.get("up", 0)

                    # 负面反馈超阈值 → 优化提示词
                    if down_count >= self.NEGATIVE_THRESHOLD:
                        adjustment = self._optimize_prompt_for_negative(pk, down_count, up_count)
                        if adjustment:
                            report["prompts_adjusted"] += 1
                            report["actions"].append({
                                "type": "prompt_optimize",
                                "pattern": pk,
                                "detail": adjustment,
                            })

                    # 正面反馈超阈值 → 沉淀知识
                    if up_count >= self.KNOWLEDGE_SINK_THRESHOLD:
                        sunk = self._sink_knowledge_for_positive(pk, up_count, down_count)
                        if sunk:
                            report["knowledge_sunk"] += 1
                            report["actions"].append({
                                "type": "knowledge_sink",
                                "pattern": pk,
                                "detail": sunk,
                            })
                except Exception as e:
                    report["errors"].append(f"处理pattern {pk} 失败: {e}")

            # ─── 步骤2：记录学习历史 ───
            self._last_learning_time = datetime.now()
            learning_entry = {
                "time": self._last_learning_time.isoformat(),
                "patterns_analyzed": len(pattern_keys),
                "prompts_adjusted": report["prompts_adjusted"],
                "knowledge_sunk": report["knowledge_sunk"],
                "elapsed_ms": round((time.time() - start_time) * 1000, 2),
            }
            self._learning_history.append(learning_entry)

            # ─── 步骤3：持久化 ───
            self._save_state()

            report["elapsed_ms"] = learning_entry["elapsed_ms"]
            report["patterns_analyzed"] = len(pattern_keys)
            report["prompt_weights"] = dict(self._prompt_weights)

            logger.info(f"✅ 自学习完成: 分析{len(pattern_keys)}个pattern, "
                        f"优化{report['prompts_adjusted']}个提示词, "
                        f"沉淀{report['knowledge_sunk']}条知识")
            return report

    # ─── 提示词优化 ────────────────────────────────

    def _optimize_prompt_for_negative(self, pattern_key: str, down_count: int,
                                       up_count: int) -> Optional[Dict[str, Any]]:
        """根据负面反馈优化提示词权重

        策略：
        - chat:tool 负面多 → 降低技术性，提高友好度
        - chat:plain 负面多 → 提高简洁度，降低详细度
        - 通用 → 根据净反馈调整

        Returns:
            调整详情 或 None
        """
        old_weights = dict(self._prompt_weights)
        net = up_count - down_count
        severity = min(down_count / 10.0, 1.0)  # 严重度0-1

        if "tool" in pattern_key:
            # 工具回复被负面评价 → 更友好、更简洁
            self._prompt_weights["friendly"] = min(2.0, self._prompt_weights["friendly"] + 0.1 * severity)
            self._prompt_weights["conciseness"] = min(2.0, self._prompt_weights["conciseness"] + 0.05 * severity)
            self._prompt_weights["technical"] = max(0.5, self._prompt_weights["technical"] - 0.05 * severity)
        elif "plain" in pattern_key:
            # 普通对话被负面评价 → 更简洁、更友好
            self._prompt_weights["conciseness"] = min(2.0, self._prompt_weights["conciseness"] + 0.1 * severity)
            self._prompt_weights["friendly"] = min(2.0, self._prompt_weights["friendly"] + 0.05 * severity)
            self._prompt_weights["detail_level"] = max(0.5, self._prompt_weights["detail_level"] - 0.05 * severity)
        else:
            # 通用调整
            if net < 0:
                self._prompt_weights["conciseness"] = min(2.0, self._prompt_weights["conciseness"] + 0.05 * severity)
                self._prompt_weights["friendly"] = min(2.0, self._prompt_weights["friendly"] + 0.05 * severity)

        # 检测是否有实际变化
        changes = {k: round(self._prompt_weights[k] - old_weights[k], 3)
                   for k in self._prompt_weights if abs(self._prompt_weights[k] - old_weights[k]) > 0.001}
        if not changes:
            return None

        return {
            "old_weights": old_weights,
            "new_weights": dict(self._prompt_weights),
            "changes": changes,
            "trigger": f"负面反馈{down_count}次",
        }

    def get_optimized_system_prompt(self, base_prompt: str = "default") -> str:
        """获取优化后的系统提示词

        根据学习到的权重，在基础提示词后追加优化指令

        Args:
            base_prompt: 基础提示词预设名

        Returns:
            优化后的系统提示词
        """
        from m_layer.llm_client import get_client
        client = get_client()
        base = client.SYSTEM_PROMPTS.get(base_prompt, base_prompt)

        # 根据权重生成优化指令
        modifiers = []
        w = self._prompt_weights

        if w["conciseness"] > 1.2:
            modifiers.append("回答尽量简洁，避免冗余")
        elif w["conciseness"] < 0.8:
            modifiers.append("回答可以详细一些")

        if w["friendly"] > 1.2:
            modifiers.append("语气友好亲切")
        elif w["friendly"] < 0.8:
            modifiers.append("语气专业客观")

        if w["technical"] > 1.2:
            modifiers.append("偏向技术性表述")
        elif w["technical"] < 0.8:
            modifiers.append("用通俗易懂的方式解释")

        if modifiers:
            return base + "。" + "，".join(modifiers) + "。"
        return base

    # ─── 知识沉淀 ────────────────────────────────

    def _sink_knowledge_for_positive(self, pattern_key: str, up_count: int,
                                      down_count: int) -> Optional[Dict[str, Any]]:
        """正面反馈好的回答沉淀到RAG知识库

        策略：
        - 从历史对话中提取该pattern的高赞回答
        - 经内容过滤后存入知识库
        - 用指纹防重复

        Returns:
            沉淀详情 或 None
        """
        if not self.knowledge:
            return None

        # 从账本历史中找该pattern的最近成功操作
        try:
            history = self.ledger.history(limit=50)
        except Exception:
            return None

        sunk = []
        for entry in history:
            if entry.get("pattern_key") != pattern_key:
                continue
            response = entry.get("response", "")
            if not response or len(response) < 20:
                continue

            # 内容过滤（安全检查）
            if self.content_filter:
                filtered, warnings = self.content_filter.filter(response)
                if warnings:
                    continue  # 有安全警告的不沉淀
                response = filtered

            # 指纹防重复
            import hashlib
            fingerprint = hashlib.md5(response[:200].encode("utf-8")).hexdigest()
            if fingerprint in self._sunk_knowledge:
                continue

            # 沉淀到知识库
            doc_id = self.knowledge.add_document(
                response[:1000],  # 限制长度
                metadata={
                    "source": "self_learning",
                    "pattern": pattern_key,
                    "up_votes": up_count,
                    "sunk_at": datetime.now().isoformat(),
                }
            )
            if doc_id > 0:
                self._sunk_knowledge.add(fingerprint)
                sunk.append({"doc_id": doc_id, "fingerprint": fingerprint[:8]})

        if not sunk:
            return None

        return {
            "sunk_count": len(sunk),
            "documents": sunk,
            "trigger": f"正面反馈{up_count}次",
        }

    # ─── 学习报告 ────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """获取自学习状态"""
        return {
            "prompt_weights": dict(self._prompt_weights),
            "sunk_knowledge_count": len(self._sunk_knowledge),
            "learning_count": len(self._learning_history),
            "last_learning": self._last_learning_time.isoformat() if self._last_learning_time else None,
            "recent_history": self._learning_history[-5:],
            "thresholds": {
                "negative": self.NEGATIVE_THRESHOLD,
                "knowledge_sink": self.KNOWLEDGE_SINK_THRESHOLD,
            },
        }

    def get_learning_history(self, limit: int = 20) -> List[Dict]:
        """获取学习历史"""
        return self._learning_history[-limit:]

    def reset_weights(self) -> Dict[str, Any]:
        """重置提示词权重（回滚机制）"""
        old = dict(self._prompt_weights)
        self._prompt_weights = dict(self.PROMPT_DIMENSIONS)
        self._save_state()
        logger.info("提示词权重已重置")
        return {"old": old, "new": dict(self._prompt_weights)}


# 全局单例
_engine: SelfLearningEngine = None


def get_engine() -> SelfLearningEngine:
    """获取自学习引擎单例"""
    global _engine
    if _engine is None:
        _engine = SelfLearningEngine()
    return _engine


def init_engine(ledger=None, knowledge_store=None, feedback_collector=None,
                content_filter=None, data_dir: str = "") -> SelfLearningEngine:
    """初始化自学习引擎（由server.py调用，注入依赖）"""
    global _engine
    _engine = SelfLearningEngine(
        ledger=ledger,
        knowledge_store=knowledge_store,
        feedback_collector=feedback_collector,
        content_filter=content_filter,
        data_dir=data_dir,
    )
    return _engine
