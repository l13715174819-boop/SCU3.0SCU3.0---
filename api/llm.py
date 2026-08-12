# -*- coding: utf-8 -*-
"""api/llm.py — LLM 平台管理路由

从 server.py 抽取的 6 个 LLM 管理路由：
  GET  /llm/platforms         — 列出所有可用 LLM 平台
  GET  /models                — 列出所有支持的模型平台（无认证）
  GET  /units                 — 列出可用 SCU 单元（无认证）
  POST /llm/switch            — 切换 LLM 平台（管理员）
  GET  /llm/status            — LLM 客户端状态
  GET  /local/backends/status — 本地模型后端状态
"""
import os
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key

logger = logging.getLogger("SCU3.api.llm")

router = APIRouter(tags=["llm"])


# ─── 请求模型 ────────────────────────────────
class PlatformSwitchRequest(BaseModel):
    platform: str
    model: str = ""


# ─── 本地模型后端状态 ────────────────────────────────
@router.get("/local/backends/status")
async def local_backends_status():
    """本地模型后端状态（LM Studio / ComfyUI）

    SCU3 默认使用自有 local_model（Qwen2.5-7B/VL），未集成 LM Studio/ComfyUI。
    返回 enabled=false 让前端显示"未启用"。
    """
    return JSONResponse({
        "success": True,
        "data": {
            "lmstudio": {
                "enabled": False,
                "available": False,
                "url": "http://localhost:1234/v1",
                "loaded_models": [],
            },
            "comfyui": {
                "enabled": False,
                "available": False,
                "checkpoint": None,
            },
        },
    })


# ─── LLM 平台管理 ────────────────────────────────
@router.get("/llm/platforms")
async def llm_platforms(api_key: str = Depends(verify_api_key)):
    """列出所有可用LLM平台"""
    from m_layer.llm_client import get_client
    client = get_client()
    return JSONResponse({
        "active": client.get_active_platform(),
        "available": client.list_available_platforms(),
    })


@router.get("/models")
async def list_models():
    """列出所有支持的模型平台（供前端对话面板下拉框使用，无认证）

    返回所有预设平台（含未配置Key的），标注 available 状态，便于用户了解可选项。
    返回 {success, data:{current, platforms:[{id,label,model,active,available,local}]}}
    """
    from m_layer.llm_client import get_client
    import urllib.request as _urlreq
    import json as _json
    try:
        client = get_client()
        active = client.get_active_platform()
        current_model = client.default_model or active.get("id", "default")
        platforms = []
        for pid, cfg in client.PLATFORM_CONFIGS.items():
            entry = {
                "id": pid,
                "label": cfg["label"],
                "model": cfg["default_model"],
                "local": cfg["local"],
                "active": pid == client.active_platform,
                "available": False,
            }
            if cfg["local"]:
                # 实时探测本地服务
                base_url = os.getenv(cfg["env_url"], cfg["default_url"])
                try:
                    with _urlreq.urlopen(f"{base_url}/models", timeout=1.5) as r:
                        data = _json.loads(r.read().decode("utf-8"))
                        models = data.get("data", [])
                    if models:
                        entry["available"] = True
                        entry["model"] = models[0].get("id", cfg["default_model"])
                except Exception as e:
                    logger.debug(f"本地模型探活失败 {base_url}: {e}")
            else:
                # 云端：检查 Key 是否配置（支持主备变量名）
                key = os.getenv(cfg["env_key"], "")
                if not key and cfg.get("env_key_alt"):
                    key = os.getenv(cfg["env_key_alt"], "")
                if key:
                    entry["available"] = True
                    entry["model"] = os.getenv(pid.upper() + "_MODEL", cfg["default_model"])
            platforms.append(entry)
        return JSONResponse({
            "success": True,
            "data": {
                "current": current_model,
                "current_platform": active,
                "platforms": platforms,
            },
        })
    except Exception as e:
        return JSONResponse({
            "success": True,
            "data": {
                "current": "default",
                "platforms": [{"id": "default", "label": "默认模型", "model": "default", "active": True, "available": True, "local": False}],
            },
        })


@router.get("/units")
async def list_units():
    """列出可用 SCU 单元（供前端对话面板下拉框使用，无认证）

    SCU3 为单实例部署，返回默认单元。
    """
    return JSONResponse({
        "success": True,
        "data": {
            "units": [
                {
                    "uid": "SCU3-default",
                    "system_prompt_style": "SCU3 标准单元",
                },
            ],
        },
    })


@router.post("/llm/switch")
async def llm_switch(req: PlatformSwitchRequest, api_key: str = Depends(verify_admin_key)):
    """切换LLM平台（需管理员权限）"""
    from m_layer.llm_client import get_client
    client = get_client()
    result = client.switch_platform(req.platform, req.model)
    return JSONResponse(result)


@router.get("/llm/status")
async def llm_status(api_key: str = Depends(verify_api_key)):
    """LLM客户端状态"""
    from m_layer.llm_client import get_client
    client = get_client()
    return JSONResponse(client.get_status())
