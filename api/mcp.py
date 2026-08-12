# -*- coding: utf-8 -*-
"""api/mcp.py — MCP 协议路由

从 server.py 抽取的 8 个 MCP 路由：
  GET    /mcp/tools                  — 列出所有 MCP 工具
  POST   /mcp/call                   — 调用 MCP 工具
  POST   /mcp/connect                — 连接远程 MCP 服务器（管理员）
  POST   /mcp/disconnect             — 断开远程 MCP 服务器（管理员）
  GET    /mcp/servers                — 列出已连接的 MCP 服务器
  POST   /mcp/servers                — 新增 MCP 服务器连接（管理员）
  DELETE /mcp/servers/{server_id}    — 移除 MCP 服务器连接（管理员）
  GET    /mcp/health                 — MCP 健康检查
"""
import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key

logger = logging.getLogger("SCU3.api.mcp")

router = APIRouter(tags=["mcp"])


# ─── 请求模型 ────────────────────────────────
class MCPCallRequest(BaseModel):
    tool: str
    params: Dict[str, Any] = {}


class MCPConnectRequest(BaseModel):
    name: str
    server_url: str
    api_key: str = ""


class MCPDisconnectRequest(BaseModel):
    name: str


class MCPServerAddRequest(BaseModel):
    """前端 POST /mcp/servers 请求体

    command 字段对应 connect_remote 的 server_url（既支持 URL，也兼容本地命令字符串）。
    """
    name: str
    command: str
    api_key: str = ""


# ─── 路由 ────────────────────────────────
@router.get("/mcp/tools")
async def mcp_tools(api_key: str = Depends(verify_api_key)):
    """列出所有MCP工具（本地+远程）"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        tools = get_mcp_registry().list_all_tools()
        return JSONResponse({"success": True, "tools": tools,
                             "count": len(tools)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/mcp/call")
async def mcp_call(req: MCPCallRequest, api_key: str = Depends(verify_api_key)):
    """调用MCP工具（自动路由本地/远程）"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        result = get_mcp_registry().route_call(req.tool, req.params)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/mcp/connect")
async def mcp_connect(req: MCPConnectRequest, api_key: str = Depends(verify_admin_key)):
    """连接远程MCP服务器（需管理员权限）"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        ok = get_mcp_registry().connect_remote(req.name, req.server_url, req.api_key or None)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/mcp/disconnect")
async def mcp_disconnect(req: MCPDisconnectRequest, api_key: str = Depends(verify_admin_key)):
    """断开远程MCP服务器（需管理员权限）"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        get_mcp_registry().disconnect_remote(req.name)
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/mcp/servers")
async def mcp_servers(api_key: str = Depends(verify_api_key)):
    """列出已连接的MCP服务器"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        status = get_mcp_registry().get_status()
        return JSONResponse({"success": True, "servers": status.get("remote_servers", {}),
                             "local_tools": status.get("local_tools", 0)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/mcp/servers")
async def mcp_servers_add(req: MCPServerAddRequest,
                          api_key: str = Depends(verify_admin_key)):
    """新增 MCP 服务器连接（管理员权限）

    前端表单提交 {name, command}，这里桥接到 registry.connect_remote。
    command 既可以是远程 URL，也可以是本地启动命令（由 MCPClient 解析）。
    """
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        ok = get_mcp_registry().connect_remote(req.name, req.command, req.api_key or None)
        if ok:
            return JSONResponse({"success": True, "name": req.name})
        return JSONResponse({"success": False, "error": f"连接失败: {req.name}"})
    except Exception as e:
        logger.error(f"新增 MCP 服务器异常: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.delete("/mcp/servers/{server_id}")
async def mcp_servers_delete(server_id: str,
                             api_key: str = Depends(verify_admin_key)):
    """移除 MCP 服务器连接（管理员权限）

    前端通过 DELETE /mcp/servers/{id} 调用，id 即连接名称（URL 解码后传入）。
    """
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        get_mcp_registry().disconnect_remote(server_id)
        return JSONResponse({"success": True, "name": server_id})
    except Exception as e:
        logger.error(f"移除 MCP 服务器异常: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/mcp/health")
async def mcp_health(api_key: str = Depends(verify_api_key)):
    """MCP健康检查"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        status = get_mcp_registry().get_status()
        healthy = all(s.get("connected") for s in status.get("remote_servers", {}).values())
        return JSONResponse({"success": True, "healthy": healthy,
                             "status": status})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
