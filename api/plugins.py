# -*- coding: utf-8 -*-
"""api/plugins.py — 插件系统与插件市场路由

从 server.py 抽取的 18 个插件路由：
  GET  /plugins                          — 列出所有插件
  POST /plugins/{name}/enable            — 启用插件（管理员）
  POST /plugins/{name}/disable           — 禁用插件（管理员）
  GET  /plugins/{name}/config            — 获取插件配置
  POST /plugins/{name}/config            — 设置插件配置（管理员）
  POST /plugins/load                     — 从目录加载插件（管理员）
  GET  /plugins/metrics                  — 获取 MetricsPlugin 统计
  GET  /plugins/list                     — 别名：/plugins/list → /plugins
  POST /plugins/toggle                   — 切换插件启用/禁用
  GET  /plugins/stats                    — 获取插件沙箱统计
  GET  /plugins/market/list              — 列出市场可用插件
  GET  /plugins/market/status            — 插件市场总状态
  POST /plugins/market/install           — 安装并加载插件
  POST /plugins/market/unload            — 卸载已加载插件
  POST /plugins/market/uninstall         — 完全卸载插件（管理员）
  GET  /plugins/market/loaded            — 查看已加载插件
  POST /plugins/market/keep-alive        — 标记插件为持久模式
  POST /plugins/market/match             — 测试能力匹配
"""
import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key

logger = logging.getLogger("SCU3.api.plugins")

router = APIRouter(tags=["plugins"])


# ─── 请求模型 ────────────────────────────────
class PluginConfigRequest(BaseModel):
    config: Dict[str, Any]


class PluginLoadRequest(BaseModel):
    dir_path: str


# ─── 插件管理 ────────────────────────────────
@router.get("/plugins")
async def plugins_list(api_key: str = Depends(verify_api_key)):
    """列出所有插件"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        plugins = get_plugin_manager().list_plugins()
        return JSONResponse({"success": True, "plugins": plugins})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/plugins/{name}/enable")
async def plugin_enable(name: str, api_key: str = Depends(verify_admin_key)):
    """启用插件（需管理员权限）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        ok = get_plugin_manager().enable_plugin(name)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/plugins/{name}/disable")
async def plugin_disable(name: str, api_key: str = Depends(verify_admin_key)):
    """禁用插件（需管理员权限）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        ok = get_plugin_manager().disable_plugin(name)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/plugins/{name}/config")
async def plugin_config_get(name: str, api_key: str = Depends(verify_api_key)):
    """获取插件配置"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        config = get_plugin_manager().get_config(name)
        return JSONResponse({"success": True, "config": config})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/plugins/{name}/config")
async def plugin_config_set(name: str, req: PluginConfigRequest,
                            api_key: str = Depends(verify_admin_key)):
    """设置插件配置（需管理员权限）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        ok = get_plugin_manager().set_config(name, req.config)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/plugins/load")
async def plugins_load(req: PluginLoadRequest, api_key: str = Depends(verify_admin_key)):
    """从目录加载插件（需管理员权限）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        loaded = get_plugin_manager().load_from_directory(req.dir_path)
        return JSONResponse({"success": True, "loaded": loaded})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/plugins/metrics")
