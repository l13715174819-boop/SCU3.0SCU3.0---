# -*- coding: utf-8 -*-
"""api/conversation.py — 会话管理路由

从 server.py 抽取的 10 个会话管理路由：
  POST   /conversation/start                  — 开始新对话会话
  POST   /conversation/{session_id}/message   — 添加消息到指定会话
  GET    /conversation/{session_id}/history   — 获取会话历史消息
  GET    /conversation/{session_id}/context   — 获取 LLM 注入上下文
  DELETE /conversation/{session_id}           — 删除会话
  GET    /conversation/sessions               — 列出所有会话
  GET    /conversations                       — 别名：返回记忆层会话列表
  POST   /conversations                       — 创建新会话
  GET    /conversations/{conv_id}/messages    — 获取会话消息列表
  GET    /conversations/{conv_id}/inject      — 预览会话注入上下文
"""
import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, get

logger = logging.getLogger("SCU3.api.conversation")

router = APIRouter(tags=["conversation"])


# ─── 请求模型 ────────────────────────────────
class ConversationStartRequest(BaseModel):
    user_id: str = "default_user"
    metadata: Dict[str, Any] = {}


class ConversationMessageRequest(BaseModel):
    role: str  # user/assistant/system
    content: str
    extra: Dict[str, Any] = {}


# ─── 多轮对话 ────────────────────────────────
@router.post("/conversation/start")
async def conversation_start(req: ConversationStartRequest, api_key: str = Depends(verify_api_key)):
    """开始新对话会话，返回session_id"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        session_id = get_conversation_manager().create_session(req.user_id, req.metadata)
        return JSONResponse({"success": True, "session_id": session_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/conversation/{session_id}/message")
async def conversation_add_message(session_id: str, req: ConversationMessageRequest,
                                    api_key: str = Depends(verify_api_key)):
    """添加消息到指定会话"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        ok = get_conversation_manager().add_message(session_id, req.role, req.content, req.extra)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/conversation/{session_id}/history")
async def conversation_history(session_id: str, limit: int = 10,
                                api_key: str = Depends(verify_api_key)):
    """获取会话历史消息"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        history = get_conversation_manager().get_history(session_id, limit)
        return JSONResponse({"success": True, "history": history})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/conversation/{session_id}/context")
async def conversation_context(session_id: str, limit: int = 10,
                                api_key: str = Depends(verify_api_key)):
    """获取LLM注入上下文（role/content格式）"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        ctx = get_conversation_manager().get_history_for_llm(session_id, limit)
        return JSONResponse({"success": True, "context": ctx})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.delete("/conversation/{session_id}")
async def conversation_delete(session_id: str, api_key: str = Depends(verify_api_key)):
    """删除会话"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        ok = get_conversation_manager().delete_session(session_id)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/conversation/sessions")
async def conversation_sessions(user_id: str = "", limit: int = 20,
                                 api_key: str = Depends(verify_api_key)):
    """列出所有会话"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        sessions = get_conversation_manager().list_sessions(user_id or None, limit)
        return JSONResponse({"success": True, "sessions": sessions})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 会话别名（前端 /conversations 路径） ────────────────────────────────
@router.get("/conversations")
async def conversations_alias(api_key: str = Depends(verify_api_key)):
    """别名：/conversations → 返回记忆层会话列表"""
    try:
        memory = get("memory")
        convs = memory.recall(limit=50)
        return JSONResponse({"success": True, "data": {"conversations": [
            {"id": str(i), "title": c.get("input", "")[:30], "created": c.get("timestamp", ""),
             "messages": 2} for i, c in enumerate(convs)
        ]}})
    except Exception as e:
        logger.debug(f"会话列表查询失败: {e}")
        return JSONResponse({"success": True, "data": {"conversations": []}})


@router.post("/conversations")
async def conversations_create(req: dict, api_key: str = Depends(verify_api_key)):
    """创建新会话（前端 POST /conversations 调用）"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        title = str(req.get("title", ""))[:100]
        sid = get_conversation_manager().create_session(
            user_id="default_user", metadata={"title": title})
        return JSONResponse({"success": True, "data": {"id": sid, "title": title}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/conversations/{conv_id}/messages")
async def conversation_messages(conv_id: str, api_key: str = Depends(verify_api_key)):
    """获取会话消息列表（前端 GET /conversations/{id}/messages 调用）"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        history = get_conversation_manager().get_history(conv_id, limit=50)
        return JSONResponse({"success": True, "data": {"messages": history}})
    except Exception as e:
        logger.debug(f"会话消息查询失败: {e}")
        return JSONResponse({"success": True, "data": {"messages": []}})


@router.get("/conversations/{conv_id}/inject")
async def conversation_inject(conv_id: str, n: int = 5,
                              api_key: str = Depends(verify_api_key)):
    """预览会话注入上下文（前端 GET /conversations/{id}/inject 调用）"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        history = get_conversation_manager().get_history_for_llm(conv_id, limit=n)
        text = "\n".join(f"[{m['role']}] {m['content']}" for m in history) if history else "(空)"
        return JSONResponse({"success": True, "data": {"text": text, "count": len(history)}})
    except Exception as e:
        logger.debug(f"会话导出查询失败: {e}")
        return JSONResponse({"success": True, "data": {"text": "(空)", "count": 0}})
