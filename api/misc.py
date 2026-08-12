# -*- coding: utf-8 -*-
"""api/misc.py — 杂项路由

从 server.py 抽取的 6 个杂项路由：
  POST   /feedback             — 反馈收集
  POST   /whitelist/add        — 添加白名单（管理员）
  GET    /whitelist/list       — 列出白名单（管理员）
  POST   /audit/daily          — 触发每日审计（管理员）
  GET    /pair/status          — 阴阳对子状态（无认证探活）
  GET    /cognition/yin-yang   — 阴阳对子思考状态
"""
import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key, get

logger = logging.getLogger("SCU3.api.misc")

router = APIRouter(tags=["misc"])


# ─── 请求模型 ────────────────────────────────
class FeedbackRequest(BaseModel):
    kind: str
    pattern_key: str
    user_id: str = "default_user"


class WhitelistRequest(BaseModel):
    action: str
    source: str
    target: str
    contracts: Dict[str, Any]
    code_hash: str = ""
    ttl_hours: float = 24.0


# 默认阴阳对子状态（无活跃对子时）
_DEFAULT_YIN_YANG_STATE: Dict[str, Any] = {
    "active": False,
    "gamma_yin": 0.0,
    "gamma_yang": 0.0,
    "endorsed": False,
    "timestamp": None,
    "yin_api": "DeepSeek-Chat",
    "yang_api": "Qwen-Plus",
}


# ─── 反馈收集 ────────────────────────────────
@router.post("/feedback")
async def feedback_endpoint(req: FeedbackRequest, api_key: str = Depends(verify_api_key)):
    feedback = get("feedback")
    result = feedback.collect(req.user_id, req.pattern_key, req.kind)
    return JSONResponse({"success": "error" not in result, "data": result})


# ─── 白名单管理 ────────────────────────────────
@router.post("/whitelist/add")
async def whitelist_add(req: WhitelistRequest, api_key: str = Depends(verify_admin_key)):
    whitelist = get("whitelist")
    ok, msg = whitelist.add(
        action=req.action, source=req.source, target=req.target,
        contracts=req.contracts, code_hash=req.code_hash,
        ttl_hours=req.ttl_hours,
    )
    return JSONResponse({"success": ok, "msg": msg})


@router.get("/whitelist/list")
async def whitelist_list(api_key: str = Depends(verify_admin_key)):
    whitelist = get("whitelist")
    return JSONResponse({"entries": whitelist.list_all()})


# ─── 审计 ────────────────────────────────
@router.post("/audit/daily")
async def daily_audit(force: bool = False, api_key: str = Depends(verify_admin_key)):
    metacog = get("metacog")
    result = metacog.force_audit() if force else metacog.daily_audit()
    return JSONResponse(result)


# ─── 顶栏探活端点（无认证，供前端状态栏轮询） ────────────────────────────────
@router.get("/pair/status")
async def pair_status():
    """阴阳对子（Yin-Yang Pair）状态

    对子为高风险操作时临时实例化的双背书机制，非常驻服务。
    无活跃对子时返回 enabled=false。
    """
    return JSONResponse({
        "success": True,
        "data": {
            "enabled": False,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "pending_human": 0,
            "avg_gamma_yin": 0.0,
            "avg_gamma_yang": 0.0,
        },
    })


@router.get("/cognition/yin-yang")
async def cognition_yin_yang_status():
    """阴阳对子思考状态（方案C）

    返回最近一次 analytical 意图触发的阴阳双签状态，
    供前端太极图动态展示。
    """
    # 从全局获取最近一次阴阳对子状态（由 server.py process_request 更新）
    state = get("_last_yin_yang_state")
    if state is None:
        state = _DEFAULT_YIN_YANG_STATE
    return JSONResponse({
        "success": True,
        "data": state,
        "threshold": {
            "yin": 0.75,
            "yang": 0.65,
        },
    })
