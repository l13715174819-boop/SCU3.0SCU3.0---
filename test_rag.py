# -*- coding: utf-8 -*-
"""RAG知识库测试 — 验证TF-IDF修复"""
import sys
sys.path.insert(0, '.')
from w1_layer.knowledge_store import KnowledgeStore

# 清空旧数据重新测试
ks = KnowledgeStore()
ks.clear()

# 添加测试文档
docs = [
    "SCU3是标准计算单元2，采用v3架构，三维度分离",
    "CUF守卫层负责跨层审计，按五维熵税计费",
    "DeepSeek API支持流式和非流式调用",
    "RAG知识库使用TF-IDF向量化实现轻量级检索",
    "工具守卫对每种工具标注tool_type并执行沙箱隔离",
]
for d in docs:
    ks.add_document(d)

print(f"文档数: {len(ks._documents)}")
for d in ks._documents:
    nonzero = sum(1 for v in d["tfidf"].values() if v > 0)
    print(f"  #{d['id']} tfidf非零项={nonzero} content={d['content'][:30]}")

# 测试搜索
queries = ["SCU3架构", "标准计算单元", "跨层审计", "DeepSeek流式", "知识库检索"]
for q in queries:
    results = ks.search(q, top_k=3, threshold=0.05)
    print(f"\n搜索 '{q}': {len(results)}条")
    for r in results:
        print(f"  #{r['id']} score={r['score']} content={r['content'][:40]}")

# 测试get_context
ctx = ks.get_context("SCU3架构是什么样的")
print(f"\n上下文: {ctx[:200]}")