async def plugins_metrics(api_key: str = Depends(verify_api_key)):
    """获取MetricsPlugin统计数据"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        pm = get_plugin_manager()
        metrics_plugin = pm.get_plugin("metrics")
        if metrics_plugin is None:
            return JSONResponse({"success": False, "error": "metrics插件未加载"})
        return JSONResponse({"success": True, "metrics": metrics_plugin.get_metrics()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/plugins/list")
async def plugins_list_alias(api_key: str = Depends(verify_api_key)):
    """别名：/plugins/list → /plugins"""
    from m_layer.plugin_system import get_plugin_manager
    try:
        pm = get_plugin_manager()
        return JSONResponse({"success": True, "data": {"plugins": pm.list_plugins()}})
    except Exception:
        return JSONResponse({"success": True, "data": {"plugins": []}})


@router.post("/plugins/toggle")
async def plugins_toggle(req: dict, api_key: str = Depends(verify_api_key)):
    """切换插件启用/禁用（前端 POST /plugins/toggle 调用）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        pid = str(req.get("id", "")).strip()
        enable = bool(req.get("enable", False))
        if not pid:
            return JSONResponse({"success": False, "error": "缺少插件 id"})
        pm = get_plugin_manager()
        ok = pm.enable_plugin(pid) if enable else pm.disable_plugin(pid)
        if ok:
            return JSONResponse({"success": True, "data": {"id": pid, "enabled": enable}})
        return JSONResponse({"success": False, "error": f"插件不存在或操作失败: {pid}"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/plugins/stats")
async def plugins_stats(id: str = "", api_key: str = Depends(verify_api_key)):
    """获取插件统计（前端 GET /plugins/stats?id= 调用）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        pm = get_plugin_manager()
        stats = pm.get_sandbox_stats()
        if id:
            return JSONResponse({"success": True, "data": stats.get(id, {})})
        return JSONResponse({"success": True, "data": stats})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 插件市场（自动下载/加载/卸载） ────────────────────────────────
@router.get("/plugins/market/list")
async def market_list(api_key: str = Depends(verify_api_key)):
    """列出插件市场所有可用插件"""
    try:
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        return JSONResponse({"success": True, "data": market.list_available()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/plugins/market/status")
async def market_status(api_key: str = Depends(verify_api_key)):
    """插件市场总状态"""
    try:
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        return JSONResponse({"success": True, "data": market.get_status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/plugins/market/install")
async def market_install(req: dict, api_key: str = Depends(verify_api_key)):
    """安装并加载指定插件（POST /plugins/market/install {name: "pdf_reader"}）"""
    try:
        name = req.get("name", "")
        if not name:
            return JSONResponse({"success": False, "error": "缺少 name 参数"})
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        result = market.install_and_load(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/plugins/market/unload")
async def market_unload(req: dict, api_key: str = Depends(verify_api_key)):
    """卸载已加载的插件（用完释放，POST /plugins/market/unload {name: "pdf_reader"}）"""
    try:
        name = req.get("name", "")
        if not name:
            return JSONResponse({"success": False, "error": "缺少 name 参数"})
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        result = market.unload_after_use(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/plugins/market/uninstall")
async def market_uninstall(req: dict, api_key: str = Depends(verify_admin_key)):
    """完全卸载插件（pip uninstall，需管理员权限）"""
    try:
        name = req.get("name", "")
        if not name:
            return JSONResponse({"success": False, "error": "缺少 name 参数"})
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        result = market.uninstall(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/plugins/market/loaded")
async def market_loaded(api_key: str = Depends(verify_api_key)):
    """查看当前已加载的插件（含 TTL 剩余时间）"""
    try:
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        return JSONResponse({"success": True, "data": market.list_loaded()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/plugins/market/keep-alive")
async def market_keep_alive(req: dict, api_key: str = Depends(verify_api_key)):
    """标记插件为持久模式（不自动卸载）"""
    try:
        name = req.get("name", "")
        if not name:
            return JSONResponse({"success": False, "error": "缺少 name 参数"})
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        if market.keep_alive(name):
            return JSONResponse({"success": True, "name": name, "message": "已标记为持久模式"})
        return JSONResponse({"success": False, "error": f"插件 {name} 未加载"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/plugins/market/match")
async def market_match(req: dict, api_key: str = Depends(verify_api_key)):
    """测试能力匹配（POST /plugins/market/match {input: "读取pdf", failed_tool: ""}）"""
    try:
        user_input = req.get("input", "")
        failed_tool = req.get("failed_tool", "")
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        info = market.match_capability(user_input, failed_tool)
        if info:
            return JSONResponse({"success": True, "matched": True, "plugin": info})
        return JSONResponse({"success": True, "matched": False, "plugin": None})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
