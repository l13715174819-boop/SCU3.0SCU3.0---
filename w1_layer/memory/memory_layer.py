# -*- coding: utf-8 -*-
"""
memory_layer.py — 向后兼容的 MemoryLayer
=========================================
包装 MemoryStore，保持原 MemoryLayer 接口（recall/store/retrieve_knowledge/process）
对 server.py 等上层调用方零改动。

新增能力：
  - store() 自动写入 L1 + L3（任务轨迹）
  - recall() 优先从 L1 召回，支持 L2 语义补充
  - retrieve_knowledge() 走 RAG 知识库（保持不变）
  - 新增 save_episode/forget/stats 等三级记忆专属接口
"""
import logging
from typing import Dict, Any, List

from w1_layer.memory.unified_api import get_memory_store
from w1_layer.knowledge_store import get_store

logger = logging.getLogger("scu2.w1.memory")


class MemoryLayer:
    """记忆层 — 三级记忆（L1/L2/L3）+ RAG 检索

    向后兼容接口：
        recall(user_id, limit) -> List[Dict]
        store(user_input, response, user_id)
        retrieve_knowledge(query) -> str
        process(ctx) -> ctx

    新增三级记忆接口：
        save_episode(event_type, task_desc, steps, result, success, reflection)
        search_cross_layer(query, layers, top_k) -> Dict
        forget(layer, item_id) -> bool
        stats() -> Dict
    """

    def __init__(self):
        self._store = get_memory_store()
        self._knowledge = get_store()
        # 兼容旧代码：保留 _conversations 引用（实际由 L1 承载）
        self._conversations: List[Dict[str, str]] = []
        self._max_history = 50
        logger.info("MemoryLayer 初始化（三级记忆 L1+L2+L3）")

    # ─── 向后兼容接口 ────────────────────────────────────

    def recall(self, user_id: str = "", limit: int = 10) -> List[Dict[str, str]]:
        """召回对话历史（兼容旧接口）

        优先从 L1 工作记忆召回，转换为旧格式 [{user_id, input, response, timestamp}]。
        """
        # 从 L1 取最近对话（按 user/assistant 配对组装成 input/response 格式）
        recent = self._store.l1.recent(limit * 2)
        result: List[Dict[str, str]] = []
        current: Dict[str, str] = {}

        for m in recent:
            if m.role == "user":
                if current:
                    result.append(current)
                current = {
                    "user_id": m.metadata.get("user_id", ""),
                    "input": m.content[:200],
                    "timestamp": m.timestamp,
                    "response": "",
                }
            elif m.role == "assistant" and current:
                current["response"] = m.content[:500]
                result.append(current)
                current = {}
        if current:
            result.append(current)

        # 按 user_id 过滤
        if user_id:
            result = [c for c in result if c.get("user_id") == user_id]

        # 同步到兼容引用
        self._conversations = result[-self._max_history:]
        return result[-limit:]

    def store(self, user_input: str, response: str, user_id: str = ""):
        """存储对话（兼容旧接口）

        同时写入：
          - L1 工作记忆（user + assistant 两条）
          - L3 情景记忆（task 轨迹）
        """
        # L1 工作记忆
        self._store.l1.add(
            role="user", content=user_input,
            metadata={"user_id": user_id}
        )
        self._store.l1.add(
            role="assistant", content=response,
            metadata={"user_id": user_id}
        )

        # L3 情景记忆（任务轨迹）
        self._store.l3.add(
            event_type="task",
            task_desc=user_input[:200],
            steps=[
                {"role": "user", "content": user_input[:500]},
                {"role": "assistant", "content": response[:500]},
            ],
            result=response[:500],
            success=True,
            metadata={"user_id": user_id}
        )

        # 同步兼容引用
        self._conversations.append({
            "user_id": user_id,
            "input": user_input[:200],
            "response": response[:500],
        })
        if len(self._conversations) > self._max_history:
            self._conversations = self._conversations[-self._max_history:]

    def retrieve_knowledge(self, query: str) -> str:
        """RAG 知识检索（保持原行为，走 KnowledgeStore）"""
        return self._knowledge.get_context(query)

    def process(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """处理数据流（W1 同层，免审）"""
        user_id = ctx.get("user_id", "")
        user_input = ctx.get("perceived", "")
        # 召回历史（兼容旧格式）
        ctx["recalled"] = self.recall(user_id, limit=5)
        # RAG 检索
        if user_input:
            ctx["rag_context"] = self.retrieve_knowledge(user_input)
        # 新增：L2 语义补充（若有相关记忆）
        if user_input:
            l2_hits = self._store.l2.search(user_input, top_k=2)
            if l2_hits:
                ctx["semantic_memory"] = l2_hits
        ctx["memory_ok"] = True
        return ctx

    # ─── 新增三级记忆接口 ────────────────────────────────

    def save_episode(self, event_type: str, task_desc: str = "",
                     steps: List[Dict] = None, result: str = "",
                     success: bool = True, reflection: str = "") -> str:
        """保存情景到 L3"""
        return self._store.save_episode(event_type, task_desc, steps, result, success, reflection)

    def save_knowledge(self, content: str, source: str = "document",
                       category: str = "general", score: float = 0.7,
                       tags: List[str] = None) -> str:
        """保存知识到 L2"""
        return self._store.save_knowledge(content, source, category, score, tags)

    def search_cross_layer(self, query: str, layers: List[str] = None,
                           top_k: int = 5) -> Dict[str, List[Dict]]:
        """跨层检索"""
        return self._store.search(query, layers, top_k)

    def forget(self, layer: str, item_id: str) -> bool:
        """遗忘指定记忆"""
        return self._store.forget(layer, item_id)

    def clear_l1(self):
        """清空工作记忆（重启对话）"""
        self._store.clear_l1()
        self._conversations.clear()

    def stats(self) -> Dict[str, Any]:
        """三级记忆统计"""
        return self._store.stats()

    def health(self) -> Dict[str, Any]:
        """健康检查"""
        return self._store.health()

    def recall_context(self, query: str, n_l1: int = 6, n_l2: int = 3) -> List[Dict[str, str]]:
        """召回 LLM 上下文（L1 历史 + L2 相关知识）"""
        return self._store.recall_context(query, n_l1, n_l2)
