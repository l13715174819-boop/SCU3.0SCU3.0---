# -*- coding: utf-8 -*-
"""
w1_layer/vector_store.py — 向量数据库知识库（W1层）
====================================================
基于向量嵌入的知识库，替换原TF-IDF实现。

特性：
  - 嵌入模型三级降级：sentence-transformers → sklearn HashingVectorizer → NumPy Hash
  - 向量存储三级降级：FAISS → ChromaDB → NumPy矩阵
  - 嵌入缓存（相同文本不重复计算）
  - 增量索引（添加文档只追加新向量）
  - 持久化（FAISS索引文件 + 文档元数据JSON + 嵌入缓存JSON）
  - 混合检索（向量相似度 + BM25风格关键词匹配，加权融合）
  - 中文优化（jieba分词，无jieba时用字符2-gram）
  - 完整兼容 KnowledgeStore 接口

架构归属：W1层（记忆层调用检索，执行层调用导入）
"""
import os
import re
import json
import math
import time
import logging
import hashlib
import threading
from typing import List, Dict, Any, Tuple, Optional

from datetime import datetime

logger = logging.getLogger("scu2.w1.vector")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VECTOR_INDEX_DIR = os.path.join(BASE_DIR, "scu2_data", "knowledge", "vector_index")

# 默认配置
DEFAULT_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_VECTOR_DIM = 384  # MiniLM-L12-v2 默认维度，运行时自动检测
DEFAULT_SIMILARITY_THRESHOLD = 0.3
DEFAULT_VECTOR_WEIGHT = 0.7  # 向量相似度权重
DEFAULT_KEYWORD_WEIGHT = 0.3  # 关键词匹配权重
DEFAULT_HASH_DIM = 1024  # Hash嵌入维度（无外部依赖模式）


# ==================== 可选依赖导入 ====================
# 外部依赖全部可选导入，缺失时自动降级

# 1. sentence-transformers（嵌入模型）
try:
    from sentence_transformers import SentenceTransformer
    _HAS_SBERT = True
except Exception:
    SentenceTransformer = None  # type: ignore
    _HAS_SBERT = False

# 2. faiss（向量索引）
try:
    import faiss  # type: ignore
    _HAS_FAISS = True
except Exception:
    faiss = None  # type: ignore
    _HAS_FAISS = False

# 3. sklearn HashingVectorizer
try:
    from sklearn.feature_extraction.text import HashingVectorizer  # type: ignore
    from sklearn.preprocessing import normalize  # type: ignore
    _HAS_SKLEARN = True
except Exception:
    HashingVectorizer = None  # type: ignore
    normalize = None  # type: ignore
    _HAS_SKLEARN = False

# 4. chromadb
try:
    import chromadb  # type: ignore
    _HAS_CHROMA = True
except Exception:
    chromadb = None  # type: ignore
    _HAS_CHROMA = False

# 5. jieba 中文分词
try:
    import jieba  # type: ignore
    _HAS_JIEBA = True
except Exception:
    jieba = None  # type: ignore
    _HAS_JIEBA = False

# 6. numpy（核心依赖，无numpy时仍可用纯Python实现降级）
try:
    import numpy as np  # type: ignore
    _HAS_NUMPY = True
except Exception:
    np = None  # type: ignore
    _HAS_NUMPY = False


