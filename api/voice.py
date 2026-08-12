# -*- coding: utf-8 -*-
"""api/voice.py — 语音IO路由

从 server.py 抽取的 7 个语音路由：
  POST /voice/recognize     — 语音识别（base64音频 → 文本）
  POST /voice/synthesize    — 语音合成（文本 → base64 WAV音频）
  GET  /voice/status        — 语音IO状态
  POST /voice/listen/start  — 启动实时持续语音监听
  POST /voice/listen/stop   — 停止语音监听
  GET  /voice/listen/status — 语音监听状态
  GET  /voice/listen/events — 获取语音监听事件（轮询）
"""
import time
import logging
from typing import Dict, Any, List
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key, require_module

logger = logging.getLogger("SCU3.api.voice")

router = APIRouter(tags=["voice"])

# 语音监听事件队列（前端轮询获取）
_voice_events: List[Dict[str, Any]] = []


# ─── 请求模型 ────────────────────────────────
class VoiceRecognizeRequest(BaseModel):
    audio_data: str  # base64编码的音频数据
    format: str = "wav"
    language: str = "zh"


class VoiceSynthesizeRequest(BaseModel):
    text: str
    lang: str = "zh"
    rate: int = 150
    pitch: int = 50
    volume: float = 1.0


class VoiceListenStartRequest(BaseModel):
    wake_word: str = ""  # 为空则直通模式（任何语音都触发）
    language: str = "zh"
    device_index: int = -1  # -1=默认设备
    auto_chat: bool = True  # 识别到语音后自动调用 LLM 生成回复


# ─── 基础语音IO ────────────────────────────────
@router.post("/voice/recognize")
async def voice_recognize(req: VoiceRecognizeRequest,
                           api_key: str = Depends(verify_api_key)):
    """语音识别（base64音频 → 文本）"""
    try:
        import base64
        from m_layer.voice_io import get_voice_io
        audio_bytes = base64.b64decode(req.audio_data)
        result = get_voice_io().recognize_detail(audio_bytes, format=req.format,
                                                  language=req.language)
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/voice/synthesize")
async def voice_synthesize(req: VoiceSynthesizeRequest,
                            api_key: str = Depends(verify_api_key)):
    """语音合成（文本 → base64 WAV音频）"""
    try:
        import base64
        from m_layer.voice_io import get_voice_io
        wav_bytes = get_voice_io().synthesize(req.text, lang=req.lang,
                                               rate=req.rate, pitch=req.pitch,
                                               volume=req.volume)
        if not wav_bytes:
            return JSONResponse({"success": False, "error": "合成失败"})
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
        return JSONResponse({"success": True, "audio_data": audio_b64,
                             "format": "wav", "size": len(wav_bytes)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/voice/status")
async def voice_status(api_key: str = Depends(verify_api_key)):
    """语音IO状态"""
    try:
        from m_layer.voice_io import get_voice_io
        return JSONResponse({"success": True, "status": get_voice_io().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 实时语音监听 ────────────────────────────────
@router.post("/voice/listen/start")
async def voice_listen_start(req: VoiceListenStartRequest, api_key: str = Depends(verify_api_key)):
    """启动实时持续语音监听

    - 直通模式（wake_word 为空）：检测到任何语音段即识别并回调
    - 唤醒词模式（wake_word 非空）：先识别唤醒词，命中后再识别命令

    若 auto_chat=true，识别到的文本会自动作为 prompt 调用 LLM 生成回复。
    """
    try:
        require_module("voice.listener")
        from m_layer.voice_io import get_listener
        listener = get_listener()

        if not listener.available:
            return JSONResponse({
                "success": False,
                "error": "pyaudio 不可用，无法采集麦克风。请执行: pip install pyaudio",
            })

        # 设置回调
        def on_utterance(text: str):
            logger.info(f"[语音监听] 识别到: {text}")
            # 记录到事件队列（前端可轮询 /voice/listen/events）
            try:
                _voice_events.append({"type": "utterance", "text": text, "ts": time.time()})
                # 保留最近 100 条
                if len(_voice_events) > 100:
                    _voice_events.pop(0)
            except Exception as e:
                logger.debug(f"语音事件入队失败: {e}")
            # 自动对话
            if req.auto_chat and text:
                try:
                    from m_layer.llm_client import get_client
                    llm = get_client()
                    reply = llm.chat(text)
                    reply_text = reply.get("content", "")
                    logger.info(f"[语音监听] 回复: {reply_text[:80]}")
                    _voice_events.append({
                        "type": "reply", "text": text,
                        "reply": reply_text, "ts": time.time(),
                    })
                except Exception as e:
                    logger.error(f"[语音监听] 自动对话失败: {e}")

        listener.on_utterance = on_utterance
        listener.on_wake_word = lambda: _voice_events.append({"type": "wake", "ts": time.time()})
        listener.on_state_change = lambda s: _voice_events.append({"type": "state", "state": s, "ts": time.time()})

        result = listener.start(
            wake_word=(req.wake_word or None),
            language=req.language,
            device_index=(req.device_index if req.device_index >= 0 else None),
        )
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"启动语音监听异常: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/voice/listen/stop")
async def voice_listen_stop(api_key: str = Depends(verify_api_key)):
    """停止语音监听"""
    try:
        require_module("voice.listener")
        from m_layer.voice_io import get_listener
        result = get_listener().stop()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/voice/listen/status")
async def voice_listen_status(api_key: str = Depends(verify_api_key)):
    """语音监听状态"""
    try:
        require_module("voice.listener")
        from m_layer.voice_io import get_listener
        return JSONResponse({"success": True, "status": get_listener().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/voice/listen/events")
async def voice_listen_events(api_key: str = Depends(verify_api_key), since: int = 0):
    """获取语音监听事件（轮询）

    Args:
        since: 返回此时间戳之后的事件（0=全部最近 100 条）
    """
    events = [e for e in _voice_events if e.get("ts", 0) > since]
    return JSONResponse({"success": True, "events": events, "count": len(events)})
