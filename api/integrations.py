# -*- coding: utf-8 -*-
"""api/integrations.py — 集成应用路由

从 server.py 抽取的 16 个集成应用路由：
  GET  /experience/list       — 列出所有经验
  GET  /experience/status     — 经验存储状态
  POST /experience/test-match — 测试经验匹配
  GET  /evolution/status      — 自进化引擎状态
  POST /evolution/scan        — 手动触发自进化扫描（管理员）
  GET  /evolution/history     — 自进化扫描历史
  GET  /evolution/defects     — 查看当前缺陷列表
  GET  /mail/status           — 邮件状态（未集成）
  POST /mail/send             — 邮件发送（未集成）
  GET  /mail/inbox            — 邮件收件箱（未集成）
  GET  /calendar/list         — 日程列表（未集成）
  POST /calendar/add          — 添加日程（未集成）
  DELETE /calendar/remove     — 删除日程（未集成）
  GET  /news/category/{cat}   — 资讯分类（未集成）
  GET  /hot-search/{platform} — 热搜（未集成）
  GET  /code/proposals        — 代码自修改提案列表（管理员）
"""
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.deps import verify_api_key, verify_admin_key, get

logger = logging.getLogger("SCU3.api.integrations")

router = APIRouter(tags=["integrations"])


# ─── 经验存储 ────────────────────────────────
@router.get("/experience/list")
async def experience_list(mature: bool = False, api_key: str = Depends(verify_api_key)):
    """列出所有经验（GET /experience/list?mature=true 仅看成熟经验）"""
    try:
        from m_layer.experience_store import get_experience_store
        store = get_experience_store()
        return JSONResponse({"success": True, "data": store.list_experiences(mature)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/experience/status")
async def experience_status(api_key: str = Depends(verify_api_key)):
    """经验存储状态"""
    try:
        from m_layer.experience_store import get_experience_store
        store = get_experience_store()
        return JSONResponse({"success": True, "data": store.get_status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/experience/test-match")
async def experience_test_match(req: dict, api_key: str = Depends(verify_api_key)):
    """测试经验匹配（POST /experience/test-match {input: "读取 test.pdf", tool: "pdf_read"}）"""
    try:
        user_input = req.get("input", "")
        tool_name = req.get("tool", "")
        from m_layer.experience_store import get_experience_store
        store = get_experience_store()
        exp = store.match_experience(user_input, tool_name)
        if exp:
            return JSONResponse({"success": True, "matched": True, "experience": exp})
        return JSONResponse({"success": True, "matched": False, "experience": None})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 自进化引擎（自动总结不足+生成方案+提交审核） ────────────────────────────────
@router.get("/evolution/status")
async def evolution_status(api_key: str = Depends(verify_api_key)):
    """自进化引擎状态"""
    try:
        from m_layer.self_evolution import get_evolution_engine
        engine = get_evolution_engine()
        return JSONResponse({"success": True, "data": engine.get_status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/evolution/scan")
async def evolution_scan(api_key: str = Depends(verify_admin_key)):
    """手动触发自进化扫描（需管理员权限）

    流程：扫描缺陷 → LLM 生成方案 → 提交审核队列
    """
    try:
        from m_layer.self_evolution import get_evolution_engine
        engine = get_evolution_engine()
        report = engine.trigger_now()
        return JSONResponse({"success": True, "data": report})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/evolution/history")
async def evolution_history(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """自进化扫描历史"""
    try:
        from m_layer.self_evolution import get_evolution_engine
        engine = get_evolution_engine()
        return JSONResponse({"success": True, "data": engine.list_scan_history(limit)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/evolution/defects")
async def evolution_defects(api_key: str = Depends(verify_api_key)):
    """查看当前缺陷列表（扫描但不提交方案）"""
    try:
        from m_layer.self_evolution import DefectAnalyzer
        analyzer = DefectAnalyzer()
        defects = analyzer.scan()
        return JSONResponse({"success": True, "data": defects, "total": len(defects)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 邮件/日历（未集成，返回未配置状态） ────────────────────────────────
@router.get("/mail/status")
async def mail_status():
    return JSONResponse({"success": True, "data": {
        "mail_send_ready": False, "mail_recv_ready": False,
        "smtp_host": "", "imap_host": "", "calendar_events": 0,
    }})


@router.post("/mail/send")
async def mail_send(req: dict):
    return JSONResponse({"success": False, "error": "邮件发送未配置（需配置SMTP环境变量）"})


@router.get("/mail/inbox")
async def mail_inbox(limit: int = 10):
    return JSONResponse({"success": True, "data": {"emails": []}})


@router.get("/calendar/list")
async def calendar_list():
    return JSONResponse({"success": True, "data": {"events": []}})


@router.post("/calendar/add")
async def calendar_add(req: dict):
    return JSONResponse({"success": False, "error": "日程管理未配置"})


@router.delete("/calendar/remove")
async def calendar_remove(event_id: str = ""):
    return JSONResponse({"success": False, "error": "日程管理未配置"})


# ─── 资讯/热搜（未集成，返回空） ────────────────────────────────
@router.get("/news/category/{cat}")
async def news_category(cat: str, n: int = 10):
    return JSONResponse({"success": True, "data": {"news": []}})


@router.get("/hot-search/{platform}")
async def hot_search(platform: str, n: int = 10):
    return JSONResponse({"success": True, "data": {"items": []}})


# ─── 代码自修改提案（别名） ────────────────────────────────
@router.get("/code/proposals")
async def code_proposals(api_key: str = Depends(verify_admin_key)):
    """别名：/code/proposals → 自修改提案列表"""
    try:
        code_modifier = get("code_modifier")
        proposals = code_modifier.list_pending()
        return JSONResponse({"success": True, "data": {"proposals": proposals}})
    except Exception as e:
        logger.debug(f"代码提案列表查询失败: {e}")
        return JSONResponse({"success": True, "data": {"proposals": []}})
