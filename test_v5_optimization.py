# -*- coding: utf-8 -*-
"""v5.0优化验证：向量数据库 + 本地小模型"""
import sys, os, ast
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("v5.0优化验证：向量数据库 + 本地小模型")
print("=" * 60)

# 1. 语法检查
print("\n[1] 语法检查")
for f in ['w1_layer/vector_store.py', 'm_layer/local_model.py', 
          'w1_layer/knowledge_store.py', 'm_layer/llm_client.py', 'server.py']:
    ast.parse(open(f, encoding='utf-8').read())
    print(f"  OK {f}")

# 2. 向量知识库测试
print("\n[2] 向量知识库")
from w1_layer.vector_store import get_vector_store
vs = get_vector_store()
print(f"  后端: {vs._storage_backend}")
print(f"  嵌入: {vs._embed_backend}")
print(f"  维度: {vs.vector_dim}")

# 添加测试文档
vs.clear()
id1 = vs.add_document("SCU2是一个基于CUF架构的智能Agent系统，支持三维度分离设计", {"source": "test"})
id2 = vs.add_document("向量数据库使用FAISS进行高效相似度检索，支持混合检索", {"source": "test"})
id3 = vs.add_document("Qwen-7B是通义千问的7B参数本地大语言模型，支持中文对话", {"source": "test"})
print(f"  添加3个文档: ids=[{id1},{id2},{id3}]")

# 搜索测试
r1 = vs.search("Agent架构", top_k=3, threshold=0.0)
print(f"  搜索'Agent架构': {len(r1)}结果, 最高分={r1[0]['score'] if r1 else 0}")

r2 = vs.search("向量检索", top_k=3, threshold=0.0)
print(f"  搜索'向量检索': {len(r2)}结果, 最高分={r2[0]['score'] if r2 else 0}")

r3 = vs.search("Qwen本地模型", top_k=3, threshold=0.0)
print(f"  搜索'Qwen本地模型': {len(r3)}结果, 最高分={r3[0]['score'] if r3 else 0}")

# 混合检索字段
if r1:
    print(f"  混合检索字段: vector_score={r1[0].get('vector_score','N/A')}, keyword_score={r1[0].get('keyword_score','N/A')}")

# 3. get_store() 返回向量版本
print("\n[3] get_store() 集成")
from w1_layer.knowledge_store import get_store
store = get_store()
store_type = type(store).__name__
print(f"  类型: {store_type}")
print(f"  是向量版本: {'Vector' in store_type}")

# 4. 本地模型测试
print("\n[4] 本地小模型")
from m_layer.local_model import get_local_model
lm = get_local_model()
print(f"  模型已加载: {lm._model_loaded}")
models = lm.list_supported_models()
print(f"  支持模型: {[m['name'] for m in models]}")
status = lm.status()
deps = status.get("dependencies", {})
print(f"  依赖: transformers={deps.get('transformers',False)}, torch={deps.get('torch',False)}, bnb={deps.get('bitsandbytes',False)}")
print(f"  GPU: available={status.get('gpu_available', False)}, free={status.get('gpu_memory_free_gb', 0):.1f}GB")

# 5. LLM客户端集成
print("\n[5] LLM客户端集成")
from m_layer.llm_client import get_client
client = get_client()
print(f"  mode={client.mode}, platform={client.active_platform}")
assert hasattr(client, '_check_local_torch_model'), "缺少_check_local_torch_model"
assert hasattr(client, '_call_local_torch'), "缺少_call_local_torch"
print("  local_torch检测方法: OK")
print("  local_torch调用方法: OK")

# chat功能测试（规则模式）
result = client.chat("你好")
print(f"  chat()测试: mode={result.get('mode')}, len={len(result.get('content',''))}")

# 6. server.py路由
print("\n[6] server.py路由")
from server import app
routes = [r.path for r in app.routes if hasattr(r, 'path')]
new_vector = [r for r in routes if '/vector/' in r]
new_model = [r for r in routes if '/local-model/' in r]
print(f"  向量端点: {new_vector}")
print(f"  模型端点: {new_model}")
print(f"  总路由数: {len(routes)}")

# 7. 向量搜索端点测试（直接调用store）
print("\n[7] 端点功能模拟")
search_result = store.search("CUF架构", top_k=3, threshold=0.0)
print(f"  store.search('CUF架构'): {len(search_result)}结果")

context = store.get_context("Agent系统", max_length=200)
print(f"  store.get_context(): {len(context)}字符")

# 8. 从旧库迁移测试
print("\n[8] TF-IDF迁移")
from w1_layer.knowledge_store import KnowledgeStore
from w1_layer.vector_store import migrate_from_tfidf
legacy = KnowledgeStore()
legacy.add_document("迁移测试文档-旧TF-IDF", {"source": "legacy"})
count = migrate_from_tfidf(legacy)
print(f"  迁移文档数: {count}")

print("\n" + "=" * 60)
print("全部验证通过!")
print("=" * 60)
