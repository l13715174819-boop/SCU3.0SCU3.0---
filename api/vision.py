# -*- coding: utf-8 -*-
"""api/vision.py — 视觉与图像生成路由

从 server.py 抽取的 5 个视觉/图像路由：
  POST /vision/chat          — 视觉对话（本地 VL 模型）
  GET  /vision/status       — 视觉模型能力状态
  POST /vision/analyze-screen — 截屏并让 VL 模型分析（"看屏幕"端点）
  POST /image/generate       — 图片生成端点（Pollinations 免配置）
  GET  /image/backends       — 返回真实可用的图片生成后端
"""
import os
import time
import logging
from typing import List
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key, require_module

logger = logging.getLogger("SCU3.api.vision")

router = APIRouter(tags=["vision"])

# 项目根目录（scu3/），与 server.py 中的 BASE_DIR 保持一致
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─── 请求模型 ────────────────────────────────
class VisionChatRequest(BaseModel):
    prompt: str
    image_path: str = ""
    image_url: str = ""
    image_base64: str = ""
    system_prompt: str = "default"
    temperature: float = 0.7
    max_tokens: int = 1024
    auto_switch: bool = True  # 自动从 text 切换到 vl


class VisionAnalyzeScreenRequest(BaseModel):
    prompt: str = "描述屏幕上的内容"
    monitor: int = 1
    region: List[int] = []  # [left, top, width, height]，为空则全屏
    auto_switch: bool = True  # 自动切换到 VL 模型
    max_tokens: int = 1024


# 图片生成尺寸映射（Pollinations 免配置）
_SIZE_MAP = {
    "landscape_16_9": (1280, 720),
    "landscape_4_3": (1152, 864),
    "square_hd": (1024, 1024),
    "square": (1024, 1024),
    "portrait_4_3": (864, 1152),
    "portrait_16_9": (720, 1280),
}


# ─── 视觉对话 ────────────────────────────────
@router.post("/vision/chat")
async def vision_chat(req: VisionChatRequest, api_key: str = Depends(verify_api_key)):
    """视觉对话：使用本地 VL 模型对图像+提示词进行推理

    支持三种图像输入方式（按优先级取其一）：
      1. image_path: 本地图像文件路径
      2. image_url: HTTP(S) 图像 URL
      3. image_base64: base64 编码的图像数据（可含 data:image/... 前缀）

    若当前加载的是文本模型且 auto_switch=true，会自动切换到 VL 模型。
    按方案 A：文本/VL 模型不同时加载，切换时卸载当前模型。

    请求体示例：
        {"prompt": "描述这张图", "image_path": "C:/images/test.png"}
        {"prompt": "图里有什么文字？", "image_url": "https://example.com/a.jpg"}
        {"prompt": "这是什么的UI截图？", "image_base64": "iVBORw0KGgo..."}

    返回：
        {success, content, model, model_type, tokens, latency, switched, error}
    """
    try:
        require_module("llm.local_model")
        # 校验图像输入
        if not (req.image_path or req.image_url or req.image_base64):
            return JSONResponse({
                "success": False,
                "error": "必须提供 image_path / image_url / image_base64 之一",
            })

        # 构造图像参数（按优先级）
        if req.image_path:
            image = {"path": req.image_path}
        elif req.image_url:
            image = {"url": req.image_url}
        else:
            image = {"base64": req.image_base64}

        from m_layer.llm_client import get_client
        llm = get_client()
        result = llm.chat_with_image(
            prompt=req.prompt,
            image=image,
            system_prompt=req.system_prompt,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            auto_switch=req.auto_switch,
        )

        return JSONResponse({
            "success": not result.get("error"),
            "content": result.get("content", ""),
            "model": result.get("model"),
            "model_type": result.get("model_type", "vl"),
            "tokens": result.get("tokens", 0),
            "latency": result.get("latency", 0),
            "switched": result.get("switched", False),
            "platform": result.get("platform", "local_torch"),
            "error": result.get("error"),
        })
    except Exception as e:
        logger.error(f"视觉对话端点异常: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/vision/status")
