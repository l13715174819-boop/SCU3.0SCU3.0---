# -*- coding: utf-8 -*-
"""api/deps.py — 路由层依赖注入

提供全局单例的访问入口，避免 api/*.py 直接 import server.py 的全局变量。
server.py 在 startup 阶段调用 set_globals() 注入实例。
"""
from typing import Optional, Any, Callable
from fastapi import Security, HTTPException
from fastapi.security import APIKeyHeader
import secrets

# ─── API Key 头 ────────────────────────────────
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ─── 全局单例（由 server.py 注入）────────────────
_globals: dict = {}

def set_globals(**kwargs):
    """server.py 启动时调用，注入全局单例"""
    _globals.update(kwargs)

def get(name: str) -> Any:
    """获取注入的全局单例"""
    return _globals.get(name)

# ─── 认证依赖（与 server.py 保持一致）────────────

def _get_configured_api_key() -> str:
    return _globals.get("api_key", "")

def _get_configured_admin_key() -> str:
    return _globals.get("admin_key", "")

def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    expected = _get_configured_api_key()
    admin_expected = _get_configured_admin_key()
    if api_key and (secrets.compare_digest(api_key, expected) or
                    secrets.compare_digest(api_key, admin_expected)):
        return api_key
    raise HTTPException(status_code=401, detail="无效的API Key")

def verify_admin_key(api_key: str = Security(api_key_header)) -> str:
    expected = _get_configured_admin_key()
    if api_key and secrets.compare_digest(api_key, expected):
        return api_key
    raise HTTPException(status_code=403, detail="需要管理员权限")

def is_admin(api_key: str) -> bool:
    """非装饰器场景下的管理员判定"""
    if not api_key:
        return False
    return secrets.compare_digest(api_key, _get_configured_admin_key())


def require_module(module_name: str):
    """模块可用性检查（可插拔性核心）

    检查模块是否在注册表中且已加载。
    若模块未注册或已卸载/禁用，抛出 503 异常。

    Args:
        module_name: 注册表中的模块名（如 "automation.browser"）

    Raises:
        HTTPException(503): 模块不可用时
    """
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        if not registry.is_available(module_name):
            m = registry._modules.get(module_name)
            if m is None:
                detail = f"模块未注册: {module_name}"
            elif m.disabled:
                detail = f"模块已禁用: {module_name}（请先 enable）"
            else:
                detail = f"模块未加载: {module_name}（请先 POST /modules/{module_name}/load）"
            raise HTTPException(status_code=503, detail=detail)
    except HTTPException:
        raise
    except Exception:
        # 注册表本身不可用时降级放行（不阻塞业务）
        import logging
        logging.getLogger("SCU3.api.deps").debug(
            f"模块检查异常（降级放行）: {module_name}", exc_info=True)
