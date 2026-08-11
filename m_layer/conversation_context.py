# -*- coding: utf-8 -*-
"""
m_layer/conversation_context.py — 多轮对话上下文管理器（M层）
==============================================================
v5.0第一批：支持多轮对话，维护对话历史，让Agent能"追问/修正/回溯"

能力对标：AI助手的多轮对话能力（上下文累积、追问、修正）

功能:
  1. 按session_id维护对话历史
  2. 上下文窗口管理（保留最近N轮）
  3. 对话历史注入到LLM提示词
  4. 引用历史消息（"刚才那个文件再分析一下"）
  5. 持久化到磁盘

架构归属：M层（认知层对话管理）
"""
import os
import json
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("scu2.m.conv")


class ConversationContext:
    """多轮对话上下文管理器

    用法:
        ctx_mgr = ConversationContext()
        session_id = ctx_mgr.create_session("user_001")
        ctx_mgr.add_message(session_id, "user", "分析readme.md")
        ctx_mgr.add_message(session_id, "assistant", "分析结果...")
        # 获取历史用于LLM
        history = ctx_mgr.get_history(session_id)
        # 引用历史
        ref = ctx_mgr.resolve_reference(session_id, "刚才那个文件")
    """

    MAX_HISTORY = 20  # 每session最多保留20轮
    MAX_SESSIONS = 100  # 最多100个活跃session

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, "scu2_data")
        self._data_dir = data_dir
        self._state_path = os.path.join(data_dir, "conversations.json")
        os.makedirs(data_dir, exist_ok=True)

        self._lock = threading.Lock()
        # session_id → {messages: [], created_at, last_active, user_id, metadata}
        self._sessions: Dict[str, Dict] = {}

        self._load_state()

    def create_session(self, user_id: str = "default_user",
                       metadata: Optional[Dict] = None) -> str:
        """创建新对话session"""
        import uuid
        session_id = f"conv_{uuid.uuid4().hex[:8]}"

        with self._lock:
            # 清理过多session
            if len(self._sessions) >= self.MAX_SESSIONS:
                # 删除最老的
                oldest = min(self._sessions.items(), key=lambda x: x[1]["last_active"])
                del self._sessions[oldest[0]]

            self._sessions[session_id] = {
                "user_id": user_id,
                "messages": [],
                "created_at": datetime.now().isoformat(),
                "last_active": datetime.now().isoformat(),
                "metadata": metadata or {},
            }
            self._save_state()

        logger.info(f"创建对话session: {session_id} (user={user_id})")
        return session_id

    def add_message(self, session_id: str, role: str, content: str,
                    extra: Optional[Dict] = None) -> bool:
        """添加对话消息

        Args:
            session_id: 会话ID
            role: user/assistant/system
            content: 消息内容
            extra: 额外元数据（如工具调用、执行结果等）
        """
        with self._lock:
            if session_id not in self._sessions:
                return False

            msg = {
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
            }
            if extra:
                msg["extra"] = extra

            self._sessions[session_id]["messages"].append(msg)
            self._sessions[session_id]["last_active"] = datetime.now().isoformat()

            # 截断历史
            msgs = self._sessions[session_id]["messages"]
            if len(msgs) > self.MAX_HISTORY * 2:  # user+assistant各算1条
                self._sessions[session_id]["messages"] = msgs[-(self.MAX_HISTORY * 2):]

            self._save_state()
        return True

    def get_history(self, session_id: str, limit: int = 10) -> List[Dict]:
        """获取对话历史"""
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return []
            return session["messages"][-limit:]

    def get_history_for_llm(self, session_id: str, limit: int = 10) -> List[Dict]:
        """获取LLM格式的对话历史

        Returns:
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        history = self.get_history(session_id, limit)
        return [{"role": m["role"], "content": m["content"]} for m in history]

    def resolve_reference(self, session_id: str, query: str) -> Optional[Dict]:
        """解析历史引用（如"刚才那个文件"）

        从最近的消息中查找相关上下文
        """
        history = self.get_history(session_id, limit=6)
        if not history:
            return None

        # 简单关键词匹配
        query_lower = query.lower()
        ref_keywords = ["刚才", "那个", "之前", "上面", "上一个", "last", "previous", "that"]

        # 查找引用目标
        for msg in reversed(history):
            if msg["role"] == "user":
                content = msg["content"]
                # 查找文件名
                import re
                files = re.findall(r'[\w.-]+\.(?:md|txt|py|json|csv|html)', content, re.I)
                if files:
                    return {"type": "file", "value": files[-1], "source_msg": msg}
                # 查找数字结果
                numbers = re.findall(r'\d+\.?\d*', content)
                if numbers and any(kw in query_lower for kw in ref_keywords):
                    return {"type": "number", "value": numbers[-1], "source_msg": msg}

        return None

    def summarize_session(self, session_id: str) -> Optional[Dict]:
        """获取session摘要"""
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            msgs = session["messages"]
            return {
                "session_id": session_id,
                "user_id": session["user_id"],
                "message_count": len(msgs),
                "created_at": session["created_at"],
                "last_active": session["last_active"],
                "first_message": msgs[0]["content"][:100] if msgs else "",
                "last_message": msgs[-1]["content"][:100] if msgs else "",
            }

    def list_sessions(self, user_id: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """列出所有session"""
        with self._lock:
            sessions = []
            for sid, s in self._sessions.items():
                if user_id and s["user_id"] != user_id:
                    continue
                sessions.append({
                    "session_id": sid,
                    "user_id": s["user_id"],
                    "message_count": len(s["messages"]),
                    "last_active": s["last_active"],
                })
            sessions.sort(key=lambda x: x["last_active"], reverse=True)
            return sessions[:limit]

    def clear_session(self, session_id: str) -> bool:
        """清空session"""
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["messages"] = []
                self._save_state()
                return True
            return False

    def delete_session(self, session_id: str) -> bool:
        """删除session"""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                self._save_state()
                return True
            return False

    def _load_state(self) -> None:
        if os.path.exists(self._state_path):
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    data = json.loads(f.read())
                self._sessions = data.get("sessions", {})
                # P2修复：解密 user_id 和 messages[].content
                try:
                    from guard.data_crypto import decrypt_field, is_encrypted
                    for sid, sess in self._sessions.items():
                        if sess.get("user_id") and is_encrypted(sess["user_id"]):
                            sess["user_id"] = decrypt_field(sess["user_id"])
                        for msg in sess.get("messages", []):
                            if msg.get("content") and is_encrypted(msg["content"]):
                                msg["content"] = decrypt_field(msg["content"])
                except Exception as _e:
                    logger.debug(f"解密对话内容跳过(非阻断): {_e}")
                logger.info(f"加载对话历史: {len(self._sessions)}个session")
            except Exception as e:
                logger.warning(f"加载对话历史失败: {e}")

    def _save_state(self) -> None:
        try:
            # P2修复：加密 user_id 和 messages[].content 后再落盘
            try:
                from guard.data_crypto import encrypt_field
                encrypted_sessions = {}
                for sid, sess in self._sessions.items():
                    enc_sess = dict(sess)
                    enc_sess["user_id"] = encrypt_field(sess.get("user_id", ""))
                    enc_msgs = []
                    for msg in sess.get("messages", []):
                        enc_msg = dict(msg)
                        if msg.get("content"):
                            enc_msg["content"] = encrypt_field(msg["content"])
                        enc_msgs.append(enc_msg)
                    enc_sess["messages"] = enc_msgs
                    encrypted_sessions[sid] = enc_sess
                payload = {"sessions": encrypted_sessions, "encrypted": True}
            except Exception as _e:
                logger.debug(f"加密对话内容跳过(明文落盘): {_e}")
                payload = {"sessions": self._sessions}
            with open(self._state_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存对话历史失败: {e}")


# ─── 单例 ────────────────────────────────────
_conv_instance: Optional[ConversationContext] = None


def get_conversation_manager() -> ConversationContext:
    """获取对话管理器单例"""
    global _conv_instance
    if _conv_instance is None:
        _conv_instance = ConversationContext()
    return _conv_instance