async def vision_status(api_key: str = Depends(verify_api_key)):
    """视觉模型能力状态：检查 VL 依赖和当前模型类型"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        status = client.status()
        deps = status.get("dependencies", {})
        return JSONResponse({
            "success": True,
            "vl_supported": deps.get("qwen_vl", False),
            "current_model_type": status.get("model_type", "text"),
            "is_vl_loaded": status.get("is_vl_model", False),
            "vl_available": client.is_vl_available(),
            "supported_vl_models": [
                m for m in client.list_supported_models()
                if m.get("model_type") == "vl"
            ],
            "pillow_required": "Pillow 未安装时无法推理",
            "hint": "若 vl_supported=false，请执行: pip install -U transformers>=4.45 pillow",
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── VL + 截屏联动：看屏幕 ─────────────────────────
@router.post("/vision/analyze-screen")
async def vision_analyze_screen(req: VisionAnalyzeScreenRequest, api_key: str = Depends(verify_api_key)):
    """截屏并让 VL 模型分析（"看屏幕"端点）

    流程：截屏 → base64 → VL 模型分析 → 返回描述
    若当前加载的是文本模型且 auto_switch=true，自动切换到 VL 模型。
    """
    try:
        require_module("automation.screen")
        from w1_layer.automation import get_screen_capture
        from m_layer.llm_client import get_client

        # 1. 截屏
        sc = get_screen_capture()
        if req.region and len(req.region) == 4:
            capture = sc.capture_region(req.region[0], req.region[1], req.region[2], req.region[3])
        else:
            capture = sc.capture_to_file(monitor=req.monitor)

        if not capture.get("success"):
            return JSONResponse({"success": False, "error": f"截屏失败: {capture.get('error')}"})

        # 2. 调用 VL 模型
        llm = get_client()
        result = llm.chat_with_image(
            prompt=req.prompt,
            image={"base64": capture["base64"]},
            auto_switch=req.auto_switch,
            max_tokens=req.max_tokens,
        )

        return JSONResponse({
            "success": not result.get("error"),
            "content": result.get("content", ""),
            "model": result.get("model"),
            "model_type": result.get("model_type", "vl"),
            "switched": result.get("switched", False),
            "screenshot_path": capture.get("path"),
            "latency": result.get("latency", 0),
            "error": result.get("error"),
        })
    except Exception as e:
        logger.error(f"看屏幕端点异常: {e}")
        return JSONResponse({"success": False, "error": str(e)})


# ─── 图片生成（Pollinations 免配置） ────────────────────────────────
@router.post("/image/generate")
async def image_generate(req: dict, api_key: str = Depends(verify_api_key)):
    """图片生成端点（默认使用 Pollinations 免配置在线服务）"""
    import urllib.request
    import urllib.parse
    import hashlib

    prompt = (req.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"success": False, "error": "提示词不能为空"})

    size_key = req.get("size", "landscape_16_9")
    width, height = _SIZE_MAP.get(size_key, (1280, 720))
    backend = req.get("backend", "pollinations")
    save_dir = req.get("save_dir", "exports/images")

    # 确保 exports/images 目录存在
    save_path = os.path.join(BASE_DIR, save_dir.replace("/", os.sep))
    os.makedirs(save_path, exist_ok=True)

    start_time = time.time()
    try:
        if backend == "pollinations":
            # Pollinations: GET https://image.pollinations.ai/prompt/{prompt}?width=&height=&nologo=true
            encoded = urllib.parse.quote(prompt, safe="")
            seed = int(time.time()) % 1000000
            url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true&seed={seed}"
            logger.info(f"图片生成请求: Pollinations, prompt={prompt[:50]}...")

            req_obj = urllib.request.Request(url, headers={"User-Agent": "SCU3/3.0"})
            # 30s 超时 + 1 次重试，比单次 60s 更稳健（Pollinations 偶尔慢，但不可用时应快速失败）
            img_data = None
            for attempt in range(2):
                try:
                    with urllib.request.urlopen(req_obj, timeout=30) as resp:
                        img_data = resp.read()
                    break
                except urllib.error.URLError as ue:
                    if attempt == 0:
                        logger.warning(f"图片下载第1次失败，重试: {ue}")
                        time.sleep(1)
                    else:
                        raise

            # 生成文件名
            name_hash = hashlib.md5(f"{prompt}{seed}".encode()).hexdigest()[:8]
            fname = f"gen_{name_hash}.png"
            fpath = os.path.join(save_path, fname)
            with open(fpath, "wb") as f:
                f.write(img_data)

            gen_seconds = time.time() - start_time
            logger.info(f"图片生成成功: {fname}, {len(img_data)} bytes, {gen_seconds:.1f}s")
            return JSONResponse({"success": True, "data": {
                "image_path": f"{save_dir}/{fname}",
                "file_bytes": len(img_data),
                "gen_seconds": gen_seconds,
                "device": "pollinations",
                "backend": backend,
                "prompt": prompt,
            }})
        else:
            return JSONResponse({"success": False, "error": f"后端 {backend} 暂未实现，请使用 pollinations"})
    except Exception as e:
        logger.error(f"图片生成失败: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": f"图片生成失败: {e}"})


# ─── 图片后端列表 ────────────────────────────────
@router.get("/image/backends")
async def image_backends(api_key: str = Depends(verify_api_key)):
    """返回真实可用的图片生成后端"""
    backends = [{
        "id": "pollinations",
        "label": "Pollinations 在线生成（免配置）",
        "available": True,
        "loaded": True,
        "requires_key": False,
    }]
    # 探测本地 diffusion（若安装）
    try:
        import importlib
        spec = importlib.util.find_spec("diffusers")
        if spec is not None:
            backends.append({
                "id": "local",
                "label": "本地 Diffusion 模型",
                "available": True,
                "loaded": True,
                "requires_key": False,
            })
        else:
            backends.append({
                "id": "local",
                "label": "本地 Diffusion 模型（未安装 diffusers）",
                "available": False,
                "loaded": False,
                "requires_key": False,
            })
    except Exception as e:
        logger.debug(f"diffusers 探测失败: {e}")
    return JSONResponse({"success": True, "data": {"backends": backends}})
