# -*- coding: utf-8 -*-
"""api/chat.py — 对话核心路由

从 server.py 抽取的 4 个对话路由：
  POST /chat         — 对话主入口
  POST /chat/stream  — SSE 流式聊天
  POST /feedback     — 反馈收集
  POST /chat/image   — 图片对话（需 VL 模型）
"""
import time
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, get

logger = logging.getLogger("SCU3.api.chat")

router = APIRouter(tags=["chat"])


# ─── 请求模型 ────────────────────────────────
class ChatRequest(BaseModel):
    prompt: str
    user_id: str = "default_user"


# ─── 对话路由 ────────────────────────────────
@router.post("/chat")
async def chat(req: ChatRequest, api_key: str = Depends(verify_api_key)):
    try:
        from server import process_request
        return JSONResponse(process_request(req.prompt, req.user_id))
    except Exception as e:
        import traceback as _tb
        logger.error(f"/chat 异常: {e}\n{_tb.format_exc()}")
        return JSONResponse({"response": f"处理失败: {e}", "error": str(e),
                             "traceback": _tb.format_exc()[-500:]}, status_code=500)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, api_key: str = Depends(verify_api_key)):
    """SSE流式聊天端点（任务2.1）

    修复：复用完整 process_request 流程（感知→记忆→执行→守卫→认知→工具/兜底），
    再将最终回复切片流式发送。原实现绕过了执行层与认知层，导致 web_search 等工具
    永不触发，LLM 凭空回答"不能联网"。
    """
    import asyncio
    from fastapi.responses import StreamingResponse
    import json as _json
    import re as _re

    from server import process_request

    # 在线程中执行完整流程（process_request 含 LLM 调用，会阻塞事件循环）
    try:
        result = await asyncio.to_thread(process_request, req.prompt, req.user_id)
    except Exception as e:
        logger.error(f"chat_stream process_request 异常: {e}", exc_info=True)
        result = {"success": False, "op_id": f"op_{int(time.time()*1000)}",
                  "response": f"处理失败: {e}", "cuf_traces": [], "balance": 0}

    op_id = result.get("op_id", f"op_{int(time.time()*1000)}")
    response_text = result.get("response", "")
    cuf_traces = result.get("cuf_traces", [])
    balance = result.get("balance", 0)
    pattern_key = result.get("pattern_key", "chat:plain")
    fallback = result.get("fallback", False)

    def event_stream():
        # 元数据（含守卫链trace，前端可展示）
        yield f"data: {_json.dumps({'type':'meta','op_id':op_id,'mode':result.get('llm_mode','deepseek'),'cuf_traces':cuf_traces,'history':0,'pattern_key':pattern_key,'fallback':fallback})}\n\n"
        # 切片流式发送（按标点/换行分块，模拟打字机效果）
        if response_text:
            chunks = _re.findall(r'[^，。！？；：\n]+[，。！？；：\n]?', response_text)
            if not chunks:
                chunks = [response_text]
            for chunk in chunks:
                yield f"data: {_json.dumps({'type':'chunk','content':chunk})}\n\n"
                time.sleep(0.02)  # 小延迟，前端有打字机观感
        # 结束标记
        yield f"data: {_json.dumps({'type':'done','op_id':op_id,'balance':balance})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.post("/chat/image")
async def chat_image(req: dict, api_key: str = Depends(verify_api_key)):
    """图片对话（需VL模型）"""
    from m_layer.llm_client import get_client
    client = get_client()
    image_data = req.get("image_data", "")
    prompt = req.get("prompt", "请描述这张图片")
    if not image_data:
        return JSONResponse({"success": False, "error": "未提供图片数据"})
    try:
        result = client.chat_with_image(prompt, image_data, auto_switch=True)
        return JSONResponse({"success": True, "data": {
            "response": result.get("content", ""),
            "model": result.get("model", ""),
            "model_type": result.get("model_type", "vl"),
            "switched": result.get("switched", False),
        }})
    except Exception as e:
        return JSONResponse({"success": False, "error": f"图片对话失败: {e}"})
