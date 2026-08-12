# -*- coding: utf-8 -*-
"""api/local_model.py — 本地模型路由

从 server.py 抽取的 6 个本地模型路由：
  GET  /local-model/status       — 本地模型状态
  GET  /local-model/models       — 列出支持的本地模型
  POST /local-model/load          — 加载本地模型（管理员）
  POST /local-model/unload       — 卸载本地模型（管理员）
  GET  /local-model/health        — 本地模型健康检查
  POST /local-model/switch-type  — 切换本地模型类型（text ↔ vl，管理员）
"""
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key

logger = logging.getLogger("SCU3.api.local_model")

router = APIRouter(tags=["local_model"])


# ─── 请求模型 ────────────────────────────────
class LocalModelLoadRequest(BaseModel):
    model_name: str
    quantization: str = "auto"  # auto/4bit/8bit/none
    device: str = "auto"  # auto/cuda/cpu/mps


class ModelTypeSwitchRequest(BaseModel):
    target_type: str  # text / vl
    model_name: str = ""  # 为空自动选择
    quantization: str = "auto"
    device: str = "auto"


# ─── 本地模型管理 ────────────────────────────────
@router.get("/local-model/status")
async def local_model_status(api_key: str = Depends(verify_api_key)):
    """本地模型状态"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        return JSONResponse({"success": True, "status": client.status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/local-model/models")
async def local_model_list(api_key: str = Depends(verify_api_key)):
    """列出支持的本地模型"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        return JSONResponse({"success": True, "models": client.list_supported_models()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/local-model/load")
async def local_model_load(req: LocalModelLoadRequest, api_key: str = Depends(verify_admin_key)):
    """加载本地模型（需管理员）"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        result = client.load_model(req.model_name, req.quantization, req.device)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/local-model/unload")
async def local_model_unload(api_key: str = Depends(verify_admin_key)):
    """卸载本地模型（需管理员）"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        result = client.unload_model()
        return JSONResponse({"success": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/local-model/health")
async def local_model_health(api_key: str = Depends(verify_api_key)):
    """本地模型健康检查"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        healthy = client.health_check()
        return JSONResponse({"success": True, "healthy": healthy})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/local-model/switch-type")
async def local_model_switch_type(req: ModelTypeSwitchRequest, api_key: str = Depends(verify_admin_key)):
    """切换本地模型类型（text ↔ vl，需管理员）

    按方案 A：文本模型与视觉模型不同时加载，切换时卸载当前模型并加载目标模型。

    请求体示例：
        {"target_type": "vl"}  # 自动选择 qwen2-5-vl-7b
        {"target_type": "text", "model_name": "qwen2-5-7b"}
    """
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        result = client.switch_model_type(
            req.target_type,
            model_name=(req.model_name or None),
            quantization=req.quantization,
            device=req.device,
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
