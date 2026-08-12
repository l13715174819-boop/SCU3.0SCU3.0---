# -*- coding: utf-8 -*-
"""api/tools.py — 工具链路由

从 server.py 抽取的 12 个工具链路由：
  POST   /codegen/generate        — 代码生成（可选自动执行）
  POST   /toolchain/execute       — 多工具链式执行
  GET    /templates               — 列出任务模板
  GET    /templates/stats         — 模板统计
  DELETE /templates/{template_id} — 删除模板（管理员）
  GET    /tools/stats             — 工具使用统计
  GET    /tools/recommend         — 推荐最优工具
  GET    /extended_tools/list     — 列出所有扩展工具
  POST   /extended_tools/call     — 调用扩展工具
  GET    /extended_tools/categories — 按类别列出工具
  POST   /tools/select            — 自然语言选择工具
  GET    /tools/extended          — 别名：/tools/extended → /extended_tools/list
"""
import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key

logger = logging.getLogger("SCU3.api.tools")

router = APIRouter(tags=["tools"])


# ─── 请求模型 ────────────────────────────────
class CodeGenRequest(BaseModel):
    requirement: str
    execute: bool = True


class ToolChainRequest(BaseModel):
    tools: list  # [{tool, params, extract_field?, input_field?, on_fail?}]


class ExtendedToolCallRequest(BaseModel):
    tool: str
    params: Dict[str, Any] = {}


class ToolSelectRequest(BaseModel):
    query: str
    context: Dict[str, Any] = {}
    max_tools: int = 3


# ─── 代码生成 ────────────────────────────────
@router.post("/codegen/generate")
async def codegen_generate(req: CodeGenRequest, api_key: str = Depends(verify_api_key)):
    """代码生成（可选自动执行）"""
    from m_layer.code_generator import get_code_generator
    gen = get_code_generator()
    if req.execute:
        result = gen.generate_and_run(req.requirement)
    else:
        result = gen.generate_only(req.requirement)
    return JSONResponse(result)


# ─── 工具链 ────────────────────────────────
@router.post("/toolchain/execute")
async def toolchain_execute(req: ToolChainRequest, api_key: str = Depends(verify_api_key)):
    """多工具链式执行"""
    from m_layer.tool_chain import quick_chain
    result = quick_chain(req.tools)
    return JSONResponse(result)


# ─── 任务模板 ────────────────────────────────
@router.get("/templates")
async def templates_list(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """列出任务模板"""
    from m_layer.task_template import get_template_manager
    return JSONResponse({"templates": get_template_manager().list_templates(limit)})


@router.get("/templates/stats")
async def templates_stats(api_key: str = Depends(verify_api_key)):
    """模板统计"""
    from m_layer.task_template import get_template_manager
    return JSONResponse(get_template_manager().get_stats())


@router.delete("/templates/{template_id}")
async def templates_delete(template_id: str, api_key: str = Depends(verify_admin_key)):
    """删除模板"""
    from m_layer.task_template import get_template_manager
    ok = get_template_manager().delete_template(template_id)
    return JSONResponse({"success": ok})


# ─── 工具偏好 ────────────────────────────────
@router.get("/tools/stats")
async def tools_stats(api_key: str = Depends(verify_api_key)):
    """工具使用统计"""
    from m_layer.tool_preference import get_tool_preference
    return JSONResponse(get_tool_preference().get_all_stats())


@router.get("/tools/recommend")
async def tools_recommend(scenario: str = "default", top_k: int = 3,
                           api_key: str = Depends(verify_api_key)):
    """推荐最优工具"""
    from m_layer.tool_preference import get_tool_preference
    return JSONResponse({"recommendations": get_tool_preference().recommend(scenario, top_k)})


# ─── 扩展工具 ────────────────────────────────
@router.get("/extended_tools/list")
async def extended_tools_list(api_key: str = Depends(verify_api_key)):
    """列出所有扩展工具"""
    try:
        from w1_layer.extended_tools import get_extended_tools
        tools = get_extended_tools()
        return JSONResponse({"success": True, "tools": list(tools.TOOL_TYPES.keys())})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/extended_tools/call")
async def extended_tools_call(req: ExtendedToolCallRequest,
                               api_key: str = Depends(verify_api_key)):
    """调用扩展工具"""
    try:
        from w1_layer.extended_tools import get_extended_tools
        result = get_extended_tools().execute(req.tool, req.params)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/extended_tools/categories")
async def extended_tools_categories(api_key: str = Depends(verify_api_key)):
    """按类别（read/write）列出工具"""
    try:
        from w1_layer.extended_tools import get_extended_tools
        types = get_extended_tools().TOOL_TYPES
        categories: Dict[str, list] = {}
        for tool, ttype in types.items():
            categories.setdefault(ttype, []).append(tool)
        return JSONResponse({"success": True, "categories": categories})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 自然语言工具选择 ────────────────────────────────
@router.post("/tools/select")
async def tools_select(req: ToolSelectRequest, api_key: str = Depends(verify_api_key)):
    """自然语言选择工具"""
    try:
        from m_layer.nl_tool_selector import get_nl_selector
        selector = get_nl_selector()
        if req.max_tools > 1:
            result = selector.select_multi(req.query, req.max_tools)
            return JSONResponse({"success": True, "selections": result})
        else:
            result = selector.select(req.query, req.context)
            return JSONResponse({"success": True, "selection": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 扩展工具别名 ────────────────────────────────
@router.get("/tools/extended")
async def tools_extended_alias(api_key: str = Depends(verify_api_key)):
    """别名：/tools/extended → /extended_tools/list"""
    try:
        from w1_layer.extended_tools import get_extended_tools
        tools = get_extended_tools()
        return JSONResponse({"success": True, "data": {"tools": tools}})
    except Exception as e:
        logger.debug(f"扩展工具别名查询失败: {e}")
        return JSONResponse({"success": True, "data": {"tools": []}})
