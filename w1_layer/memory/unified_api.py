# -*- coding: utf-8 -*-
"""
unified_api.py — 统一记忆 API（MemoryStore）
=============================================
封装 L1/L2/L3 三层，提供 save/load/search/forget 统一接口。
屏蔽底层差异，上层只需调用 MemoryStore。
"""
import os
import logging
from typing import List, Dict, Any, Optional

from w1_layer.memory.l1_working import L1WorkingStore
from w1_layer.memory.l2_semantic import L2SemanticStore
from w1_layer.memory.l3_episodic import L3EpisodicStore

logger = logging.getLogger("SCU3.w1.memory")


class MemoryStore:
    """统一记忆存储 — 三层记忆的统一入口"""

    def __init__(self, data_dir: str = "", l1_max_turns: int = 20):
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.data_dir = data_dir or os.path.join(base, "SCU3_data", "memory")
        os.makedirs(self.data_dir, exist_ok=True)

        self.l1 = L1WorkingStore(max_turns=l1_max_turns)
        self.l2 = L2SemanticStore(data_dir=self.data_dir)
        self.l3 = L3EpisodicStore(db_path=os.path.join(self.data_dir, "memory_l3.db"))
        logger.info("MemoryStore 初始化完成（L1+L2+L3）")

    # ─── 保存 ────────────────────────────────────────────

    def save_conversation(self, role: str, content: str, task_id: str = "",
                          tokens: int = 0, persist_l2: bool = False,
                          category: str = "conversation") -> Dict[str, Any]:
        """保存对话：默认入 L1，可选入 L2 持久化"""
        l1_item = self.l1.add(role, content, task_id, tokens)
        result = {"l1_id": l1_item.id}
        if persist_l2 and content.strip():
            l2_item = self.l2.add(
                content=content, source=f"chat:{role}",
                category=category, score=0.5
            )
            result["l2_id"] = l2_item.id
        return result

    def save_knowledge(self, content: str, source: str = "document",
                       category: str = "general", score: float = 0.7,
                       tags: List[str] = None) -> str:
        """保存知识到 L2"""
        item = self.l2.add(content, source, category, score, tags)
        return item.id

    def save_episode(self, event_type: str, task_desc: str = "",
                     steps: List[Dict] = None, result: str = "",
                     success: bool = True, reflection: str = "") -> str:
        """保存情景到 L3"""
        item = self.l3.add(event_type, task_desc, steps, result, success, reflection)
        return item.id

    # ─── 检索 ────────────────────────────────────────────

    def search(self, query: str, layers: List[str] = None,
               top_k: int = 5, category: Optional[str] = None,
               time_start: Optional[str] = None,
               time_end: Optional[str] = None) -> Dict[str, List[Dict]]:
        """跨层检索"""
        layers = layers or ["L1", "L2", "L3"]
        results = {}
        if "L1" in layers:
            results["L1"] = [
                {"id": m.id, "timestamp": m.timestamp, "role": m.role,
                 "content": m.content, "task_id": m.task_id}
                for m in self.l1.search(query, top_k)
            ]
        if "L2" in layers:
            results["L2"] = self.l2.search(query, top_k, category=category)
        if "L3" in layers:
            results["L3"] = self.l3.search(
                keyword=query, top_k=top_k,
                time_start=time_start, time_end=time_end
            )
        return results

    def recall_context(self, query: str, n_l1: int = 6, n_l2: int = 3) -> List[Dict[str, str]]:
        """召回上下文（供 LLM 使用）：L1 历史 + L2 相关知识"""
        context = []
        # L1 最近对话
        for m in self.l1.recent(n_l1):
            context.append({"role": m.role, "content": m.content})
        # L2 相关知识
        l2_results = self.l2.search(query, top_k=n_l2)
        if l2_results:
            knowledge = "\n".join(
                f"- {r['content'][:200]}" for r in l2_results if r.get("similarity", 0) > 0.05
            )
            if knowledge:
                context.append({
                    "role": "system",
                    "content": f"[相关记忆]\n{knowledge}"
                })
        return context

    # ─── 遗忘 ────────────────────────────────────────────

    def forget(self, layer: str, item_id: str) -> bool:
        """遗忘指定记忆"""
        if layer == "L1":
            return self.l1.forget(item_id)
        elif layer == "L2":
            return self.l2.forget(item_id)
        elif layer == "L3":
            return self.l3.forget(item_id)
        return False

    def clear_l1(self):
        """清空工作记忆（重启对话）"""
        self.l1.clear()

    # ─── 统计 ────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "L1": self.l1.stats(),
            "L2": self.l2.stats(),
            "L3": self.l3.stats(),
        }

    def health(self) -> Dict[str, Any]:
        s = self.stats()
        return {
            "healthy": True,
            "total_items": s["L1"]["items"] + s["L2"]["items"] + s["L3"]["items"],
            "layers": {
                "L1_items": s["L1"]["items"],
                "L2_items": s["L2"]["items"],
                "L3_items": s["L3"]["items"],
            },
        }


# ─── 全局单例 ────────────────────────────────────────────

_memory_store: Optional[MemoryStore] = None


def get_memory_store() -> MemoryStore:
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store
