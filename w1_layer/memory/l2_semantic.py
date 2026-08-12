# -*- coding: utf-8 -*-
"""
l2_semantic.py — L2 语义记忆（向量检索）
========================================
复用项目已有的 VectorKnowledgeStore（FAISS+SBERT+jieba 三级降级 + 混合检索），
比参考实现的 TF-IDF 更先进。维护独立的 L2 索引，与 RAG 知识库分离。
"""
import os
import uuid
import logging
from typing import List, Dict, Any, Optional

from w1_layer.memory.schemas import L2SemanticMemory

logger = logging.getLogger("SCU3.w1.memory.l2")

# 复用项目已有的向量库（FAISS+SBERT+BM25 三级降级 + 混合检索）
try:
    from w1_layer.vector_store import VectorKnowledgeStore
    _HAS_VECTOR = True
except Exception:
    _HAS_VECTOR = False
    VectorKnowledgeStore = None  # type: ignore

# 降级：sklearn TF-IDF
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _HAS_SKLEARN = True
except Exception:
    _HAS_SKLEARN = False


class L2SemanticStore:
    """L2 语义记忆：知识/偏好/概念，向量检索

    策略：优先复用 VectorKnowledgeStore（FAISS+SBERT），
    缺失时降级为 TF-IDF，再缺失则用关键词匹配。
    """

    def __init__(self, data_dir: str = ""):
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "SCU3_data", "memory"
        )
        os.makedirs(self.data_dir, exist_ok=True)

        # L2 专属向量库索引目录
        l2_index_dir = os.path.join(self.data_dir, "l2_index")
        os.makedirs(l2_index_dir, exist_ok=True)

        self._backend = "none"
        self._vector_store = None
        self._tfidf_vectorizer = None
        self._tfidf_matrix = None
        self._tfidf_ids: List[str] = []
        self._items: Dict[str, L2SemanticMemory] = {}  # id -> item（内存缓存）

        if _HAS_VECTOR:
            try:
                self._vector_store = VectorKnowledgeStore(store_dir=l2_index_dir)
                self._backend = "faiss+sbert"
                logger.info("L2 语义记忆使用 VectorKnowledgeStore（FAISS+SBERT）")
            except Exception as e:
                logger.warning(f"VectorKnowledgeStore 初始化失败，降级 TF-IDF: {e}")
                self._vector_store = None

        if self._vector_store is None and _HAS_SKLEARN:
            self._backend = "tfidf"
            logger.info("L2 语义记忆使用 TF-IDF 后端")

        if self._backend == "none":
            self._backend = "keyword"
            logger.warning("L2 语义记忆降级为关键词匹配")

    def add(self, content: str, source: str = "", category: str = "general",
            score: float = 0.0, tags: List[str] = None,
            metadata: Dict = None) -> L2SemanticMemory:
        """添加语义记忆"""
        item = L2SemanticMemory(
            id=str(uuid.uuid4())[:12],
            content=content,
            source=source,
            category=category,
            score=score,
            tags=tags or [],
            metadata=metadata or {},
        )
        self._items[item.id] = item

        # 写入向量库
        if self._vector_store is not None:
            try:
                self._vector_store.add_document(content, metadata={
                    "id": item.id, "source": source,
                    "category": category, "tags": tags or [],
                })
            except Exception as e:
                logger.warning(f"L2 向量库写入失败: {e}")

        # 写入 TF-IDF（重建索引）
        if self._backend == "tfidf":
            self._rebuild_tfidf()

        return item

    def search(self, query: str, top_k: int = 5,
               category: Optional[str] = None) -> List[Dict[str, Any]]:
        """语义检索"""
        if not self._items or not query:
            return []

        # 先按 category 过滤候选集
        candidates = list(self._items.values())
        if category:
            candidates = [m for m in candidates if m.category == category]

        if not candidates:
            return []

        results: List[Dict[str, Any]] = []

        # 通道 1: VectorKnowledgeStore（FAISS+SBERT）
        if self._vector_store is not None:
            try:
                hits = self._vector_store.search(query, top_k=top_k * 2)
                for hit in hits:
                    meta = hit.get("metadata", {})
                    item_id = meta.get("id", "")
                    if item_id and item_id in self._items:
                        item = self._items[item_id]
                        results.append({
                            "id": item.id,
                            "timestamp": item.timestamp,
                            "content": item.content,
                            "source": item.source,
                            "category": item.category,
                            "score": item.score,
                            "tags": item.tags,
                            "similarity": float(hit.get("score", 0.0)),
                        })
            except Exception as e:
                logger.warning(f"L2 向量检索失败: {e}")

        # 通道 2: TF-IDF
        if not results and self._backend == "tfidf" and self._tfidf_vectorizer is not None:
            results = self._tfidf_search(query, candidates, top_k)

        # 通道 3: 关键词匹配（兜底）
        if not results:
            results = self._keyword_search(query, candidates, top_k)

        return results[:top_k]

    def _rebuild_tfidf(self):
        """重建 TF-IDF 索引"""
        if not _HAS_SKLEARN or not self._items:
            return
        try:
            corpus = [m.content for m in self._items.values()]
            self._tfidf_ids = [m.id for m in self._items.values()]
            self._tfidf_vectorizer = TfidfVectorizer(max_features=5000)
            self._tfidf_matrix = self._tfidf_vectorizer.fit_transform(corpus)
        except Exception as e:
            logger.warning(f"L2 TF-IDF 重建失败: {e}")
            self._tfidf_vectorizer = None

    def _tfidf_search(self, query: str, candidates: List[L2SemanticMemory],
                      top_k: int) -> List[Dict[str, Any]]:
        """TF-IDF 向量检索"""
        try:
            q_vec = self._tfidf_vectorizer.transform([query])
            sims = cosine_similarity(q_vec, self._tfidf_matrix).flatten()
            scored = []
            for i, sim in enumerate(sims):
                if i < len(self._tfidf_ids):
                    item_id = self._tfidf_ids[i]
                    item = self._items.get(item_id)
                    if item and item in candidates and sim > 0.01:
                        scored.append((item, float(sim)))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [{
                "id": item.id, "timestamp": item.timestamp,
                "content": item.content, "source": item.source,
                "category": item.category, "score": item.score,
                "tags": item.tags, "similarity": round(sim, 4),
            } for item, sim in scored[:top_k]]
        except Exception as e:
            logger.warning(f"L2 TF-IDF 检索失败: {e}")
            return []

    def _keyword_search(self, query: str, candidates: List[L2SemanticMemory],
                        top_k: int) -> List[Dict[str, Any]]:
        """关键词匹配（兜底）"""
        kw = query.lower()
        scored = []
        for item in candidates:
            hits = sum(1 for t in kw.split() if t in item.content.lower())
            if hits > 0 or kw in item.content.lower():
                sim = hits / max(len(kw.split()), 1) if hits > 0 else 0.5
                scored.append((item, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [{
            "id": item.id, "timestamp": item.timestamp,
            "content": item.content, "source": item.source,
            "category": item.category, "score": item.score,
            "tags": item.tags, "similarity": round(sim, 4),
        } for item, sim in scored[:top_k]]

    def forget(self, item_id: str) -> bool:
        """遗忘指定条目"""
        if item_id in self._items:
            del self._items[item_id]
            if self._backend == "tfidf":
                self._rebuild_tfidf()
            return True
        return False

    def by_category(self, category: str, limit: int = 50) -> List[Dict]:
        return [
            {"id": m.id, "timestamp": m.timestamp, "content": m.content,
             "source": m.source, "category": m.category, "score": m.score, "tags": m.tags}
            for m in list(self._items.values()) if m.category == category
        ][:limit]

    def stats(self) -> Dict[str, Any]:
        cats: Dict[str, int] = {}
        sources: Dict[str, int] = {}
        for m in self._items.values():
            cats[m.category] = cats.get(m.category, 0) + 1
            sources[m.source] = sources.get(m.source, 0) + 1
        return {
            "layer": "L2",
            "items": len(self._items),
            "backend": self._backend,
            "categories": cats,
            "sources": sources,
        }
