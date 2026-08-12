# -*- coding: utf-8 -*-
"""api/ledger.py — 账本与 CUF 守卫状态路由

从 server.py 抽取的 4 个路由：
  GET  /cuf/activity       — CUF守卫活动记录
  GET  /cuf/check          — CUF守卫状态检查
  POST /ledger/replenish   — 熵税账本充值
  GET  /ledger/balance     — 查询熵税余额
"""
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key, get

logger = logging.getLogger("SCU3.api.ledger")

router = APIRouter(tags=["ledger"])


class LedgerReplenishRequest(BaseModel):
    amount: float
    auth_token: str = ""
    reason: str = ""


@router.get("/cuf/activity")
async def cuf_activity(limit: int = 50, api_key: str = Depends(verify_api_key)):
    """CUF守卫活动记录"""
    ledger = get("ledger")
    try:
        history = ledger.history(limit)
        events = []
        for h in history:
            events.append({
                "ts": h.get("timestamp", ""),
                "tool": h.get("action", ""),
                "action": h.get("action", ""),
                "allowed": h.get("allowed", True),
                "axioms": [],
            })
        return JSONResponse({"success": True, "data": {
            "balance": round(ledger.balance(), 4),
            "total": len(events),
            "events": events,
        }})
    except Exception as e:
        logger.debug(f"CUF活动查询异常: {e}")
        return JSONResponse({"success": True, "data": {
            "balance": round(ledger.balance(), 4), "total": 0, "events": []}})


@router.get("/cuf/check")
async def cuf_check(api_key: str = Depends(verify_api_key)):
    """CUF守卫状态检查"""
    ledger = get("ledger")
    whitelist = get("whitelist")
    bal = ledger.balance()
    return JSONResponse({"success": True, "data": {
        "guard_active": True,
        "balance": round(bal, 4),
        "whitelist_count": len(whitelist.list_all()),
        "stats": ledger.stats(),
        "balance_warning": bal < 50.0,
    }})


@router.post("/ledger/replenish")
async def ledger_replenish(req: LedgerReplenishRequest,
                           api_key: str = Depends(verify_admin_key)):
    """熵税账本充值端点（管理员权限 + 独立 auth_token 双重鉴权）

    解决余额耗尽导致系统功能性阻塞的问题。
    - 单笔上限 1000E（MAX_SINGLE_TRANSACTION）
    - 需 SCU3_ADMIN_API_KEY + SCU3_LEDGER_AUTH 双重鉴权
    """
    ledger = get("ledger")
    ok, msg = ledger.replenish(req.amount, req.auth_token, req.reason)
    return JSONResponse({"success": ok, "message": msg,
                         "balance": round(ledger.balance(), 4)})


@router.get("/ledger/balance")
async def ledger_balance(api_key: str = Depends(verify_api_key)):
    """查询熵税余额（普通权限可查）"""
    ledger = get("ledger")
    bal = ledger.balance()
    return JSONResponse({"success": True, "data": {
        "balance": round(bal, 4),
        "warning": bal < 50.0,
        "critical": bal < 10.0,
    }})
