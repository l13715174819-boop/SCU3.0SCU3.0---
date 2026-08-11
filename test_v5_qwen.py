# -*- coding: utf-8 -*-
"""v5.0 Qwen2-7B 本地模型集成测试（使用 Qwen2.5-3B-Instruct 验证流程）

测试流程：
  1. 模型下载（首次需联网）
  2. 模型加载（4bit量化，GPU）
  3. 对话功能测试
  4. 流式生成测试
  5. 与向量库 RAG 集成测试
  6. LLMClient 自动检测测试
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置 HuggingFace 镜像（国内加速）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

print("=" * 60)
print("v5.0 Qwen2-7B 本地模型集成测试")
print("=" * 60)

# 1. 依赖检查
print("\n[1] 依赖检查")
import torch
from m_layer.local_model import get_local_model, LocalModelClient, SUPPORTED_MODELS
print(f"  torch: {torch.__version__}")
print(f"  CUDA: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"  GPU: {torch.cuda.get_device_name(0)}")
    free, total = torch.cuda.mem_get_info()
    print(f"  显存: {round(free/1024**3, 2)}GB free / {round(total/1024**3, 2)}GB total")

# 2. 模型加载
# 使用完整 model_id 加载 Qwen2.5-3B-Instruct（约6GB，4bit量化约2GB，适合16GB显存）
print("\n[2] 加载 Qwen2.5-3B-Instruct（4bit量化，验证流程）")
client = get_local_model()

# 检查是否已加载
if not client._model_loaded:
    MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"
    print(f"  开始加载 {MODEL_ID} (quantization=4bit, device=auto)...")
    print("  首次加载需下载模型，请耐心等待...")
    start = time.time()
    result = client.load_model(MODEL_ID, quantization="4bit", device="auto")
    load_time = time.time() - start
    print(f"  加载结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    print(f"  加载耗时: {load_time:.1f}s")
else:
    print(f"  模型已加载: {client._model_name}")

if not client._model_loaded:
    print("\n[FAIL] 模型未加载成功，终止测试")
    sys.exit(1)

# 3. 健康检查
print("\n[3] 健康检查")
healthy = client.health_check()
print(f"  健康检查: {'PASS' if healthy else 'FAIL'}")

# 4. 基础对话测试
print("\n[4] 基础对话测试")
test_prompts = [
    "你好，请用一句话介绍自己。",
    "什么是向量数据库？",
    "用Python写一个简单的hello world。",
]
for prompt in test_prompts:
    print(f"\n  用户: {prompt}")
    start = time.time()
    result = client.chat(prompt, max_tokens=200)
    elapsed = time.time() - start
    print(f"  助手: {result.get('content', '')[:200]}")
    print(f"  [tokens={result.get('tokens', 0)}, latency={result.get('latency', 0)}s, "
          f"total={elapsed:.2f}s, error={result.get('error')}]")

# 5. 流式生成测试
print("\n[5] 流式生成测试")
print("  用户: 讲一个关于AI的短故事（50字以内）")
print("  助手: ", end="", flush=True)
start = time.time()
total_chunks = 0
full_text = ""
for chunk in client.chat_stream("讲一个关于AI的短故事（50字以内）", max_tokens=100):
    print(chunk, end="", flush=True)
    full_text += chunk
    total_chunks += 1
elapsed = time.time() - start
print(f"\n  [chunks={total_chunks}, total={elapsed:.2f}s]")

# 6. RAG 集成测试
print("\n[6] RAG 集成测试（向量库 + 本地模型）")
from w1_layer.vector_store import get_vector_store
vs = get_vector_store()
print(f"  向量库: backend={vs._storage_backend}, embed={vs._embed_backend}, docs={len(vs._documents)}")

# 检索知识
query = "SCU2的架构是什么？"
context = vs.get_context(query, max_length=500)
print(f"  检索查询: {query}")
print(f"  检索上下文: {context[:150]}...")

# 带上下文调用本地模型
print(f"  本地模型RAG回答:")
start = time.time()
rag_result = client.chat(query, context=context, max_tokens=300)
elapsed = time.time() - start
print(f"  {rag_result.get('content', '')[:300]}")
print(f"  [tokens={rag_result.get('tokens', 0)}, latency={rag_result.get('latency', 0)}s, total={elapsed:.2f}s]")

# 7. LLMClient 自动检测测试
print("\n[7] LLMClient 自动检测测试")
# 设置环境变量让 LLMClient 能检测到本地模型
os.environ["LOCAL_MODEL_NAME"] = client._model_name or "qwen2.5-3b"
os.environ["LOCAL_MODEL_QUANT"] = "4bit"
os.environ["LOCAL_MODEL_DEVICE"] = "auto"

from m_layer.llm_client import LLMClient
test_client = LLMClient()
print(f"  mode: {test_client.mode}")
print(f"  active_platform: {test_client.active_platform}")

# 检测本地模型
local_torch = test_client._check_local_torch_model()
print(f"  _check_local_torch_model: {local_torch is not None}")
if local_torch:
    print(f"    id: {local_torch.get('id')}")
    print(f"    label: {local_torch.get('label')}")
    print(f"    model: {local_torch.get('model')}")

# 8. 状态报告
print("\n[8] 最终状态报告")
status = client.status()
print(json.dumps(status, ensure_ascii=False, indent=2))

# 9. 卸载模型（释放显存）
print("\n[9] 卸载模型")
unload_result = client.unload_model()
print(f"  卸载结果: {unload_result}")

print("\n" + "=" * 60)
print("Qwen2-7B 集成测试完成！")
print("=" * 60)
