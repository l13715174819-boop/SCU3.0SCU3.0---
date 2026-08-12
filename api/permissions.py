# -*- coding: utf-8 -*-
"""api/permissions.py — 权限审批路由

从 server.py 抽取的 10 个权限路由：
  GET  /permissions/tools                              — 列出工具权限分级
  POST /permissions/check                              — 检查用户权限
  POST /permissions/confirm                            — 创建敏感操作确认请求
  POST /permissions/confirm/{confirmation_id}/resolve  — 处理敏感操作确认（管理员）
  POST /permissions/approval                           — 创建危险操作审批请求
  POST /permissions/approval/{approval_id}/resolve     — 处理危险操作审批（管理员）
  POST /permissions/elevation                          — 申请权限提升
  GET  /permissions/audit                              — 权限审计日志（管理员）
  GET  /permissions/status                             — 权限状态统计
  GET  /permissions/pending                            — 待审批权限列表
"""
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key, is_admin

logger = logging.getLogger("SCU3.api.permissions")

router = APIRouter(tags=["permissions"])


# ─── 请求模型 ────────────────────────────────
class PermissionCheckRequest(BaseModel):
    user_level: str  # guest/user/power_user/admin 或 L0~L3
    tool_name: str


class ConfirmCreateRequest(BaseModel):
    tool_name: str
    user_id: str


class ConfirmResolveRequest(BaseModel):
    confirmed: bool
    resolver: str = ""


class ApprovalCreateRequest(BaseModel):
    tool_name: str
    user_id: str


class ApprovalResolveRequest(BaseModel):
    approved: bool
    approver: str = "admin"


class ElevationRequest(BaseModel):
    user_id: str
    requested_level: str
    reason: str


# ─── 工具权限分级 ────────────────────────────────
@router.get("/permissions/tools")
async def permissions_tools(level: str = "", api_key: str = Depends(verify_api_key)):
    """列出工具权限分级（可按level过滤）"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        result = get_permission_manager().list_tools_by_level(level or None)
        return JSONResponse({"success": True, "tools_by_level": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/permissions/check")
async def permissions_check(req: PermissionCheckRequest,
                             api_key: str = Depends(verify_api_key)):
    """检查用户权限是否可使用某工具"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        allowed, reason = get_permission_manager().check_permission(req.user_level, req.tool_name)
        return JSONResponse({"success": True, "allowed": allowed, "reason": reason})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/permissions/confirm")
async def permissions_confirm_create(req: ConfirmCreateRequest,
                                      api_key: str = Depends(verify_api_key)):
    """创建敏感操作确认请求"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        cfm_id = get_permission_manager().create_confirmation(req.tool_name, req.user_id)
        return JSONResponse({"success": True, "confirmation_id": cfm_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/permissions/confirm/{confirmation_id}/resolve")
async def permissions_confirm_resolve(confirmation_id: str, req: ConfirmResolveRequest,
                                       api_key: str = Depends(verify_admin_key)):
    """处理敏感操作确认（需管理员权限）"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        ok = get_permission_manager().resolve_confirmation(confirmation_id, req.confirmed, req.resolver)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/permissions/approval")
async def permissions_approval_create(req: ApprovalCreateRequest,
                                       api_key: str = Depends(verify_api_key)):
    """创建危险操作审批请求"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        apv_id = get_permission_manager().require_approval(req.tool_name, req.user_id)
        return JSONResponse({"success": True, "approval_id": apv_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/permissions/approval/{approval_id}/resolve")
async def permissions_approval_resolve(approval_id: str, req: ApprovalResolveRequest,
                                        api_key: str = Depends(verify_admin_key)):
    """处理危险操作审批（需管理员权限）"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        ok = get_permission_manager().resolve_approval(approval_id, req.approved, req.approver)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/permissions/elevation")
async def permissions_elevation(req: ElevationRequest,
                                 api_key: str = Depends(verify_api_key)):
    """申请权限提升"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        req_id = get_permission_manager().apply_elevation(req.user_id, req.requested_level, req.reason)
        return JSONResponse({"success": True, "request_id": req_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/permissions/audit")
async def permissions_audit(limit: int = 100, api_key: str = Depends(verify_admin_key)):
    """获取权限审计日志（需管理员权限）"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        log = get_permission_manager().get_audit_log(limit)
        return JSONResponse({"success": True, "audit_log": log})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 权限状态 ────────────────────────────────
@router.get("/permissions/status")
async def permissions_status(api_key: str = Depends(verify_api_key)):
    """权限状态：统计真实工具数（ActionLayer + ExtendedTools 去重）"""
    try:
        from w1_layer.action import ActionLayer
        from w1_layer.extended_tools import ExtendedTools
        # 直接从实例获取工具列表
        a = ActionLayer()
        e = ExtendedTools()
        core_tools = set(getattr(a, "_tools", {}).keys())
        ext_tools = set(getattr(e, "_tools", {}).keys())
        all_tools = core_tools | ext_tools
        # 写类工具（高危）按工具类型映射统计
        write_tools = {t for t in ext_tools
                       if ExtendedTools.TOOL_TYPES.get(t) == "write"}
        return JSONResponse({"success": True, "data": {
            "role": "admin" if is_admin(api_key) else "user",
            "elevated": is_admin(api_key),
            "tools_enabled": len(all_tools),
            "tools_blocked": len(write_tools) if not is_admin(api_key) else 0,
            "core_tools": sorted(core_tools),
            "extended_tools": sorted(ext_tools),
        }})
    except Exception as e:
        logger.error(f"权限状态查询失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/permissions/pending")
async def permissions_pending(api_key: str = Depends(verify_api_key)):
    """待审批权限列表（当前无运行时权限审批机制，返回空）"""
    return JSONResponse({"success": True, "data": {"items": []}})
