# -*- coding: utf-8 -*-
"""v5.0向量库后端升级验证：FAISS + SBERT + jieba"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("v5.0向量库后端升级验证：FAISS + SBERT + jieba")
print("=" * 60)

# 1. 依赖检查
print("\n[1] 依赖检查")
import faiss
import sentence_transformers
import jieba
import numpy as np
print(f"  faiss: {faiss.__version__}")
print(f"  sentence-transformers: {sentence_transformers.__version__}")
print(f"  jieba: {jieba.__version__}")
print(f"  numpy: {np.__version__}")

# 2. 向量库后端检测
print("\n[2] 向量库后端检测（需清空单例缓存）")
# 清空模块级单例
from w1_layer import vector_store as vs_mod
vs_mod._vector_store = None

# 重新导入并实例化
from w1_layer.vector_store import VectorKnowledgeStore
store = VectorKnowledgeStore()
print(f"  存储后端: {store._storage_backend}")
print(f"  嵌入后端: {store._embed_backend}")
print(f"  向量维度: {store.vector_dim}")
assert store._storage_backend == "faiss", f"存储后端应为faiss, 实际为{store._storage_backend}"
assert store._embed_backend == "sbert", f"嵌入后端应为sbert, 实际为{store._embed_backend}"
print("  后端升级验证: PASS")

# 3. 清空并添加测试文档
print("\n[3] 添加测试文档")
store.clear()
docs = [
    ("SCU3是一个基于CUF架构的智能Agent系统，支持三维度分离设计", "架构"),
    ("向量数据库使用FAISS进行高效相似度检索，支持混合检索", "向量"),
    ("Qwen-7B是通义千问的7B参数本地大语言模型，支持中文对话", "模型"),
    ("BM25是一种基于词频的文本检索算法，常用于搜索引擎", "算法"),
    ("RAG通过检索增强生成，结合知识库和LLM提升回答质量", "RAG"),
]
for content, tag in docs:
    did = store.add_document(content, {"source": "test", "tag": tag})
    print(f"  #{did} [{tag}] {content[:30]}...")

# 4. 检索质量对比测试
print("\n[4] 检索质量测试（语义+关键词混合）")
queries = [
    ("Agent架构设计", "架构"),
    ("向量检索技术", "向量"),
    ("本地大模型", "模型"),
    ("文本搜索算法", "算法"),
    ("检索增强生成", "RAG"),
]
correct = 0
total = len(queries)
for query, expected_tag in queries:
    results = store.search(query, top_k=1, threshold=0.0)
    if results:
        top = results[0]
        actual_tag = top.get("metadata", {}).get("tag", "")
        is_correct = actual_tag == expected_tag
        status = "OK" if is_correct else "MISS"
        print(f"  [{status}] '{query}' -> tag={actual_tag}(expect={expected_tag}), "
              f"score={top['score']}, vec={top.get('vector_score',0)}, kw={top.get('keyword_score',0)}")
        if is_correct:
            correct += 1
    else:
        print(f"  [FAIL] '{query}' -> 无结果")

accuracy = correct / total
print(f"\n  检索准确率: {correct}/{total} = {accuracy*100:.1f}%")

# 5. FAISS索引状态
print("\n[5] FAISS索引状态")
if store._faiss_index is not None:
    print(f"  索引类型: {type(store._faiss_index).__name__}")
    print(f"  向量数量: {store._faiss_index.ntotal}")
    print(f"  向量维度: {store._faiss_index.d}")
else:
    print("  FAISS索引未初始化")

# 6. 性能测试
print("\n[6] 性能测试")
import time
# 嵌入计算耗时
t0 = time.time()
for _ in range(10):
    store._embed("性能测试查询语句")
embed_time = (time.time() - t0) / 10 * 1000
print(f"  嵌入计算平均耗时: {embed_time:.2f}ms")

# 检索耗时
t0 = time.time()
for _ in range(20):
    store.search("测试查询", top_k=3)
search_time = (time.time() - t0) / 20 * 1000
print(f"  检索平均耗时: {search_time:.2f}ms")

# 7. jieba分词验证
print("\n[7] jieba中文分词")
tokens = store._tokenize("SCU3是一个基于CUF架构的智能Agent系统")
print(f"  分词结果: {tokens[:10]}...")
assert "SCU3" in tokens or "SCU3" in tokens or "架构" in tokens, "jieba分词异常"
print("  jieba分词: OK")

# 8. 持久化验证
print("\n[8] 持久化验证")
status = store.get_status()
print(f"  文档总数: {status['total_documents']}")
print(f"  缓存大小: {status['cache_size']}")
print(f"  词汇表大小: {status['vocabulary_size']}")
print(f"  持久化目录: {status['store_dir']}")

import os
faiss_path = os.path.join(status['store_dir'], 'faiss_index.bin')
docs_path = os.path.join(status['store_dir'], 'documents.json')
cache_path = os.path.join(status['store_dir'], 'embeddings_cache.json')
print(f"  faiss_index.bin: {'存在' if os.path.exists(faiss_path) else '不存在'} ({os.path.getsize(faiss_path) if os.path.exists(faiss_path) else 0} bytes)")
print(f"  documents.json: {'存在' if os.path.exists(docs_path) else '不存在'} ({os.path.getsize(docs_path) if os.path.exists(docs_path) else 0} bytes)")
print(f"  embeddings_cache.json: {'存在' if os.path.exists(cache_path) else '不存在'} ({os.path.getsize(cache_path) if os.path.exists(cache_path) else 0} bytes)")

print("\n" + "=" * 60)
print(f"向量库后端升级验证完成！准确率={accuracy*100:.1f}%")
print("=" * 60)
