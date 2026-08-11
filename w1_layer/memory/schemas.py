# -*- coding: utf-8 -*-
"""
schemas.py — 记忆层统一数据模型
================================
定义 L1/L2/L3 三层记忆的数据结构（用 dataclass，避免引入 pydantic 依赖）。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional


@dataclass
class MemoryItem:
    """记忆条目基类"""
    id: str
    layer: str  # L1 / L2 / L3
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class L1WorkingMemory(MemoryItem):
    """L1 工作记忆：当前对话上下文（短期、易失）"""
    layer: str = "L1"
    role: str = "user"  # user / assistant / system
    content: str = ""
    task_id: str = ""
    tokens: int = 0


@dataclass
class L2SemanticMemory(MemoryItem):
    """L2 语义记忆：知识、偏好、概念（向量检索）"""
    layer: str = "L2"
    content: str = ""
    source: str = ""  # 来源：knowledge / preference / self_chat / document
    category: str = "general"
    score: float = 0.0  # 质量分
    tags: List[str] = field(default_factory=list)


@dataclass
class L3EpisodicMemory(MemoryItem):
    """L3 情景记忆：任务执行轨迹、反思记录（时序查询）"""
    layer: str = "L3"
    event_type: str = "task"  # task / reflection / decision / error
    task_desc: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    result: str = ""
    success: bool = True
    reflection: str = ""


@dataclass
class MemoryQuery:
    """记忆检索请求"""
    query: str
    layers: List[str] = field(default_factory=lambda: ["L1", "L2", "L3"])
    top_k: int = 5
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    category: Optional[str] = None
