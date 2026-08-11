# -*- coding: utf-8 -*-
"""
m_layer/tool_permissions.py — 工具权限分级管理（M层）
======================================================
工具调用的4级权限分级与管控。

能力对标：AI助手调用工具前的"权限校验→敏感确认→危险审批"环节

功能：
  1. 4级权限分级（L0公开 / L1普通 / L2敏感 / L3危险）
  2. 用户权限检查 check_permission(user_level, tool_name)
  3. 敏感操作确认机制 require_confirmation(tool_name)
  4. 危险操作审批机制 require_approval(tool_name, user_id)
  5. 权限提升申请 apply_elevation(user_id, requested_level, reason)
  6. 权限审计日志记录
  7. 状态持久化到 scu2_data/tool_permissions.json

架构归属：M层（权限管理层）
依赖：无（纯本地状态）
"""
import os
import json
import uuid
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger("scu2.m.tool_perm")

# ─── 权限等级定义 ────────────────────────────────
# 4级权限分级：L0 < L1 < L2 < L3
PERMISSION_LEVELS: Dict[str, Dict[str, str]] = {
    "L0": {"name": "public",    "label": "公开", "desc": "所有用户可用（只读工具）"},
    "L1": {"name": "normal",    "label": "普通", "desc": "登录用户可用"},
    "L2": {"name": "sensitive", "label": "敏感", "desc": "需二次确认（写操作）"},
    "L3": {"name": "dangerous", "label": "危险", "desc": "需管理员审批"},
}

# 等级数值（用于大小比较）
_LEVEL_ORDER: Dict[str, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}

# ─── 用户角色 → 最高权限等级 ────────────────────────
USER_LEVELS: Dict[str, str] = {
    "guest":      "L0",  # 访客
    "user":       "L1",  # 普通用户
    "power_user": "L2",  # 高级用户
    "admin":      "L3",  # 管理员
}

# ─── 默认工具权限映射表（tool_name → level）─────────
DEFAULT_TOOL_PERMISSIONS: Dict[str, str] = {
    # L0 公开（所有用户可用，只读工具）
    "calculator":      "L0",
    "time_now":        "L0",
    "weather":         "L0",
    "exchange_rate":   "L0",
    "crypto_price":    "L0",
    "stock_price":     "L0",
    "github_search":   "L0",
    "datetime_calc":   "L0",
    "unit_convert":    "L0",
    # L1 普通（登录用户可用）
    "file_read":       "L1",
    "text_stats":      "L1",
    # L2 敏感（需二次确认，写操作）
    "file_write":      "L2",
    "code_run":        "L2",
    # L3 危险（需管理员审批）
    "self_modify":     "L3",
    "system_config":   "L3",
}


