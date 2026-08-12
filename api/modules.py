# -*- coding: utf-8 -*-
"""api/modules.py — 功能模块注册表路由

从 server.py 抽取的 8 个模块管理路由：
  GET  /modules                  — 列出所有功能模块
  GET  /modules/{name}           — 获取单个模块详情
  POST /modules/{name}/load      — 加载模块（管理员）
  POST /modules/{name}/unload    — 卸载模块（管理员）
  POST /modules/{name}/reload    — 重载模块（管理员）
  POST /modules/{name}/disable   — 禁用模块（管理员）
  POST /modules/{name}/enable    — 启用模块（管理员）
  GET  /modules/status           — 模块注册表总状态
"""
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key

logger = logging.getLogger("SCU3.api.modules")

router = APIRouter(tags=["modules"])


class ModuleActionRequest(BaseModel):
    force: bool = False  # 强制操作（如卸载受保护模块）


@router.get("/modules")
async def modules_list(api_key: str = Depends(verify_api_key), category: str = ""):
    """列出所有功能模块"""
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        modules = registry.list_modules(category=category or None)
        return JSONResponse({
            "success": True,
            "modules": modules,
            "status": registry.status(),
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/modules/{name}")
async def module_get(name: str, api_key: str = Depends(verify_api_key)):
    """获取单个模块详情"""
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        modules = {m["name"]: m for m in registry.list_modules()}
        if name not in modules:
            return JSONResponse({"success": False, "error": f"未注册的模块: {name}"})
        return JSONResponse({"success": True, "module": modules[name]})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/modules/{name}/load")
async def module_load(name: str, api_key: str = Depends(verify_admin_key)):
    """加载模块（管理员）"""
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().load(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/modules/{name}/unload")
async def module_unload(name: str, req: ModuleActionRequest,
                        api_key: str = Depends(verify_admin_key)):
    """卸载模块（管理员）

    释放模块占用的资源（关闭浏览器、停止监听、卸载模型等）。
    受保护模块（CUF守卫/防火墙等）不可卸载，除非 force=true。
    """
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().unload(name, force=req.force)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/modules/{name}/reload")
async def module_reload(name: str, api_key: str = Depends(verify_admin_key)):
    """重载模块（unload + load，管理员）"""
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().reload(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/modules/{name}/disable")
async def module_disable(name: str, api_key: str = Depends(verify_admin_key)):
    """禁用模块（卸载 + 标记 disabled，之后无法 load 直到 enable）"""
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().disable(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/modules/{name}/enable")
async def module_enable(name: str, api_key: str = Depends(verify_admin_key)):
    """启用模块（清除 disabled 标记，不自动加载）"""
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().enable(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/modules/status")
async def modules_status(api_key: str = Depends(verify_api_key)):
    """模块注册表总状态"""
    try:
        from m_layer.module_registry import get_registry
        return JSONResponse({"success": True, "status": get_registry().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
