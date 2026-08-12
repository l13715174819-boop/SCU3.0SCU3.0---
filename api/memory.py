# -*- coding: utf-8 -*-
"""api/memory.py — 三级记忆管理路由

从 server.py 抽取的 7 个记忆管理路由：
  GET    /memory/stats                — 三级记忆统计
  GET    /memory/health               — 三级记忆健康检查
  GET    /memory/search               — 跨层检索记忆
  POST   /memory/episode              — 保存情景到 L3
  POST   /memory/knowledge            — 保存知识到 L2
  DELETE /memory/{layer}/{item_id}    — 遗忘指定记忆
  POST   /memory/clear-l1             — 清空工作记忆（管理员）
"""
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.deps import verify_api_key, verify_admin_key, get

logger = logging.getLogger("SCU3.api.memory")

router = APIRouter(tags=["memory"])


@router.get("/memory/stats")
async def memory_stats(api_key: str = Depends(verify_api_key)):
    """三级记忆统计"""
    try:
        memory = get("memory")
        return JSONResponse({"success": True, "data": memory.stats() if memory else {}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/memory/health")
async def memory_health(api_key: str = Depends(verify_api_key)):
    """三级记忆健康检查"""
    try:
        memory = get("memory")
        return JSONResponse({"success": True, "data": memory.health() if memory else {}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/memory/search")
async def memory_search(query: str, layers: str = "L1,L2,L3",
                        top_k: int = 5, category: str = "",
                        api_key: str = Depends(verify_api_key)):
    """跨层检索记忆

    Args:
        query: 查询文本
        layers: 检索层级，逗号分隔（L1,L2,L3）
        top_k: 每层返回条数
        category: L2 类别过滤（可选）
    """
    try:
        memory = get("memory")
        layer_list = [l.strip() for l in layers.split(",") if l.strip()]
        result = memory.search_cross_layer(
            query, layers=layer_list, top_k=top_k,
            **({"category": category} if category else {})
        )
        return JSONResponse({"success": True, "data": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/memory/episode")
async def memory_save_episode(req: dict, api_key: str = Depends(verify_api_key)):
    """保存情景到 L3（任务轨迹/反思/决策）"""
    try:
        memory = get("memory")
        eid = memory.save_episode(
            event_type=str(req.get("event_type", "task")),
            task_desc=str(req.get("task_desc", ""))[:500],
            steps=req.get("steps", []),
            result=str(req.get("result", ""))[:1000],
            success=bool(req.get("success", True)),
            reflection=str(req.get("reflection", ""))[:1000],
        )
        return JSONResponse({"success": True, "data": {"id": eid}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/memory/knowledge")
async def memory_save_knowledge(req: dict, api_key: str = Depends(verify_api_key)):
    """保存知识到 L2（语义记忆）"""
    try:
        memory = get("memory")
        kid = memory.save_knowledge(
            content=str(req.get("content", ""))[:2000],
            source=str(req.get("source", "manual")),
            category=str(req.get("category", "general")),
            score=float(req.get("score", 0.7)),
            tags=req.get("tags", []),
        )
        return JSONResponse({"success": True, "data": {"id": kid}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.delete("/memory/{layer}/{item_id}")
async def memory_forget(layer: str, item_id: str,
                        api_key: str = Depends(verify_api_key)):
    """遗忘指定记忆

    Args:
        layer: L1 / L2 / L3
        item_id: 记忆条目 ID
    """
    try:
        memory = get("memory")
        deleted = memory.forget(layer, item_id)
        return JSONResponse({"success": deleted, "data": {"deleted": deleted}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/memory/clear-l1")
async def memory_clear_l1(api_key: str = Depends(verify_admin_key)):
    """清空工作记忆（需管理员权限）"""
    try:
        memory = get("memory")
        memory.clear_l1()
        return JSONResponse({"success": True, "data": {"cleared": True}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
