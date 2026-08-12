# -*- coding: utf-8 -*-
"""api/multimodal.py — 多模态路由

从 server.py 抽取的 5 个多模态路由：
  POST /multimodal/process  — 处理多模态输入（自动检测模态）
  POST /multimodal/image    — 图像理解
  POST /multimodal/audio    — 音频理解
  POST /multimodal/video    — 视频理解
  GET  /multimodal/status   — 多模态处理器状态
"""
import logging
from typing import Any
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key

logger = logging.getLogger("SCU3.api.multimodal")

router = APIRouter(tags=["multimodal"])


# ─── 请求模型 ────────────────────────────────
class MultimodalProcessRequest(BaseModel):
    input_data: Any  # 文本/文件路径/混合字典
    modality: str = ""  # text/image/audio/video/mixed，空则自动检测


class MultimodalPathRequest(BaseModel):
    path: str


# ─── 多模态处理 ────────────────────────────────
@router.post("/multimodal/process")
async def multimodal_process(req: MultimodalProcessRequest,
                              api_key: str = Depends(verify_api_key)):
    """处理多模态输入（自动检测模态）"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        result = get_multimodal_processor().process(
            req.input_data, req.modality or None)
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/multimodal/image")
async def multimodal_image(req: MultimodalPathRequest,
                            api_key: str = Depends(verify_api_key)):
    """图像理解"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        result = get_multimodal_processor().process(req.path, "image")
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/multimodal/audio")
async def multimodal_audio(req: MultimodalPathRequest,
                            api_key: str = Depends(verify_api_key)):
    """音频理解"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        result = get_multimodal_processor().process(req.path, "audio")
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/multimodal/video")
async def multimodal_video(req: MultimodalPathRequest,
                            api_key: str = Depends(verify_api_key)):
    """视频理解"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        result = get_multimodal_processor().process(req.path, "video")
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/multimodal/status")
async def multimodal_status(api_key: str = Depends(verify_api_key)):
    """多模态处理器状态"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        proc = get_multimodal_processor()
        return JSONResponse({"success": True, "status": {
            "cache_size": len(proc._cache),
        }})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
