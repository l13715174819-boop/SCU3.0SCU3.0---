# -*- coding: utf-8 -*-
"""验证知识库检索（低阈值）"""
import urllib.request, urllib.parse, json

API_BASE = "http://localhost:8000"
HEADERS = {"X-API-Key": "SCU3_dev_key_2026"}

queries = [
    "v5.0性能对比",
    "部署手册安装步骤",
    "FAISS Unicode修复",
    "本地模型量化策略",
    "API端点清单",
    "故障排查",
]

print("知识库检索验证（threshold=0.1）:")
for q in queries:
    url = f"{API_BASE}/knowledge/search?q={urllib.parse.quote(q)}&top_k=2"
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))
        results = d.get("results", [])
        if results:
            top = results[0]
            tag = top.get("metadata", {}).get("tag", "")
            score = top.get("score", 0)
            vec = top.get("vector_score", 0)
            kw = top.get("keyword_score", 0)
            print(f"  [OK] {q} -> score={score}, vec={vec}, kw={kw}, tag={tag}")
        else:
            print(f"  [MISS] {q}")
