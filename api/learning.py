# -*- coding: utf-8 -*-
"""api/learning.py — 自学习闭环路由

从 server.py 抽取的 4 个自学习路由：
  POST /learning/run     — 手动触发自学习闭环（管理员）
  GET  /learning/status  — 自学习状态
  GET  /learning/history — 学习历史
  POST /learning/reset   — 重置提示词权重（管理员）
"""
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.deps import verify_api_key, verify_admin_key, get

logger = logging.getLogger("SCU3.api.learning")

router = APIRouter(tags=["learning"])


# ─── 自学习闭环 ────────────────────────────────
@router.post("/learning/run")
async def learning_run(force: bool = True, api_key: str = Depends(verify_admin_key)):
    """手动触发自学习闭环（需管理员权限）"""
    learning_engine = get("learning_engine")
    report = learning_engine.learn(force=True)
    return JSONResponse(report)


@router.get("/learning/status")
async def learning_status(api_key: str = Depends(verify_api_key)):
    """自学习状态"""
    learning_engine = get("learning_engine")
    return JSONResponse(learning_engine.get_status())


@router.get("/learning/history")
async def learning_history(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """学习历史"""
    learning_engine = get("learning_engine")
    return JSONResponse({"history": learning_engine.get_learning_history(limit)})


@router.post("/learning/reset")
async def learning_reset(api_key: str = Depends(verify_admin_key)):
    """重置提示词权重（回滚机制，需管理员权限）"""
    learning_engine = get("learning_engine")
    result = learning_engine.reset_weights()
    return JSONResponse({"success": True, "result": result})