class VectorKnowledgeStore:
    """向量数据库知识库 — 多后端降级 + 混合检索

    嵌入后端优先级：
      1. sentence-transformers（高质量语义嵌入）
      2. sklearn HashingVectorizer（轻量级哈希嵌入）
      3. NumPy + 自定义Hash嵌入（无外部依赖）

    存储后端优先级：
      1. FAISS（高性能向量索引，支持IVF）
      2. ChromaDB（持久化向量数据库）
      3. NumPy矩阵（暴力余弦相似度）
    """

    def __init__(
        self,
        store_dir: str = VECTOR_INDEX_DIR,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        vector_dim: Optional[int] = None,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
    ):
        """初始化向量知识库

        Args:
            store_dir: 持久化目录
            embedding_model: 嵌入模型名（仅sbert后端生效）
            vector_dim: 向量维度（None则自动检测）
            threshold: 默认相似度阈值
            vector_weight: 混合检索中向量权重
            keyword_weight: 混合检索中关键词权重
        """
        self.store_dir = store_dir
        self.embedding_model_name = embedding_model
        self.vector_dim = vector_dim
        self.threshold = threshold
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight

        # 持久化文件路径
        self.faiss_index_path = os.path.join(store_dir, "faiss_index.bin")
        self.documents_path = os.path.join(store_dir, "documents.json")
        self.embeddings_cache_path = os.path.join(store_dir, "embeddings_cache.json")

        # 运行时状态
        self._lock = threading.Lock()
        self._documents: List[Dict[str, Any]] = []
        self._embeddings_cache: Dict[str, List[float]] = {}  # {text_hash: vector}
        self._next_id = 1

        # 后端标识
        self._embed_backend: str = "unknown"  # sbert / sklearn / numpy
        self._storage_backend: str = "unknown"  # faiss / chroma / numpy
        self._sbert_model = None
        self._hashing_vectorizer = None
        self._faiss_index = None
        self._chroma_client = None
        self._chroma_collection = None
        self._numpy_matrix: Optional[List[List[float]]] = None  # 仅numpy存储后端使用

        # BM25倒排索引（关键词检索用）
        self._bm25_inverted: Dict[str, List[Tuple[int, int]]] = {}  # {token: [(doc_id, tf)]}
        self._bm25_doc_len: Dict[int, int] = {}
        self._bm25_avg_len: float = 0.0

        # 检测并初始化后端
        self._detect_backend()
        self._init_backends()
        self._load()

    # ==================== 后端检测 ====================

    def _detect_backend(self) -> None:
        """自动检测可用后端，按优先级降级"""
        # CPU 模式下 SBERT 不稳定（加载后约1-2分钟进程崩溃），强制降级到 sklearn
        _cpu_only = os.environ.get("CUDA_VISIBLE_DEVICES", "") == ""
        if _HAS_SBERT and not _cpu_only:
            self._embed_backend = "sbert"
            logger.info("嵌入后端：sentence-transformers（GPU模式）")
        elif _HAS_SKLEARN:
            self._embed_backend = "sklearn"
            if _cpu_only:
                logger.info("嵌入后端：sklearn HashingVectorizer（CPU模式跳过SBERT避免崩溃）")
            else:
                logger.info("嵌入后端：sklearn HashingVectorizer")
        elif _HAS_SKLEARN:
            self._embed_backend = "sklearn"
            logger.info("嵌入后端：sklearn HashingVectorizer")
        else:
            self._embed_backend = "numpy"
            logger.info("嵌入后端：NumPy Hash嵌入（无外部依赖）")

        # 存储后端检测
        if _HAS_FAISS and (_HAS_NUMPY or self._embed_backend == "sbert"):
            self._storage_backend = "faiss"
            logger.info("存储后端：FAISS")
        elif _HAS_CHROMA:
            self._storage_backend = "chroma"
            logger.info("存储后端：ChromaDB")
        else:
            self._storage_backend = "numpy"
            logger.info("存储后端：NumPy矩阵")

    def _init_backends(self) -> None:
        """根据检测到的后端初始化资源"""
        # 嵌入后端初始化
        try:
            if self._embed_backend == "sbert":
                # 优先尝试 CUDA，失败则降级 CPU（避免 GPU 上下文异常导致服务无法启动）
                self._sbert_model = None
                for _dev in ("cuda", "cpu"):
                    try:
                        self._sbert_model = SentenceTransformer(self.embedding_model_name, device=_dev)
                        logger.info(f"SBERT 加载成功，device={_dev}")
                        break
                    except Exception as _e:
                        logger.warning(f"SBERT device={_dev} 加载失败: {_e}")
                        self._sbert_model = None
                if self._sbert_model is None:
                    raise RuntimeError("SBERT 所有 device 加载均失败")
                # 自动检测维度
                if self.vector_dim is None:
                    test_vec = self._sbert_model.encode("维度检测")
                    self.vector_dim = int(test_vec.shape[0]) if hasattr(test_vec, "shape") else DEFAULT_VECTOR_DIM
                logger.info(f"SBERT模型加载完成，维度={self.vector_dim}")
            elif self._embed_backend == "sklearn":
                dim = self.vector_dim or DEFAULT_HASH_DIM
                self._hashing_vectorizer = HashingVectorizer(
                    n_features=dim,
                    alternate_sign=False,
                    norm=None,
                    token_pattern=r"(?u)\b\w+\b|[\u4e00-\u9fff]",
                )
                self.vector_dim = dim
                logger.info(f"HashingVectorizer初始化完成，维度={dim}")
            else:
                # numpy hash 模式
                self.vector_dim = self.vector_dim or DEFAULT_HASH_DIM
                logger.info(f"NumPy Hash嵌入初始化，维度={self.vector_dim}")
        except Exception as e:
            logger.warning(f"嵌入后端初始化失败，降级到NumPy: {e}")
            self._embed_backend = "numpy"
            self.vector_dim = self.vector_dim or DEFAULT_HASH_DIM

        # 存储后端初始化
        try:
            if self._storage_backend == "faiss":
                self._init_faiss()
            elif self._storage_backend == "chroma":
                self._init_chroma()
            else:
                self._numpy_matrix = []
        except Exception as e:
            logger.warning(f"存储后端初始化失败，降级到NumPy: {e}")
            self._storage_backend = "numpy"
            self._numpy_matrix = []

    def _init_faiss(self) -> None:
        """初始化FAISS索引"""
        dim = int(self.vector_dim)
        # 使用 IndexFlatIP（内积，配合归一化向量等价于余弦相似度）
        # 当数据量大时可换IVF，但需训练，这里用FlatIP保证简单可靠
        self._faiss_index = faiss.IndexFlatIP(dim)
        logger.info(f"FAISS索引初始化完成（dim={dim}, IndexFlatIP）")

    def _init_chroma(self) -> None:
        """初始化ChromaDB"""
        os.makedirs(self.store_dir, exist_ok=True)
        self._chroma_client = chromadb.PersistentClient(path=self.store_dir)
        self._chroma_collection = self._chroma_client.get_or_create_collection(
            name="scu2_knowledge",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB集合初始化完成")

    # ==================== 嵌入计算 ====================

    def _text_hash(self, text: str) -> str:
        """计算文本哈希（用于嵌入缓存键）"""
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def _embed(self, text: str) -> Optional[List[float]]:
        """计算文本嵌入向量（带缓存）

        Args:
            text: 文本

        Returns:
            向量（List[float]）或 None
        """
        if not text or not text.strip():
            return None

        cache_key = self._text_hash(text)
        if cache_key in self._embeddings_cache:
            return self._embeddings_cache[cache_key]

        try:
            vec = self._compute_embedding(text)
            if vec is not None:
                # 缓存
                self._embeddings_cache[cache_key] = vec
                # 异步持久化缓存（这里简单同步保存）
                self._save_embeddings_cache()
            return vec
        except Exception as e:
            logger.warning(f"嵌入计算失败: {e}")
            return None

    def _compute_embedding(self, text: str) -> Optional[List[float]]:
        """实际计算嵌入（不分缓存）"""
        if self._embed_backend == "sbert" and self._sbert_model is not None:
            vec = self._sbert_model.encode(text, normalize_embeddings=True)
            return vec.tolist() if hasattr(vec, "tolist") else list(vec)
        elif self._embed_backend == "sklearn" and self._hashing_vectorizer is not None:
            vec = self._hashing_vectorizer.transform([text]).toarray()[0]
            # L2归一化
            if normalize is not None:
                vec = normalize([vec])[0]
            return vec.tolist() if hasattr(vec, "tolist") else list(vec)
        else:
            # NumPy Hash嵌入：基于字符n-gram的哈希
            return self._numpy_hash_embedding(text)

    def _numpy_hash_embedding(self, text: str) -> List[float]:
        """纯NumPy实现的哈希嵌入（无外部依赖）

        策略：对字符2-gram和单词进行哈希，映射到固定维度向量，再L2归一化
        """
        dim = int(self.vector_dim)
        vec = [0.0] * dim

        tokens = self._tokenize(text)
        # 单词级别哈希
        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if (h // dim) % 2 == 0 else -1.0
            vec[idx] += sign

        # 字符2-gram哈希（增强局部语义）
        for tok in tokens:
            chars = tok if len(tok) <= 1 else [tok[i:i+2] for i in range(len(tok) - 1)]
            for c in chars:
                h = int(hashlib.md5(("c2g:" + c).encode("utf-8")).hexdigest(), 16)
                idx = h % dim
                sign = 1.0 if (h // dim) % 2 == 0 else -1.0
                vec[idx] += sign * 0.5

        # L2归一化
        if _HAS_NUMPY:
            arr = np.array(vec, dtype=np.float32)
            norm = float(np.linalg.norm(arr))
            if norm > 0:
                arr = arr / norm
            return arr.tolist()
        else:
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            return vec

    # ==================== 中文分词 ====================

    def _tokenize(self, text: str) -> List[str]:
        """中文分词（jieba优先，无jieba时字符2-gram + 英文单词）"""
        if not text:
            return []

        if _HAS_JIEBA:
            try:
                words = list(jieba.cut(text))
            except Exception:
                words = self._fallback_tokenize(text)
        else:
            words = self._fallback_tokenize(text)

        # 过滤空白与过短词
        return [w.strip().lower() for w in words if w and w.strip() and len(w.strip()) >= 1]

    def _fallback_tokenize(self, text: str) -> List[str]:
        """无jieba时的分词：英文单词 + 中文2-gram"""
        words: List[str] = []
        # 英文单词
        en_words = re.findall(r'[a-zA-Z0-9]+', text.lower())
        words.extend(en_words)
        # 中文2-gram
        chinese_segs = re.findall(r'[\u4e00-\u9fff]+', text)
        for seg in chinese_segs:
            for i in range(len(seg) - 1):
                words.append(seg[i:i+2])
            # 单字也保留
            for c in seg:
                words.append(c)
        return words

    # ==================== BM25 关键词索引 ====================

    def _bm25_update_doc(self, doc_id: int, tokens: List[str]) -> None:
        """更新BM25倒排索引"""
        self._bm25_doc_len[doc_id] = len(tokens)
        tf_map: Dict[str, int] = {}
        for tok in tokens:
            tf_map[tok] = tf_map.get(tok, 0) + 1
        for tok, tf in tf_map.items():
            if tok not in self._bm25_inverted:
                self._bm25_inverted[tok] = []
            self._bm25_inverted[tok].append((doc_id, tf))
        # 重算平均长度
        if self._bm25_doc_len:
            self._bm25_avg_len = sum(self._bm25_doc_len.values()) / len(self._bm25_doc_len)
        else:
            self._bm25_avg_len = 0.0

    def _bm25_remove_doc(self, doc_id: int) -> None:
        """从BM25索引中移除文档"""
        self._bm25_doc_len.pop(doc_id, None)
        empty_keys = []
        for tok, postings in self._bm25_inverted.items():
            self._bm25_inverted[tok] = [(d, tf) for (d, tf) in postings if d != doc_id]
            if not self._bm25_inverted[tok]:
                empty_keys.append(tok)
        for k in empty_keys:
            del self._bm25_inverted[k]
        if self._bm25_doc_len:
            self._bm25_avg_len = sum(self._bm25_doc_len.values()) / len(self._bm25_doc_len)
        else:
            self._bm25_avg_len = 0.0

    def _bm25_scores(self, query_tokens: List[str], k1: float = 1.5, b: float = 0.75) -> Dict[int, float]:
        """计算BM25得分

        Returns:
            {doc_id: score}
        """
        scores: Dict[int, float] = {}
        N = len(self._documents)
        if N == 0 or not query_tokens:
            return scores
        avg_len = self._bm25_avg_len if self._bm25_avg_len > 0 else 1.0

        for tok in set(query_tokens):
            postings = self._bm25_inverted.get(tok, [])
            if not postings:
                continue
            df = len(postings)
            idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)
            for doc_id, tf in postings:
                doc_len = self._bm25_doc_len.get(doc_id, 0)
                denom = tf + k1 * (1 - b + b * doc_len / avg_len)
                if denom == 0:
                    continue
                s = idf * (tf * (k1 + 1)) / denom
                scores[doc_id] = scores.get(doc_id, 0.0) + s
        return scores

    # ==================== 接口实现 ====================

    def add_document(self, content: str, metadata: Dict = None) -> int:
        """添加文档

        Args:
            content: 文档内容
            metadata: 元数据

        Returns:
            文档ID（失败返回-1）
        """
        if not content or not content.strip():
            return -1

        with self._lock:
            # 计算嵌入（增量，不重建全部）
            vec = self._embed(content)
            if vec is None:
                logger.warning(f"文档嵌入失败，跳过: {content[:50]}")
                return -1

            doc_id = self._next_id
            self._next_id += 1

            doc = {
                "id": doc_id,
                "content": content,
                "metadata": metadata or {},
                "tokens": self._tokenize(content),
                "vector_dim": len(vec),
                "created_at": datetime.now().isoformat(),
            }
            self._documents.append(doc)

            # 增量添加到存储后端
            self._add_vector_to_store(doc_id, vec)

            # 更新BM25索引
            self._bm25_update_doc(doc_id, doc["tokens"])

            self._save()
            logger.info(f"向量库添加文档 #{doc_id} ({len(content)}字, backend={self._storage_backend})")
            return doc_id

    def _add_vector_to_store(self, doc_id: int, vec: List[float]) -> None:
        """增量添加向量到存储后端"""
        try:
            if self._storage_backend == "faiss" and self._faiss_index is not None:
                if _HAS_NUMPY:
                    arr = np.array([vec], dtype=np.float32)
                    self._faiss_index.add(arr)
                else:
                    # faiss要求numpy数组，无numpy时降级
                    self._faiss_index.add([vec])  # type: ignore
            elif self._storage_backend == "chroma" and self._chroma_collection is not None:
                self._chroma_collection.add(
                    ids=[str(doc_id)],
                    embeddings=[vec],
                    metadatas=[{"doc_id": doc_id}],
                )
            else:
                # numpy矩阵
                if self._numpy_matrix is None:
                    self._numpy_matrix = []
                self._numpy_matrix.append(vec)
        except Exception as e:
            logger.warning(f"向量添加到存储失败: {e}")

    def _rebuild_storage_index(self) -> None:
        """重建存储后端索引（删除文档或加载后调用）"""
        try:
            if self._storage_backend == "faiss" and self._faiss_index is not None:
                # 清空并重建
                self._init_faiss()
                for doc in self._documents:
                    vec = self._embed(doc["content"])
                    if vec is not None:
                        if _HAS_NUMPY:
                            self._faiss_index.add(np.array([vec], dtype=np.float32))
                        else:
                            self._faiss_index.add([vec])  # type: ignore
            elif self._storage_backend == "chroma" and self._chroma_collection is not None:
                # ChromaDB清空重建
                try:
                    self._chroma_client.delete_collection("scu2_knowledge")
                except Exception:
                    pass
                self._init_chroma()
                for doc in self._documents:
                    vec = self._embed(doc["content"])
                    if vec is not None:
                        self._chroma_collection.add(
                            ids=[str(doc["id"])],
                            embeddings=[vec],
                            metadatas=[{"doc_id": doc["id"]}],
                        )
            else:
                # numpy矩阵重建
                self._numpy_matrix = []
                for doc in self._documents:
                    vec = self._embed(doc["content"])
                    if vec is not None:
                        self._numpy_matrix.append(vec)
        except Exception as e:
            logger.warning(f"重建存储索引失败: {e}")

    def search(self, query: str, top_k: int = 3, threshold: float = None) -> List[Dict]:
        """混合检索：向量相似度 + BM25关键词匹配

        Args:
            query: 查询文本
            top_k: 返回前K条
            threshold: 相似度阈值（None用默认）

        Returns:
            [{id, content, metadata, score, vector_score, keyword_score}]
        """
        if not self._documents or not query:
            return []

        if threshold is None:
            threshold = self.threshold

        # 1. 向量相似度
        vector_scores = self._vector_search(query, top_k=max(top_k * 2, 10))

        # 2. BM25关键词得分
        query_tokens = self._tokenize(query)
        bm25_scores = self._bm25_scores(query_tokens)

        # 3. 加权融合
        # 收集所有候选文档ID
        all_ids = set(vector_scores.keys()) | set(bm25_scores.keys())
        fused: List[Tuple[int, float, float, float]] = []
        for doc_id in all_ids:
            vs = vector_scores.get(doc_id, 0.0)
            ks = bm25_scores.get(doc_id, 0.0)
            # 归一化BM25（粗略：除以最大值）
            max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0
            ks_norm = ks / max_bm25 if max_bm25 > 0 else 0.0
            score = self.vector_weight * vs + self.keyword_weight * ks_norm
            fused.append((doc_id, score, vs, ks_norm))

        fused.sort(key=lambda x: x[1], reverse=True)
        results: List[Dict] = []
        for doc_id, score, vs, ks in fused[:top_k]:
            if score < threshold:
                continue
            doc = self._get_doc_by_id(doc_id)
            if doc is None:
                continue
            results.append({
                "id": doc["id"],
                "content": doc["content"],
                "metadata": doc.get("metadata", {}),
                "score": round(float(score), 4),
                "vector_score": round(float(vs), 4),
                "keyword_score": round(float(ks), 4),
            })
        return results

    def _vector_search(self, query: str, top_k: int = 10) -> Dict[int, float]:
        """纯向量检索

        Returns:
            {doc_id: score}
        """
        query_vec = self._embed(query)
        if query_vec is None or not self._documents:
            return {}

        try:
            if self._storage_backend == "faiss" and self._faiss_index is not None:
                return self._faiss_search(query_vec, top_k)
            elif self._storage_backend == "chroma" and self._chroma_collection is not None:
                return self._chroma_search(query_vec, top_k)
            else:
                return self._numpy_search(query_vec, top_k)
        except Exception as e:
            logger.warning(f"向量检索失败，降级到numpy: {e}")
            return self._numpy_search(query_vec, top_k)

    def _faiss_search(self, query_vec: List[float], top_k: int) -> Dict[int, float]:
        """FAISS检索"""
        k = min(top_k, self._faiss_index.ntotal) if self._faiss_index.ntotal > 0 else 0
        if k == 0:
            return {}
        if _HAS_NUMPY:
            arr = np.array([query_vec], dtype=np.float32)
            scores, indices = self._faiss_index.search(arr, k)
        else:
            scores, indices = self._faiss_index.search([query_vec], k)  # type: ignore
        result: Dict[int, float] = {}
        for i, idx in enumerate(indices[0]):
            if idx >= 0 and idx < len(self._documents):
                doc_id = self._documents[idx]["id"]
                result[doc_id] = float(scores[0][i])
        return result

    def _chroma_search(self, query_vec: List[float], top_k: int) -> Dict[int, float]:
        """ChromaDB检索"""
        res = self._chroma_collection.query(
            query_embeddings=[query_vec],
            n_results=min(top_k, len(self._documents)),
        )
        result: Dict[int, float] = {}
        ids = res.get("ids", [[]])[0]
        distances = res.get("distances", [[]])[0]
        for i, id_str in enumerate(ids):
            try:
                doc_id = int(id_str)
                # chroma返回cosine distance，转换为相似度
                dist = float(distances[i]) if i < len(distances) else 1.0
                sim = 1.0 - dist
                result[doc_id] = sim
            except (ValueError, IndexError):
                continue
        return result

    def _numpy_search(self, query_vec: List[float], top_k: int) -> Dict[int, float]:
        """NumPy矩阵暴力余弦相似度（向量已归一化，等价于内积）"""
        if not self._numpy_matrix:
            return {}
        result: Dict[int, float] = {}
        for i, doc_vec in enumerate(self._numpy_matrix):
            if i >= len(self._documents):
                break
            sim = self._cosine_sim(query_vec, doc_vec)
            doc_id = self._documents[i]["id"]
            result[doc_id] = sim
        # 取top_k
        sorted_items = sorted(result.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return dict(sorted_items)

    def _cosine_sim(self, v1: List[float], v2: List[float]) -> float:
        """余弦相似度（纯Python实现，兼容无numpy）"""
        if _HAS_NUMPY:
            a = np.array(v1, dtype=np.float32)
            b = np.array(v2, dtype=np.float32)
            na = float(np.linalg.norm(a))
            nb = float(np.linalg.norm(b))
            if na == 0 or nb == 0:
                return 0.0
            return float(np.dot(a, b) / (na * nb))
        else:
            if not v1 or not v2 or len(v1) != len(v2):
                return 0.0
            dot = sum(a * b for a, b in zip(v1, v2))
            n1 = math.sqrt(sum(a * a for a in v1))
            n2 = math.sqrt(sum(b * b for b in v2))
            if n1 == 0 or n2 == 0:
                return 0.0
            return dot / (n1 * n2)

    def get_context(self, query: str, max_length: int = 1000) -> str:
        """获取检索上下文（供LLM使用）"""
        results = self.search(query, top_k=3)
        if not results:
            return ""
        context_parts = []
        per_len = max_length // max(len(results), 1)
        for r in results:
            content = r["content"][:per_len]
            context_parts.append(f"[知识#{r['id']}] {content}")
        return "\n\n".join(context_parts)

    def list_documents(self, limit: int = 20) -> List[Dict]:
        """列出文档"""
        return [
            {
                "id": d["id"],
                "content": d["content"][:100],
                "metadata": d.get("metadata", {}),
                "created_at": d.get("created_at", ""),
            }
            for d in self._documents[-limit:]
        ]

    def delete_document(self, doc_id: int) -> bool:
        """删除文档"""
        with self._lock:
            before = len(self._documents)
            self._documents = [d for d in self._documents if d["id"] != doc_id]
            if len(self._documents) < before:
                # 重建存储索引与BM25索引
                self._bm25_remove_doc(doc_id)
                self._rebuild_storage_index()
                self._save()
                logger.info(f"删除文档 #{doc_id}")
                return True
            return False

    def clear(self) -> None:
        """清空知识库"""
        with self._lock:
            self._documents = []
            self._embeddings_cache = {}
            self._bm25_inverted = {}
            self._bm25_doc_len = {}
            self._bm25_avg_len = 0.0
            self._next_id = 1
            self._numpy_matrix = []
            # 重置存储后端
            self._init_backends()
            self._save()
            # 清理持久化文件
            for p in [self.faiss_index_path, self.documents_path, self.embeddings_cache_path]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception as e:
                        logger.warning(f"清理文件失败 {p}: {e}")
            logger.info("向量知识库已清空")

    def get_status(self) -> Dict[str, Any]:
        """获取知识库状态"""
        return {
            "total_documents": len(self._documents),
            "vector_dim": self.vector_dim,
            "embed_backend": self._embed_backend,
            "storage_backend": self._storage_backend,
            "embedding_model": self.embedding_model_name if self._embed_backend == "sbert" else self._embed_backend,
            "cache_size": len(self._embeddings_cache),
            "vocabulary_size": len(self._bm25_inverted),
            "bm25_avg_doc_len": round(self._bm25_avg_len, 2),
            "store_dir": self.store_dir,
            "next_id": self._next_id,
            "vector_weight": self.vector_weight,
            "keyword_weight": self.keyword_weight,
            "threshold": self.threshold,
        }

    def import_from_file(self, file_path: str) -> int:
        """从文件导入知识"""
        if not os.path.exists(file_path):
            return -1
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"读取文件失败 {file_path}: {e}")
            return -1
        # 按段落分割
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        count = 0
        for para in paragraphs:
            doc_id = self.add_document(para, metadata={"source": os.path.basename(file_path)})
            if doc_id > 0:
                count += 1
        return count

    def import_from_directory(self, dir_path: str) -> int:
        """从目录批量导入知识（C3修复：限制在知识库目录内）"""
        # 路径白名单检查
        allowed_root = os.path.join(BASE_DIR, "scu2_data")
        abs_path = os.path.abspath(dir_path)
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
                if count > 0:
                    total += count
        logger.info(f"从 {abs_path} 导入 {total} 个文档")
        return total

    # ==================== 辅助方法 ====================

    def _get_doc_by_id(self, doc_id: int) -> Optional[Dict]:
        """根据ID获取文档"""
        for d in self._documents:
            if d["id"] == doc_id:
                return d
        return None

    # ==================== 持久化 ====================

    def _save(self) -> None:
        """持久化文档元数据"""
        try:
            os.makedirs(self.store_dir, exist_ok=True)
            data = {
                "documents": [
                    {k: v for k, v in d.items() if k != "tokens" or True}
                    for d in self._documents
                ],
                "next_id": self._next_id,
                "config": {
                    "vector_dim": self.vector_dim,
                    "embed_backend": self._embed_backend,
                    "storage_backend": self._storage_backend,
                    "embedding_model": self.embedding_model_name,
                },
                "updated_at": datetime.now().isoformat(),
            }
            with open(self.documents_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # FAISS索引持久化（处理Unicode路径问题）
            if self._storage_backend == "faiss" and self._faiss_index is not None and self._faiss_index.ntotal > 0:
                self._faiss_save_unicode(self._faiss_index, self.faiss_index_path)
        except Exception as e:
            logger.warning(f"向量库持久化失败: {e}")

    def _faiss_save_unicode(self, index, target_path: str) -> None:
        """FAISS索引保存（兼容Unicode路径）

        FAISS C++库在Windows上无法处理含非ASCII字符的路径，
        通过相对路径临时文件 + shutil.move 绕过。
        """
        import shutil
        import tempfile
        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            # 尝试直接写入（部分环境可成功）
            faiss.write_index(index, target_path)
        except Exception:
            # 降级：用相对路径临时文件写入，再移动到目标路径
            try:
                tmp_name = f".faiss_tmp_{int(time.time()*1000)}.bin"
                faiss.write_index(index, tmp_name)
                shutil.move(tmp_name, target_path)
            except Exception as e:
                logger.warning(f"FAISS索引写入失败（Unicode路径兼容方案）: {e}")

    def _faiss_load_unicode(self, path: str):
        """FAISS索引加载（兼容Unicode路径）"""
        import shutil
        import tempfile
        try:
            return faiss.read_index(path)
        except Exception:
            try:
                tmp_name = f".faiss_tmp_read_{int(time.time()*1000)}.bin"
                shutil.copy(path, tmp_name)
                idx = faiss.read_index(tmp_name)
                os.remove(tmp_name)
                return idx
            except Exception as e:
                logger.warning(f"FAISS索引加载失败（Unicode路径兼容方案）: {e}")
                return None

    def _save_embeddings_cache(self) -> None:
        """持久化嵌入缓存"""
        if not self._embeddings_cache:
            return
        try:
            os.makedirs(self.store_dir, exist_ok=True)
            with open(self.embeddings_cache_path, "w", encoding="utf-8") as f:
                json.dump(self._embeddings_cache, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"嵌入缓存持久化失败: {e}")

    def _load(self) -> None:
        """加载持久化数据"""
        # 1. 加载嵌入缓存
        if os.path.exists(self.embeddings_cache_path):
            try:
                with open(self.embeddings_cache_path, "r", encoding="utf-8") as f:
                    self._embeddings_cache = json.load(f)
                logger.info(f"加载嵌入缓存: {len(self._embeddings_cache)}条")
            except Exception as e:
                logger.warning(f"加载嵌入缓存失败: {e}")
                self._embeddings_cache = {}

        # 2. 加载文档元数据
        if not os.path.exists(self.documents_path):
            return
        try:
            with open(self.documents_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._next_id = data.get("next_id", 1)
            self._documents = data.get("documents", [])

            # 重建tokens（旧数据可能未保存tokens）
            for d in self._documents:
                if "tokens" not in d or not d["tokens"]:
                    d["tokens"] = self._tokenize(d.get("content", ""))
                # 重建BM25索引
                self._bm25_update_doc(d["id"], d["tokens"])

            # 重建存储索引（向量需重新计算/从缓存读取）
            self._rebuild_storage_index()

            # 加载FAISS索引文件（若存在且匹配维度）
            if self._storage_backend == "faiss" and self._faiss_index is not None:
                if os.path.exists(self.faiss_index_path):
                    loaded = self._faiss_load_unicode(self.faiss_index_path)
                    if loaded is not None:
                        if loaded.d == self._faiss_index.d and loaded.ntotal == len(self._documents):
                            self._faiss_index = loaded
                            logger.info(f"FAISS索引加载: {loaded.ntotal}条向量")
                        else:
                            logger.info("FAISS索引维度/数量不匹配，保持重建结果")

            logger.info(f"向量库加载: {len(self._documents)}个文档, backend={self._storage_backend}/{self._embed_backend}")
        except Exception as e:
            logger.warning(f"向量库加载失败: {e}")

    # ==================== 兼容与迁移 ====================

    def from_legacy(self, knowledge_store) -> int:
        """从旧TF-IDF知识库迁移文档

        Args:
            knowledge_store: KnowledgeStore 实例

        Returns:
            迁移的文档数
        """
        try:
            legacy_docs = knowledge_store.list_documents(limit=100000)
            count = 0
            for d in legacy_docs:
                # 旧接口只返回截断内容，需要从内部_documents读取完整内容
                pass
            # 直接读取内部_documents以获取完整内容
            for d in getattr(knowledge_store, "_documents", []):
                content = d.get("content", "")
                metadata = d.get("metadata", {})
                metadata.setdefault("migrated_from", "tfidf")
                doc_id = self.add_document(content, metadata)
                if doc_id > 0:
                    count += 1
            logger.info(f"从TF-IDF知识库迁移 {count} 个文档")
            return count
        except Exception as e:
            logger.warning(f"从旧知识库迁移失败: {e}")
            return 0


# ==================== 全局单例 ====================

_vector_store: Optional[VectorKnowledgeStore] = None
_singleton_lock = threading.Lock()


def get_vector_store() -> VectorKnowledgeStore:
    """获取向量知识库单例"""
    global _vector_store
    if _vector_store is None:
        with _singleton_lock:
            if _vector_store is None:
                _vector_store = VectorKnowledgeStore()
    return _vector_store


def migrate_from_tfidf(legacy_store=None) -> int:
    """从TF-IDF知识库自动迁移

    Args:
        legacy_store: 旧KnowledgeStore实例，None则自动创建

    Returns:
        迁移的文档数
    """
    try:
        if legacy_store is None:
            # 延迟导入避免循环依赖
            from .knowledge_store import get_store
            legacy_store = get_store()
        vs = get_vector_store()
        return vs.from_legacy(legacy_store)
    except Exception as e:
        logger.warning(f"TF-IDF自动迁移失败: {e}")
        return 0


# ==================== 自测入口 ====================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print("=" * 60)
    print("向量知识库自测")
    print("=" * 60)

    store = VectorKnowledgeStore()
    print("\n[状态]")
    print(json.dumps(store.get_status(), ensure_ascii=False, indent=2))

    print("\n[添加文档]")
    docs = [
        "SCU2是一个自洽认知智能体系统，采用三层架构。",
        "D层是底层约束层，包含公理、契约和账本。",
        "W1层是感知与记忆层，负责RAG检索和上下文管理。",
        "W2层是感知层，处理多模态输入。",
        "M层是执行层，包含任务规划、工具链和LLM调用。",
    ]
    for d in docs:
        did = store.add_document(d, metadata={"test": True})
        print(f"  添加 #{did}: {d[:30]}...")

    print("\n[搜索测试]")
    results = store.search("SCU2的架构是怎样的？", top_k=3)
    for r in results:
        print(f"  #{r['id']} score={r['score']} vec={r['vector_score']} kw={r['keyword_score']} | {r['content'][:40]}")

    print("\n[上下文获取]")
    ctx = store.get_context("RAG检索在哪里？", max_length=300)
    print(ctx)

    print("\n[最终状态]")
    print(json.dumps(store.get_status(), ensure_ascii=False, indent=2))
