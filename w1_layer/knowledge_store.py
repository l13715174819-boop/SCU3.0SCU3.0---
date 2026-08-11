# -*- coding: utf-8 -*-
"""
w1_layer/knowledge_store.py — RAG知识库（W1层）
================================================
任务2.2：RAG知识库

特性：
  - 轻量级TF-IDF向量化（无需外部依赖）
  - 文档存储与检索
  - 相似度计算（余弦相似度）
  - 知识导入接口
  - 持久化到JSON

架构归属：W1层（记忆层调用检索，执行层调用导入）
"""
import os
import re
import json
import math
import time
import logging
import threading
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime

logger = logging.getLogger("scu2.w1.knowledge")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "scu2_data", "knowledge")
KNOWLEDGE_INDEX_PATH = os.path.join(KNOWLEDGE_DIR, "knowledge_index.json")


class KnowledgeStore:
    """RAG知识库 — TF-IDF向量检索"""

    def __init__(self, store_path: str = KNOWLEDGE_INDEX_PATH):
        self.store_path = store_path
        self._lock = threading.Lock()
        self._documents: List[Dict[str, Any]] = []  # [{id, content, metadata, tfidf_vector}]
        self._vocabulary: Dict[str, int] = {}  # {word: doc_frequency}
        self._idf_cache: Dict[str, float] = {}
        self._next_id = 1
        self._load()

    def _tokenize(self, text: str) -> List[str]:
        """中文分词（基于字符 + 英文单词）"""
        # 英文单词
        words = re.findall(r'[a-zA-Z]+', text.lower())
        # 中文字符（2-4字组合）
        chinese = re.findall(r'[\u4e00-\u9fff]+', text)
        for seg in chinese:
            # 2-gram
            for i in range(len(seg) - 1):
                words.append(seg[i:i+2])
            # 单字
            for c in seg:
                words.append(c)
        return [w for w in words if len(w) >= 1]

    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """计算词频"""
        if not tokens:
            return {}
        tf = {}
        for w in tokens:
            tf[w] = tf.get(w, 0) + 1
        total = len(tokens)
        return {w: c / total for w, c in tf.items()}

    def _compute_idf(self, word: str) -> float:
        """计算逆文档频率"""
        if word in self._idf_cache:
            return self._idf_cache[word]
        df = self._vocabulary.get(word, 0)
        if df == 0:
            return 0
        idf = math.log((len(self._documents) + 1) / (df + 1)) + 1
        self._idf_cache[word] = idf
        return idf

    def _compute_tfidf(self, tokens: List[str]) -> Dict[str, float]:
        """计算TF-IDF向量"""
        tf = self._compute_tf(tokens)
        return {w: tf_val * self._compute_idf(w) for w, tf_val in tf.items()}

    def _cosine_similarity(self, v1: Dict[str, float], v2: Dict[str, float]) -> float:
        """余弦相似度"""
        if not v1 or not v2:
            return 0.0
        # 找共同词
        common = set(v1.keys()) & set(v2.keys())
        if not common:
            return 0.0
        # 点积
        dot = sum(v1[w] * v2[w] for w in common)
        # 模
        norm1 = math.sqrt(sum(v * v for v in v1.values()))
        norm2 = math.sqrt(sum(v * v for v in v2.values()))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def _recompute_all_tfidf(self):
        """重算所有文档的TF-IDF向量（词汇表变更后调用）"""
        self._idf_cache.clear()
        for doc in self._documents:
            doc["tfidf"] = self._compute_tfidf(doc["tokens"])

    def add_document(self, content: str, metadata: Dict = None) -> int:
        """添加文档

        Args:
            content: 文档内容
            metadata: 元数据（如source, title等）

        Returns:
            文档ID
        """
        if not content or not content.strip():
            return -1

        with self._lock:
            tokens = self._tokenize(content)

            doc_id = self._next_id
            self._next_id += 1

            doc = {
                "id": doc_id,
                "content": content,
                "metadata": metadata or {},
                "tokens": tokens,
                "tfidf": {},  # 占位，下面重算
                "created_at": datetime.now().isoformat(),
            }
            self._documents.append(doc)

            # 更新词表
            for w in set(tokens):
                self._vocabulary[w] = self._vocabulary.get(w, 0) + 1

            # 词汇表变更后重算所有文档TF-IDF（IDF依赖全局词频）
            self._recompute_all_tfidf()

            self._save()
            logger.info(f"知识库添加文档 #{doc_id} ({len(content)}字)")
            return doc_id

    def search(self, query: str, top_k: int = 3, threshold: float = 0.1) -> List[Dict]:
        """检索相关文档

        Args:
            query: 查询文本
            top_k: 返回前K条
            threshold: 相似度阈值

        Returns:
            [{id, content, metadata, score}]
        """
        if not self._documents or not query:
            return []

        query_tokens = self._tokenize(query)
        query_tfidf = self._compute_tfidf(query_tokens)

        scores = []
        for doc in self._documents:
            score = self._cosine_similarity(query_tfidf, doc["tfidf"])
            if score >= threshold:
                scores.append({
                    "id": doc["id"],
                    "content": doc["content"],
                    "metadata": doc["metadata"],
                    "score": round(score, 4),
                })

        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]

    def get_context(self, query: str, max_length: int = 1000) -> str:
        """获取检索上下文（供LLM使用）

        Args:
            query: 查询文本
            max_length: 最大上下文长度

        Returns:
            拼接的上下文文本
        """
        results = self.search(query, top_k=3)
        if not results:
            return ""

        context_parts = []
        for r in results:
            content = r["content"][:max_length // len(results)]
            context_parts.append(f"[知识#{r['id']}] {content}")

        return "\n\n".join(context_parts)

    def import_from_file(self, file_path: str) -> int:
        """从文件导入知识

        Args:
            file_path: 文本文件路径

        Returns:
            添加的文档ID
        """
        if not os.path.exists(file_path):
            return -1
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 按段落分割
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        ids = []
        for para in paragraphs:
            doc_id = self.add_document(para, metadata={"source": os.path.basename(file_path)})
            if doc_id > 0:
                ids.append(doc_id)
        return len(ids)

    def import_from_directory(self, dir_path: str) -> int:
        """从目录批量导入知识（C3修复：限制在知识库目录内）

        Args:
            dir_path: 目录路径（必须为项目目录子目录）

        Returns:
            导入的文档数
        """
        # C3修复：路径白名单检查，限制在项目目录内
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        allowed_root = os.path.join(base_dir, "scu2_data")
        abs_path = os.path.abspath(dir_path)
        # M1修复：用 commonpath 防前缀碰撞
        try:
            common = os.path.commonpath([abs_path, allowed_root])
            if common != allowed_root:
                logger.warning(f"拒绝导入越界路径: {dir_path}")
                return 0
        except ValueError:
            logger.warning(f"路径校验失败: {dir_path}")
            return 0

        if not os.path.isdir(abs_path):
            return 0
        total = 0
        for fname in os.listdir(abs_path):
            if fname.endswith((".txt", ".md", ".json")):
                fpath = os.path.join(abs_path, fname)
                count = self.import_from_file(fpath)
                total += count
        logger.info(f"从 {abs_path} 导入 {total} 个文档")
        return total

    def list_documents(self, limit: int = 20) -> List[Dict]:
        """列出文档"""
        return [{"id": d["id"], "content": d["content"][:100],
                 "metadata": d["metadata"], "created_at": d["created_at"]}
                for d in self._documents[-limit:]]

    def delete_document(self, doc_id: int) -> bool:
        """删除文档"""
        with self._lock:
            before = len(self._documents)
            self._documents = [d for d in self._documents if d["id"] != doc_id]
            if len(self._documents) < before:
                # 重建词汇表并重算TF-IDF
                self._rebuild_vocabulary()
                self._recompute_all_tfidf()
                self._save()
                return True
            return False

    def _rebuild_vocabulary(self):
        """从现有文档重建词汇表"""
        self._vocabulary = {}
        for doc in self._documents:
            for w in set(doc["tokens"]):
                self._vocabulary[w] = self._vocabulary.get(w, 0) + 1

    def clear(self):
        """清空知识库"""
        with self._lock:
            self._documents = []
            self._vocabulary = {}
            self._idf_cache = {}
            self._next_id = 1
            self._save()

    def get_status(self) -> Dict[str, Any]:
        """获取知识库状态"""
        return {
            "total_documents": len(self._documents),
            "vocabulary_size": len(self._vocabulary),
            "store_path": self.store_path,
            "next_id": self._next_id,
        }

    def _save(self):
        """持久化"""
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        data = {
            "documents": [{k: v for k, v in d.items() if k != "tfidf"}
                          for d in self._documents],
            "vocabulary": self._vocabulary,
            "next_id": self._next_id,
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self):
        """加载"""
        if not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 先加载词汇表（IDF依赖它）
            self._vocabulary = data.get("vocabulary", {})
            self._next_id = data.get("next_id", 1)
            # 再加载文档并计算TF-IDF
            self._documents = []
            for d in data.get("documents", []):
                tokens = d.get("tokens", [])
                if not tokens:
                    tokens = self._tokenize(d.get("content", ""))
                d["tokens"] = tokens
                self._documents.append(d)
            # 词汇表已就绪，重算所有TF-IDF
            self._recompute_all_tfidf()
            logger.info(f"知识库加载: {len(self._documents)}个文档, {len(self._vocabulary)}个词")
        except Exception as e:
            logger.warning(f"知识库加载失败: {e}")


# 全局单例
_store = None  # 类型：KnowledgeStore 或 VectorKnowledgeStore


def get_store():
    """获取知识库单例（优先返回向量版本，降级到TF-IDF）

    向量版本优先级：FAISS/ChromaDB + sentence-transformers > NumPy降级
    当向量库不可用时，自动降级到TF-IDF知识库
    """
    global _store
    if _store is None:
        try:
            # 优先使用向量知识库
            from w1_layer.vector_store import get_vector_store
            _store = get_vector_store()
            logger.info("知识库后端: 向量数据库（FAISS/ChromaDB/NumPy降级）")
        except ImportError:
            # 降级到TF-IDF
            _store = KnowledgeStore()
            logger.info("知识库后端: TF-IDF（向量模块未安装）")
        except Exception as e:
            logger.warning(f"向量知识库初始化失败，降级到TF-IDF: {e}")
            _store = KnowledgeStore()
    return _store
