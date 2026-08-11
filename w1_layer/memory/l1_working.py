# -*- coding: utf-8 -*-
"""
l1_working.py — L1 工作记忆（短期对话上下文）
=============================================
基于内存的双端队列，保留最近 N 轮对话，自动截断。
线程安全，支持任务 ID 关联。
"""
import uuid
import threading
from collections import deque
from typing import List, Dict, Any

from w1_layer.memory.schemas import L1WorkingMemory


class L1WorkingStore:
    """L1 工作记忆存储（线程安全的内存队列）"""

    def __init__(self, max_turns: int = 20):
        self.max_turns = max_turns
        self._buffer: deque = deque(maxlen=max_turns)
        self._lock = threading.Lock()

    def add(self, role: str, content: str, task_id: str = "",
            tokens: int = 0, metadata: Dict = None) -> L1WorkingMemory:
        """添加一条工作记忆"""
        item = L1WorkingMemory(
            id=str(uuid.uuid4())[:12],
            role=role,
            content=content,
            task_id=task_id,
            tokens=tokens,
            metadata=metadata or {},
        )
        with self._lock:
            self._buffer.append(item)
        return item

    def recent(self, n: int = 10) -> List[L1WorkingMemory]:
        """获取最近 n 条"""
        with self._lock:
            return list(self._buffer)[-n:]

    def search(self, keyword: str, top_k: int = 5) -> List[L1WorkingMemory]:
        """关键词搜索"""
        kw = keyword.lower()
        with self._lock:
            matched = [m for m in self._buffer if kw in m.content.lower()]
        return matched[-top_k:]

    def by_task(self, task_id: str) -> List[L1WorkingMemory]:
        """按任务 ID 查询"""
        with self._lock:
            return [m for m in self._buffer if m.task_id == task_id]

    def clear(self):
        """清空工作记忆"""
        with self._lock:
            self._buffer.clear()

    def forget(self, item_id: str) -> bool:
        """遗忘指定条目"""
        with self._lock:
            for i, m in enumerate(self._buffer):
                if m.id == item_id:
                    del self._buffer[i]
                    return True
        return False

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total_tokens = sum(m.tokens for m in self._buffer)
            roles: Dict[str, int] = {}
            for m in self._buffer:
                roles[m.role] = roles.get(m.role, 0) + 1
            return {
                "layer": "L1",
                "items": len(self._buffer),
                "max_capacity": self.max_turns,
                "total_tokens": total_tokens,
                "roles": roles,
            }

    def to_messages(self, n: int = 20) -> List[Dict[str, str]]:
        """转换为 OpenAI 消息格式（供 LLM 上下文使用）"""
        with self._lock:
            items = list(self._buffer)[-n:]
        return [{"role": m.role, "content": m.content} for m in items]