class ToolPermissionManager:
    """工具权限分级管理器

    用法:
        mgr = get_permission_manager()
        # 1. 权限检查
        ok, reason = mgr.check_permission("user", "file_read")
        # 2. 敏感操作是否需要确认
        need_confirm = mgr.require_confirmation("file_write")
        # 3. 创建敏感确认请求
        cfm_id = mgr.create_confirmation("file_write", "user_001")
        # 4. 危险操作申请审批
        approval_id = mgr.require_approval("self_modify", "user_001")
        # 5. 权限提升申请
        req_id = mgr.apply_elevation("user_001", "L2", "需要文件写入能力")
    """

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, "scu2_data")
        self._data_dir = data_dir
        os.makedirs(self._data_dir, exist_ok=True)
        self._state_path = os.path.join(self._data_dir, "tool_permissions.json")

        self._lock = threading.Lock()
        # 工具权限映射表
        self._tool_permissions: Dict[str, str] = dict(DEFAULT_TOOL_PERMISSIONS)
        # 待确认记录 / 待审批记录 / 提升申请 / 审计日志
        self._pending_confirmations: List[Dict] = []
        self._pending_approvals: List[Dict] = []
        self._elevation_requests: List[Dict] = []
        self._audit_log: List[Dict] = []

        # 加载持久化状态
        self._load()

    # ─── 持久化 ────────────────────────────────────
    def _load(self) -> None:
        """从磁盘加载状态"""
        if not os.path.exists(self._state_path):
            logger.info("权限状态文件不存在，使用默认配置")
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                state = json.loads(f.read())
            # 合并工具权限（自定义覆盖默认）
            custom = state.get("tool_permissions", {})
            self._tool_permissions.update(custom)
            self._pending_confirmations = state.get("pending_confirmations", [])
            self._pending_approvals = state.get("pending_approvals", [])
            self._elevation_requests = state.get("elevation_requests", [])
            self._audit_log = state.get("audit_log", [])
            logger.info(f"权限状态已加载: {len(self._tool_permissions)}个工具, "
                        f"{len(self._audit_log)}条审计日志")
        except Exception as e:
            logger.warning(f"加载权限状态失败: {e}，使用默认配置")

    def _save(self) -> None:
        """持久化状态到磁盘"""
        state = {
            "tool_permissions": self._tool_permissions,
            "pending_confirmations": self._pending_confirmations,
            "pending_approvals": self._pending_approvals,
            "elevation_requests": self._elevation_requests,
            "audit_log": self._audit_log,
            "updated_at": datetime.now().isoformat(),
        }
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存权限状态失败: {e}")

    # ─── 审计日志 ──────────────────────────────────
    def _log_audit(self, event: str, user_id: str, tool_name: str,
                   result: str, details: str = "") -> None:
        """记录权限审计日志"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event,           # check/confirm_create/confirm_resolve/...
            "user_id": user_id,
            "tool_name": tool_name,
            "result": result,         # allowed/denied/pending/confirmed/...
            "details": details,
        }
        # 限制审计日志条数，避免无限增长
        self._audit_log.append(entry)
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-1000:]
        logger.debug(f"审计[{event}] user={user_id} tool={tool_name} → {result}")

    # ─── 等级解析 ──────────────────────────────────
    @staticmethod
    def _resolve_user_level(user_level: str) -> Optional[str]:
        """将用户等级（角色名或L级别）解析为L级别字符串

        支持 guest/user/power_user/admin 角色名，或 L0~L3 等级字符串
        """
        if user_level in _LEVEL_ORDER:
            return user_level
        if user_level in USER_LEVELS:
            return USER_LEVELS[user_level]
        return None

    def get_tool_level(self, tool_name: str) -> str:
        """获取工具所需权限等级（未知工具默认L1）"""
        return self._tool_permissions.get(tool_name, "L1")

    # ─── 权限检查 ──────────────────────────────────
    def check_permission(self, user_level: str, tool_name: str) -> Tuple[bool, str]:
        """权限检查：判断用户能否使用某工具

        Args:
            user_level: 用户角色（guest/user/power_user/admin）或权限等级（L0~L3）
            tool_name: 工具名

        Returns:
            (allowed, reason)
        """
        user_lvl = self._resolve_user_level(user_level)
        if user_lvl is None:
            reason = f"未知用户等级: {user_level}"
            self._log_audit("check", str(user_level), tool_name, "denied", reason)
            logger.warning(f"权限检查失败: {reason}")
            return False, reason

        tool_lvl = self.get_tool_level(tool_name)
        user_val = _LEVEL_ORDER.get(user_lvl, 0)
        tool_val = _LEVEL_ORDER.get(tool_lvl, 0)

        if user_val >= tool_val:
            reason = (f"权限通过: 用户{user_lvl}({PERMISSION_LEVELS[user_lvl]['label']})"
                      f" ≥ 工具{tool_name}({tool_lvl})")
            self._log_audit("check", user_level, tool_name, "allowed", reason)
            logger.info(f"权限通过: {user_lvl} → {tool_name}({tool_lvl})")
            return True, reason
        else:
            reason = (f"权限不足: 用户{user_lvl}({PERMISSION_LEVELS[user_lvl]['label']})"
                      f" < 工具{tool_name}({tool_lvl})")
            self._log_audit("check", user_level, tool_name, "denied", reason)
            logger.warning(f"权限拒绝: {reason}")
            return False, reason

    # ─── 敏感操作确认 ──────────────────────────────
    def require_confirmation(self, tool_name: str) -> bool:
        """判断工具是否需要二次确认（L2敏感操作）

        Args:
            tool_name: 工具名

        Returns:
            True 表示需要二次确认
        """
        tool_lvl = self.get_tool_level(tool_name)
        need = (tool_lvl == "L2")
        logger.debug(f"敏感确认检查: {tool_name}({tool_lvl}) → need_confirmation={need}")
        return need

    def create_confirmation(self, tool_name: str, user_id: str) -> str:
        """创建敏感操作确认请求

        Args:
            tool_name: 工具名
            user_id: 用户ID

        Returns:
            confirmation_id
        """
        confirmation_id = f"cfm_{uuid.uuid4().hex[:12]}"
        record = {
            "confirmation_id": confirmation_id,
            "tool_name": tool_name,
            "user_id": user_id,
            "tool_level": self.get_tool_level(tool_name),
            "status": "pending",  # pending / confirmed / denied
            "created_at": datetime.now().isoformat(),
            "resolved_at": None,
            "resolver": None,
        }
        with self._lock:
            self._pending_confirmations.append(record)
            self._log_audit("confirm_create", user_id, tool_name, "pending",
                            f"confirmation_id={confirmation_id}")
            self._save()
        logger.info(f"敏感确认请求已创建: {tool_name} user={user_id} id={confirmation_id}")
        return confirmation_id

    def resolve_confirmation(self, confirmation_id: str, confirmed: bool,
                             resolver: str = "") -> bool:
        """处理敏感操作确认请求

        Args:
            confirmation_id: 确认ID
            confirmed: 是否确认通过
            resolver: 确认人

        Returns:
            是否处理成功
        """
        with self._lock:
            for rec in self._pending_confirmations:
                if rec["confirmation_id"] == confirmation_id and rec["status"] == "pending":
                    rec["status"] = "confirmed" if confirmed else "denied"
                    rec["resolved_at"] = datetime.now().isoformat()
                    rec["resolver"] = resolver
                    self._log_audit("confirm_resolve", rec["user_id"], rec["tool_name"],
                                    "confirmed" if confirmed else "denied",
                                    f"resolver={resolver}")
                    self._save()
                    logger.info(f"敏感确认已处理: {confirmation_id} → "
                                f"{'confirmed' if confirmed else 'denied'}")
                    return True
        logger.warning(f"敏感确认记录不存在或已处理: {confirmation_id}")
        return False

    # ─── 危险操作审批 ──────────────────────────────
    def require_approval(self, tool_name: str, user_id: str) -> str:
        """为危险操作（L3）创建审批请求

        Args:
            tool_name: 工具名
            user_id: 用户ID

        Returns:
            approval_id（审批ID）
        """
        approval_id = f"apv_{uuid.uuid4().hex[:12]}"
        record = {
            "approval_id": approval_id,
            "tool_name": tool_name,
            "user_id": user_id,
            "tool_level": self.get_tool_level(tool_name),
            "status": "pending",  # pending / approved / rejected
            "created_at": datetime.now().isoformat(),
            "resolved_at": None,
            "resolved_by": None,
        }
        with self._lock:
            self._pending_approvals.append(record)
            self._log_audit("approval_create", user_id, tool_name, "pending",
                            f"approval_id={approval_id}")
            self._save()
        logger.info(f"危险操作审批请求已创建: {tool_name} user={user_id} id={approval_id}")
        return approval_id

    def resolve_approval(self, approval_id: str, approved: bool,
                         approver: str = "admin") -> bool:
        """处理危险操作审批请求

        Args:
            approval_id: 审批ID
            approved: 是否批准
            approver: 审批人

        Returns:
            是否处理成功
        """
        with self._lock:
            for rec in self._pending_approvals:
                if rec["approval_id"] == approval_id and rec["status"] == "pending":
                    rec["status"] = "approved" if approved else "rejected"
                    rec["resolved_at"] = datetime.now().isoformat()
                    rec["resolved_by"] = approver
                    self._log_audit("approval_resolve", rec["user_id"], rec["tool_name"],
                                    "approved" if approved else "rejected",
                                    f"approver={approver}")
                    self._save()
                    logger.info(f"危险操作审批已处理: {approval_id} → "
                                f"{'approved' if approved else 'rejected'}")
                    return True
        logger.warning(f"审批记录不存在或已处理: {approval_id}")
        return False

    def is_approval_granted(self, approval_id: str) -> bool:
        """查询审批是否已批准"""
        for rec in self._pending_approvals:
            if rec["approval_id"] == approval_id:
                return rec["status"] == "approved"
        return False

    # ─── 权限提升申请 ──────────────────────────────
    def apply_elevation(self, user_id: str, requested_level: str,
                        reason: str) -> str:
        """用户申请权限提升

        Args:
            user_id: 用户ID
            requested_level: 申请的目标等级（L0~L3 或 角色名 guest/user/power_user/admin）
            reason: 申请理由

        Returns:
            request_id（提升申请ID）
        """
        target = self._resolve_user_level(requested_level)
        if target is None:
            raise ValueError(f"非法的目标权限等级: {requested_level}")

        request_id = f"elv_{uuid.uuid4().hex[:12]}"
        record = {
            "request_id": request_id,
            "user_id": user_id,
            "requested_level": target,
            "reason": reason,
            "status": "pending",  # pending / approved / rejected
            "created_at": datetime.now().isoformat(),
            "resolved_at": None,
            "resolved_by": None,
        }
        with self._lock:
            self._elevation_requests.append(record)
            self._log_audit("elevation_apply", user_id, "", "pending",
                            f"target={target} reason={reason}")
            self._save()
        logger.info(f"权限提升申请已创建: user={user_id} target={target} id={request_id}")
        return request_id

    def resolve_elevation(self, request_id: str, approved: bool,
                          approver: str = "admin") -> bool:
        """处理权限提升申请

        Args:
            request_id: 提升申请ID
            approved: 是否批准
            approver: 审批人

        Returns:
            是否处理成功
        """
        with self._lock:
            for rec in self._elevation_requests:
                if rec["request_id"] == request_id and rec["status"] == "pending":
                    rec["status"] = "approved" if approved else "rejected"
                    rec["resolved_at"] = datetime.now().isoformat()
                    rec["resolved_by"] = approver
                    self._log_audit("elevation_resolve", rec["user_id"], "",
                                    "approved" if approved else "rejected",
                                    f"target={rec['requested_level']} approver={approver}")
                    self._save()
                    logger.info(f"权限提升申请已处理: {request_id} → "
                                f"{'approved' if approved else 'rejected'}")
                    return True
        logger.warning(f"提升申请不存在或已处理: {request_id}")
        return False

    # ─── 查询接口 ──────────────────────────────────
    def list_pending_approvals(self) -> List[Dict]:
        """列出待处理的危险操作审批"""
        return [r for r in self._pending_approvals if r["status"] == "pending"]

    def list_pending_elevations(self) -> List[Dict]:
        """列出待处理的权限提升申请"""
        return [r for r in self._elevation_requests if r["status"] == "pending"]

    def get_audit_log(self, limit: int = 100) -> List[Dict]:
        """获取最近的审计日志（默认100条，按时间倒序）"""
        return list(reversed(self._audit_log[-limit:]))

    def list_tools_by_level(self, level: Optional[str] = None) -> Dict[str, List[str]]:
        """按权限等级列出工具

        Args:
            level: 指定等级（L0~L3），None则返回全部

        Returns:
            {等级: [工具名, ...]}
        """
        result: Dict[str, List[str]] = {}
        for tool, lvl in self._tool_permissions.items():
            if level is not None and lvl != level:
                continue
            result.setdefault(lvl, []).append(tool)
        return result

    def register_tool(self, tool_name: str, level: str) -> bool:
        """注册/更新工具权限等级

        Args:
            tool_name: 工具名
            level: 权限等级（L0~L3）

        Returns:
            是否成功
        """
        if level not in _LEVEL_ORDER:
            logger.warning(f"非法权限等级: {level}")
            return False
        with self._lock:
            self._tool_permissions[tool_name] = level
            self._log_audit("register", "", tool_name, "ok", f"level={level}")
            self._save()
        logger.info(f"工具权限已注册: {tool_name} → {level}")
        return True

    def get_status(self) -> Dict[str, Any]:
        """获取权限管理器状态摘要"""
        return {
            "total_tools": len(self._tool_permissions),
            "by_level": {
                lvl: sum(1 for v in self._tool_permissions.values() if v == lvl)
                for lvl in _LEVEL_ORDER
            },
            "pending_approvals": sum(1 for r in self._pending_approvals if r["status"] == "pending"),
            "pending_confirmations": sum(1 for r in self._pending_confirmations if r["status"] == "pending"),
            "pending_elevations": sum(1 for r in self._elevation_requests if r["status"] == "pending"),
            "audit_log_size": len(self._audit_log),
            "state_path": self._state_path,
        }


# ─── 单例 ────────────────────────────────────
_permission_instance: Optional[ToolPermissionManager] = None


def get_permission_manager() -> ToolPermissionManager:
    """获取工具权限管理器单例"""
    global _permission_instance
    if _permission_instance is None:
        _permission_instance = ToolPermissionManager()
    return _permission_instance
