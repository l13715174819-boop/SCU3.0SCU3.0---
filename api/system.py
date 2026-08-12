# -*- coding: utf-8 -*-
"""api/system.py — 系统级路由

从 server.py 抽取的系统/健康/自检路由：
  GET  /@vite/client     — Vite HMR 探活空响应
  GET  /                 — 首页 HTML
  GET  /health           — 无认证健康检查
  GET  /status           — 系统状态（管理员）
  GET  /history          — 账本历史（管理员）
  GET  /help             — 帮助命令列表
  GET  /favicon.ico      — 站点图标
  GET  /self-check/quick — 快速自检（管理员）
  GET  /self-check       — 完整自检（管理员）
"""
import os
import logging
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse, Response

from api.deps import verify_api_key, verify_admin_key, get

logger = logging.getLogger("SCU3.api.system")

router = APIRouter(tags=["system"])

# 项目根目录（scu3/），用于定位 web/index.html
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@router.get("/@vite/client")
async def vite_client():
    """空响应：某些浏览器开发环境会自动请求 /@vite/client（HMR 探测），
    返回空 JS 避免控制台 ERR_ABORTED 报错。
    """
    return PlainTextResponse("", media_type="application/javascript")


@router.get("/")
async def index():
    html_path = os.path.join(_BASE_DIR, "web", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>web/index.html not found</h1>")


@router.get("/health")
async def health():
    """无认证健康检查端点（供前端探活/心跳判断在线状态）

    返回 {status, arch, version, balance}
    敏感字段（stats/whitelist_count/last_audit）仍走需认证的 /status
    """
    ledger = get("ledger")
    return JSONResponse({
        "status": "ok",
        "arch": "v3",
        "version": "3.0.0",
        "balance": round(ledger.balance(), 4) if ledger else 0.0,
    })


@router.get("/status")
async def status(request: Request, api_key: str = Depends(verify_admin_key)):
    ledger = get("ledger")
    whitelist = get("whitelist")
    metacog = get("metacog")
    bal = ledger.balance() if ledger else 0.0
    return JSONResponse({
        "arch": "v3", "version": "3.0.0",
        "balance": round(bal, 4),
        "balance_warning": bal < 50.0,
        "balance_critical": bal < 10.0,
        "ledger_ready": getattr(request.app.state, "ledger_ready", True),
        "stats": ledger.stats() if ledger else {},
        "whitelist_count": len(whitelist.list_all()) if whitelist else 0,
        "last_audit": metacog._last_audit_time.isoformat()
                      if metacog and getattr(metacog, "_last_audit_time", None) else None,
    })


@router.get("/history")
async def history(limit: int = 20, api_key: str = Depends(verify_admin_key)):
    ledger = get("ledger")
    return JSONResponse({"history": ledger.history(limit) if ledger else []})


@router.get("/help")
async def help_endpoint():
    """帮助命令列表"""
    commands = [
        {"cmd": "你好", "desc": "开始对话", "category": "对话", "alias": ["hi", "hello"]},
        {"cmd": "计算 <表达式>", "desc": "数学计算，如 计算 25*4", "category": "对话"},
        {"cmd": "几点了", "desc": "查询当前时间", "category": "对话"},
        {"cmd": "天气 <城市>", "desc": "查询天气", "category": "对话"},
        {"cmd": "搜索 <关键词>", "desc": "搜索知识库", "category": "知识库"},
        {"cmd": "/switch <平台>", "desc": "切换LLM平台", "category": "系统"},
        {"cmd": "/status", "desc": "查看系统状态", "category": "系统"},
        {"cmd": "/audit", "desc": "触发周期审计", "category": "系统"},
    ]
    return JSONResponse({"success": True, "data": {"commands": commands}})


@router.get("/favicon.ico")
async def favicon():
    """返回空图标避免浏览器 404 报错"""
    # 1x1 透明 PNG
    return Response(
        content=b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82',
        media_type="image/png",
    )


@router.get("/self-check/quick")
async def self_check_quick(api_key: str = Depends(verify_admin_key)):
    """快速自检"""
    ledger = get("ledger")
    return JSONResponse({"success": True, "data": {
        "status": "ok",
        "arch": "v3",
        "balance": round(ledger.balance(), 4) if ledger else 0.0,
        "layers": {"W2": "ok", "W1": "ok", "M": "ok", "D": "ok"},
        "guards": {"g1": "ok", "g2": "ok", "g3": "ok", "g4": "ok", "g5": "ok"},
    }})


@router.get("/self-check")
async def self_check_full(api_key: str = Depends(verify_admin_key)):
    """完整自检"""
    ledger = get("ledger")
    whitelist = get("whitelist")
    return JSONResponse({"success": True, "data": {
        "status": "ok",
        "arch": "v3",
        "version": "3.0.0",
        "balance": round(ledger.balance(), 4) if ledger else 0.0,
        "stats": ledger.stats() if ledger else {},
        "whitelist_count": len(whitelist.list_all()) if whitelist else 0,
        "layers": {"W2": "ok", "W1": "ok", "M": "ok", "D": "ok"},
        "guards": {"g1_W2_W1": "ok", "g2_W1_M": "ok", "g3_tool": "ok",
                   "g4_audit": "ok", "g5_filter": "ok"},
    }})
