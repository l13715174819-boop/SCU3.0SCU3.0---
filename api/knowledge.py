# -*- coding: utf-8 -*-
"""api/knowledge.py — 知识库与向量数据库路由

从 server.py 抽取的 9 个知识库/向量路由：
  POST   /knowledge/add       — 添加知识文档
  POST   /knowledge/import    — 从目录批量导入知识（管理员）
  GET    /knowledge/search    — 检索知识
  GET    /knowledge/list      — 列出知识文档
  GET    /knowledge/status    — 知识库状态
  DELETE /knowledge/{doc_id}  — 删除知识文档（管理员）
  GET    /vector/status       — 向量知识库状态
  POST   /vector/search       — 向量搜索（混合检索：向量+关键词）
  POST   /vector/migrate      — 从TF-IDF迁移到向量知识库（管理员）
"""
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key

logger = logging.getLogger("SCU3.api.knowledge")

router = APIRouter(tags=["knowledge"])


# ─── 请求模型 ────────────────────────────────
class KnowledgeRequest(BaseModel):
    content: str
    source: str = "api"


class VectorSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    threshold: float = 0.3


# ─── 知识库管理 ────────────────────────────────
@router.post("/knowledge/add")
async def knowledge_add(req: KnowledgeRequest, api_key: str = Depends(verify_api_key)):
    """添加知识文档"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    doc_id = store.add_document(req.content, metadata={"source": req.source})
    return JSONResponse({"success": doc_id > 0, "doc_id": doc_id})


@router.post("/knowledge/import")
async def knowledge_import(req: dict, api_key: str = Depends(verify_admin_key)):
    """从目录批量导入知识（C3+C4修复：需管理员权限+路径限制）"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    dir_path = req.get("dir_path", "")
    count = store.import_from_directory(dir_path)
    return JSONResponse({"success": True, "imported": count})


@router.get("/knowledge/search")
async def knowledge_search(q: str = "", top_k: int = 3, api_key: str = Depends(verify_api_key)):
    """检索知识"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    results = store.search(q, top_k=top_k)
    return JSONResponse({"results": results})


@router.get("/knowledge/list")
async def knowledge_list(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """列出知识文档"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    return JSONResponse({"documents": store.list_documents(limit)})


@router.get("/knowledge/status")
async def knowledge_status(api_key: str = Depends(verify_api_key)):
    """知识库状态"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    return JSONResponse(store.get_status())


@router.delete("/knowledge/{doc_id}")
async def knowledge_delete(doc_id: int, api_key: str = Depends(verify_admin_key)):
    """删除知识文档"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    ok = store.delete_document(doc_id)
    return JSONResponse({"success": ok})


# ─── 向量数据库（v5.0优化） ────────────────────────────────
@router.get("/vector/status")
async def vector_status(api_key: str = Depends(verify_api_key)):
    """向量知识库状态"""
    try:
        from w1_layer.knowledge_store import get_store
        store = get_store()
        status = store.get_status()
        # 检查是否为向量版本
        is_vector = "vector_store" in str(type(store).__name__).lower() or \
                     "backend" in status or "embedding" in str(status).lower()
        return JSONResponse({
            "success": True,
            "is_vector": is_vector,
            "store_type": type(store).__name__,
            "status": status,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/vector/search")
async def vector_search(req: VectorSearchRequest, api_key: str = Depends(verify_api_key)):
    """向量搜索（混合检索：向量+关键词）"""
    try:
        from w1_layer.knowledge_store import get_store
        store = get_store()
        results = store.search(req.query, top_k=req.top_k, threshold=req.threshold)
        return JSONResponse({"success": True, "results": results, "count": len(results)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/vector/migrate")
async def vector_migrate(api_key: str = Depends(verify_admin_key)):
    """从TF-IDF迁移到向量知识库（需管理员）"""
    try:
        from w1_layer.vector_store import migrate_from_tfidf
        count = migrate_from_tfidf()
        return JSONResponse({"success": True, "migrated": count})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
