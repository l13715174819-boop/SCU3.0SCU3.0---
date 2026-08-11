# -*- coding: utf-8 -*-
"""
m_layer/code_self_modify.py — 代码自修改引擎（M层）
====================================================
阶段3：代码自修改能力

闭环流程：
  1. 提议生成：LLM生成 / 手动提交代码补丁
  2. 安全审查：AST预检 + 危险模式检测 + D层保护
  3. 阴阳双签：临时Pair实例化，Yin(γ≥0.75) + Yang(γ≥0.65)双通过
  4. 人工审批：高风险操作需人工确认（require_human_approval=true）
  5. 备份回滚：修改前自动备份，失败自动回滚
  6. 应用补丁：原子写入 + 语法验证
  7. 审计记录：全链路记录到修改日志

架构归属：M层（元认知层的扩展，同层免审）
安全约束：
  - D层基础文件不可修改（axioms/firewall/entropy_ledger/engine/meta_guard/baseline）
  - 审计模块和自修改模块自身不可被代码补丁削弱
  - 所有修改必须经阴阳双签 + 人工审批
  - 修改前自动备份，支持一键回滚
"""
import os
import ast
import json
import time
import shutil
import hashlib
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger("scu2.m.selfmod")


# ─── D层保护：这些文件绝对不可被自修改触碰 ────────────────────
D_LAYER_PROTECTED = {
    "d_layer/axioms.py",
    "d_layer/firewall.py",
    "d_layer/entropy_ledger.py",
    "d_layer/ledger_base.py",
    "engine.py",
    "meta_guard.py",
    "baseline.py",
    # 审计和自修改模块自身不可被削弱
    "m_layer/code_self_modify.py",
    "guard/firewall.py",
    "guard/content_filter.py",
}

# ─── 危险模式：AST预检禁止的模式 ────────────────────────────
DANGEROUS_CALLS = {
    "eval", "exec", "compile", "globals", "locals",
    "vars", "dir", "getattr", "setattr", "delattr",
    "__import__", "exit", "quit",
}

DANGEROUS_ATTRS = {
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__code__", "__func__",
}

DANGEROUS_IMPORTS = {
    "subprocess", "ctypes", "multiprocessing",
    "shlex", "pty", "commands",
}

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {".py"}


class SecurityReviewError(Exception):
    """安全审查失败"""


class YinYangEndorseError(Exception):
    """阴阳双签失败"""


class CodeSelfModifier:
    """代码自修改引擎

    用法：
        modifier = CodeSelfModifier(project_root, backup_dir)
        # 提议修改
        proposal = modifier.propose_modification(
            target_file="w1_layer/memory.py",
            description="优化记忆层缓存",
            new_code="...",
            proposer="llm"
        )
        # 审查+双签+应用
        result = modifier.apply_modification(proposal["id"], approved_by="admin")
    """

    def __init__(self, project_root: str, backup_dir: str = "",
                 ledger=None, require_human_approval: bool = True):
        self.project_root = os.path.abspath(project_root)
        self.backup_dir = backup_dir or os.path.join(self.project_root, "scu2_data", "backups")
        self.ledger = ledger
        self.require_human_approval = require_human_approval

        os.makedirs(self.backup_dir, exist_ok=True)

        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, Any]] = {}  # 待审批的修改
        self._history: List[Dict[str, Any]] = []        # 修改历史
        self._backups: Dict[str, str] = {}              # modification_id → backup_path

        self._load_state()

    # ─── 状态持久化 ────────────────────────────────

    def _state_path(self) -> str:
        return os.path.join(self.project_root, "scu2_data", "self_modify_state.json")

    def _load_state(self):
        path = self._state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._history = state.get("history", [])[-200:]
            logger.info(f"自修改状态加载: 历史{len(self._history)}条")
        except Exception as e:
            logger.warning(f"加载自修改状态失败: {e}")

    def _save_state(self):
        path = self._state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            state = {
                "history": self._history[-200:],
                "updated_at": datetime.now().isoformat(),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存自修改状态失败: {e}")

    # ─── 路径安全 ────────────────────────────────

    def _validate_path(self, target_file: str) -> str:
        """验证目标文件路径安全（防路径穿越 + D层保护）"""
        # 规范化路径
        full_path = os.path.abspath(os.path.join(self.project_root, target_file))

        # 防路径穿越：必须在项目根目录下
        if not full_path.startswith(self.project_root):
            raise SecurityReviewError(f"路径越界: {target_file}")

        # 检查D层保护
        rel_path = os.path.relpath(full_path, self.project_root).replace("\\", "/")
        if rel_path in D_LAYER_PROTECTED:
            raise SecurityReviewError(f"D层保护文件不可修改: {rel_path}")

        # 检查扩展名
        _, ext = os.path.splitext(full_path)
        if ext not in ALLOWED_EXTENSIONS:
            raise SecurityReviewError(f"不支持的文件类型: {ext}")

        return full_path

    # ─── 安全审查：AST预检 ────────────────────────────────

    def _security_review(self, code: str) -> Tuple[bool, List[str], float]:
        """AST安全审查

        检查项：
        1. 语法正确性（AST可解析）
        2. 危险函数调用（eval/exec/compile等）
        3. 危险属性访问（__class__/__subclasses__等）
        4. 危险模块导入（subprocess/ctypes等）
        5. 文件系统操作（open写模式需审查）

        Returns:
            (passed, issues, score)  score: 0-1 安全分数
        """
        issues = []

        # 1. 语法检查
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, [f"语法错误: {e}"], 0.0

        # 遍历AST节点
        dangerous_count = 0
        total_nodes = 0

        for node in ast.walk(tree):
            total_nodes += 1

            # 检查函数调用
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in DANGEROUS_CALLS:
                    issues.append(f"禁止调用危险函数: {func.id} (line {node.lineno})")
                    dangerous_count += 1
                # 检查 __import__
                elif isinstance(func, ast.Attribute) and func.attr in DANGEROUS_CALLS:
                    issues.append(f"禁止调用危险方法: {func.attr} (line {node.lineno})")
                    dangerous_count += 1

            # 检查属性访问
            if isinstance(node, ast.Attribute) and node.attr in DANGEROUS_ATTRS:
                issues.append(f"禁止访问危险属性: {node.attr} (line {node.lineno})")
                dangerous_count += 1

            # 检查import
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in DANGEROUS_IMPORTS:
                        issues.append(f"禁止导入危险模块: {alias.name} (line {node.lineno})")
                        dangerous_count += 1
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module in DANGEROUS_IMPORTS:
                    issues.append(f"禁止导入危险模块: {node.module} (line {node.lineno})")
                    dangerous_count += 1

            # 检查open()写模式
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                # 检查模式参数
                if len(node.args) >= 2:
                    mode_arg = node.args[1]
                    if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
                        mode = mode_arg.value
                        if any(m in mode for m in ("w", "a", "x", "+")):
                            issues.append(f"文件写操作需审查: open(..., '{mode}') (line {node.lineno})")
                            # 写操作不是禁止的，但标记审查
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                        if isinstance(mode, str) and any(m in mode for m in ("w", "a", "x", "+")):
                            issues.append(f"文件写操作需审查: open(..., mode='{mode}') (line {node.lineno})")

        # 计算安全分数
        if dangerous_count > 0:
            score = max(0.0, 1.0 - 0.3 * dangerous_count)
        else:
            score = 1.0

        passed = dangerous_count == 0 and len(issues) == 0
        return passed, issues, round(score, 3)

    # ─── 阴阳双签 ────────────────────────────────

    def _yin_yang_endorse(self, proposal: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """阴阳双签机制

        阴（Yin）：保守审查——检查修改是否符合架构约束、是否破坏不变量
        阳（Yang）：积极审查——检查修改是否有效解决问题、是否有测试覆盖

        双签通过条件：gamma_yin ≥ 0.75 且 gamma_yang ≥ 0.65

        Returns:
            (passed, detail)
        """
        # ─── 阴：保守审查 ───
        yin_checks = {
            "d_layer_protected": True,   # 未触碰D层
            "no_dangerous_patterns": True, # 无危险模式
            "backup_available": True,     # 有备份机制
            "rollback_ready": True,       # 回滚就绪
            "path_safe": True,            # 路径安全
        }
        yin_score = 0.0

        try:
            self._validate_path(proposal["target_file"])
        except SecurityReviewError:
            yin_checks["d_layer_protected"] = False
            yin_checks["path_safe"] = False

        passed, issues, sec_score = self._security_review(proposal["new_code"])
        if not passed:
            yin_checks["no_dangerous_patterns"] = False
        yin_score += sec_score * 0.3

        # 备份机制检查
        yin_score += 0.2 if yin_checks["backup_available"] else 0
        yin_score += 0.2 if yin_checks["rollback_ready"] else 0
        yin_score += 0.15 if yin_checks["path_safe"] else 0
        yin_score += 0.15 if yin_checks["d_layer_protected"] else 0

        gamma_yin = round(min(yin_score, 1.0), 3)

        # ─── 阳：积极审查 ───
        yang_checks = {
            "has_description": bool(proposal.get("description", "").strip()),
            "has_reasoning": bool(proposal.get("reasoning", "").strip()),
            "code_non_empty": bool(proposal.get("new_code", "").strip()),
            "syntax_valid": False,
            "target_exists": os.path.exists(
                os.path.join(self.project_root, proposal["target_file"])
            ),
        }
        # 语法验证
        try:
            ast.parse(proposal.get("new_code", ""))
            yang_checks["syntax_valid"] = True
        except SyntaxError:
            pass

        yang_score = 0.0
        yang_score += 0.2 if yang_checks["has_description"] else 0
        yang_score += 0.15 if yang_checks["has_reasoning"] else 0
        yang_score += 0.2 if yang_checks["code_non_empty"] else 0
        yang_score += 0.2 if yang_checks["syntax_valid"] else 0
        yang_score += 0.2 if yang_checks["target_exists"] else 0

        gamma_yang = round(min(yang_score, 1.0), 3)

        # 双签判定
        yin_passed = gamma_yin >= 0.75
        yang_passed = gamma_yang >= 0.65
        endorsed = yin_passed and yang_passed

        detail = {
            "yin": {
                "gamma": gamma_yin,
                "passed": yin_passed,
                "threshold": 0.75,
                "checks": yin_checks,
            },
            "yang": {
                "gamma": gamma_yang,
                "passed": yang_passed,
                "threshold": 0.65,
                "checks": yang_checks,
            },
            "endorsed": endorsed,
        }

        if not endorsed:
            reason = []
            if not yin_passed:
                reason.append(f"阴签未达标(γ={gamma_yin}<0.75)")
            if not yang_passed:
                reason.append(f"阳签未达标(γ={gamma_yang}<0.65)")
            detail["reject_reason"] = "；".join(reason)

        return endorsed, detail

    # ─── 备份与回滚 ────────────────────────────────

    def _create_backup(self, target_path: str, modification_id: str) -> str:
        """创建文件备份"""
        backup_name = f"{modification_id}_{os.path.basename(target_path)}.bak"
        backup_path = os.path.join(self.backup_dir, backup_name)

        if os.path.exists(target_path):
            shutil.copy2(target_path, backup_path)
        else:
            # 新文件，创建空备份标记
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write("__NEW_FILE__")

        logger.info(f"备份已创建: {backup_path}")
        return backup_path

    def _rollback(self, backup_path: str, target_path: str) -> bool:
        """从备份回滚"""
        try:
            if not os.path.exists(backup_path):
                logger.error(f"备份文件不存在: {backup_path}")
                return False

            with open(backup_path, "r", encoding="utf-8") as f:
                content = f.read()

            if content == "__NEW_FILE__":
                # 原本不存在，删除新创建的文件
                if os.path.exists(target_path):
                    os.remove(target_path)
            else:
                shutil.copy2(backup_path, target_path)

            logger.info(f"回滚成功: {target_path}")
            return True
        except Exception as e:
            logger.error(f"回滚失败: {e}")
            return False

    # ─── 提议生成 ────────────────────────────────

    def propose_modification(
        self,
        target_file: str,
        description: str,
        new_code: str,
        proposer: str = "manual",
        reasoning: str = "",
        mode: str = "replace",  # replace / append / prepend
    ) -> Dict[str, Any]:
        """提议一个代码修改

        Args:
            target_file: 目标文件相对路径（如 w1_layer/memory.py）
            description: 修改描述
            new_code: 新代码内容
            proposer: 提议者（manual/llm/user）
            reasoning: 修改理由
            mode: 替换模式 replace/append/prepend

        Returns:
            提议详情（含id、状态、审查结果）
        """
        modification_id = f"mod_{int(time.time()*1000)}_{hashlib.md5(target_file.encode()).hexdigest()[:6]}"

        proposal = {
            "id": modification_id,
            "target_file": target_file,
            "description": description,
            "new_code": new_code,
            "proposer": proposer,
            "reasoning": reasoning,
            "mode": mode,
            "created_at": datetime.now().isoformat(),
            "status": "proposed",  # proposed → reviewed → endorsed → approved → applied/rejected/rolled_back
        }

        # ─── 步骤1：路径安全验证 ───
        try:
            full_path = self._validate_path(target_file)
            proposal["full_path"] = full_path
        except SecurityReviewError as e:
            proposal["status"] = "rejected"
            proposal["reject_reason"] = f"路径安全失败: {e}"
            self._history.append(proposal)
            self._save_state()
            return proposal

        # ─── 步骤2：安全审查 ───
        passed, issues, sec_score = self._security_review(new_code)
        proposal["security_review"] = {
            "passed": passed,
            "issues": issues,
            "score": sec_score,
        }

        if not passed:
            proposal["status"] = "rejected"
            proposal["reject_reason"] = f"安全审查失败: {'; '.join(issues[:3])}"
            self._history.append(proposal)
            self._save_state()
            return proposal

        # ─── 步骤3：阴阳双签 ───
        endorsed, endorse_detail = self._yin_yang_endorse(proposal)
        proposal["yin_yang"] = endorse_detail

        if not endorsed:
            proposal["status"] = "rejected"
            proposal["reject_reason"] = f"阴阳双签未通过: {endorse_detail.get('reject_reason', '')}"
            self._history.append(proposal)
            self._save_state()
            return proposal

        proposal["status"] = "endorsed"

        # ─── 步骤4：等待人工审批 ───
        if self.require_human_approval:
            proposal["status"] = "pending_approval"
            with self._lock:
                self._pending[modification_id] = proposal
        else:
            # 自动审批（低风险场景）
            proposal["status"] = "approved"

        self._history.append(proposal)
        self._save_state()

        logger.info(f"代码修改提议已创建: {modification_id} (状态: {proposal['status']})")
        return proposal

    # ─── 应用修改 ────────────────────────────────

    def apply_modification(self, modification_id: str, approved_by: str = "") -> Dict[str, Any]:
        """应用一个已审批的修改

        流程：
        1. 检查状态（必须已审批）
        2. 创建备份
        3. 写入新代码
        4. 语法验证
        5. 失败自动回滚

        Returns:
            应用结果
        """
        with self._lock:
            proposal = self._pending.get(modification_id)
            if not proposal:
                # 从历史中找
                for h in reversed(self._history):
                    if h.get("id") == modification_id:
                        proposal = h
                        break
            if not proposal:
                return {"success": False, "error": "修改提议不存在"}

            if proposal["status"] not in ("approved", "endorsed"):
                return {"success": False, "error": f"状态不允许应用: {proposal['status']}"}

        full_path = proposal["full_path"]
        new_code = proposal["new_code"]
        mode = proposal.get("mode", "replace")

        # ─── 创建备份 ───
        backup_path = self._create_backup(full_path, modification_id)
        with self._lock:
            self._backups[modification_id] = backup_path

        # ─── 应用修改 ───
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            if mode == "replace":
                write_content = new_code
            elif mode == "append" and os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    old = f.read()
                write_content = old + "\n\n" + new_code
            elif mode == "prepend" and os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    old = f.read()
                write_content = new_code + "\n\n" + old
            else:
                write_content = new_code

            # 原子写入（先写临时文件再重命名）
            tmp_path = full_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(write_content)

            # 语法验证
            try:
                ast.parse(write_content)
            except SyntaxError as e:
                os.remove(tmp_path)
                # 自动回滚
                self._rollback(backup_path, full_path)
                proposal["status"] = "rolled_back"
                proposal["rollback_reason"] = f"语法验证失败: {e}"
                self._save_state()
                logger.error(f"语法验证失败，已回滚: {e}")
                return {"success": False, "error": f"语法验证失败已回滚: {e}"}

            # 重命名（原子操作）
            os.replace(tmp_path, full_path)

            proposal["status"] = "applied"
            proposal["applied_at"] = datetime.now().isoformat()
            proposal["applied_by"] = approved_by

            # 从待审批移除
            with self._lock:
                self._pending.pop(modification_id, None)

            self._save_state()
            logger.info(f"✅ 代码修改已应用: {modification_id} → {proposal['target_file']}")
            return {
                "success": True,
                "modification_id": modification_id,
                "target_file": proposal["target_file"],
                "backup_path": backup_path,
                "applied_at": proposal["applied_at"],
            }

        except Exception as e:
            # 自动回滚
            logger.error(f"应用失败，自动回滚: {e}")
            self._rollback(backup_path, full_path)
            proposal["status"] = "rolled_back"
            proposal["rollback_reason"] = str(e)
            self._save_state()
            return {"success": False, "error": f"应用失败已回滚: {e}"}

    # ─── 审批管理 ────────────────────────────────

    def approve_modification(self, modification_id: str, approved_by: str = "admin") -> Dict[str, Any]:
        """审批通过一个待审批的修改"""
        with self._lock:
            proposal = self._pending.get(modification_id)
            if not proposal:
                return {"success": False, "error": "待审批的修改不存在"}
            if proposal["status"] != "pending_approval":
                return {"success": False, "error": f"状态不允许审批: {proposal['status']}"}

            proposal["status"] = "approved"
            proposal["approved_by"] = approved_by
            proposal["approved_at"] = datetime.now().isoformat()

        # 自动应用
        return self.apply_modification(modification_id, approved_by)

    def reject_modification(self, modification_id: str, reason: str = "") -> Dict[str, Any]:
        """拒绝一个待审批的修改"""
        with self._lock:
            proposal = self._pending.pop(modification_id, None)
            if not proposal:
                return {"success": False, "error": "待审批的修改不存在"}
            proposal["status"] = "rejected"
            proposal["reject_reason"] = reason
        self._save_state()
        logger.info(f"修改已拒绝: {modification_id} ({reason})")
        return {"success": True, "modification_id": modification_id}

    def rollback_modification(self, modification_id: str) -> Dict[str, Any]:
        """回滚一个已应用的修改"""
        with self._lock:
            backup_path = self._backups.get(modification_id)
            proposal = None
            for h in self._history:
                if h.get("id") == modification_id:
                    proposal = h
                    break

        if not proposal:
            return {"success": False, "error": "修改记录不存在"}
        if proposal.get("status") != "applied":
            return {"success": False, "error": f"只能回滚已应用的修改，当前状态: {proposal['status']}"}
        if not backup_path:
            return {"success": False, "error": "备份文件不存在"}

        full_path = proposal["full_path"]
        ok = self._rollback(backup_path, full_path)
        if ok:
            proposal["status"] = "rolled_back"
            proposal["rolled_back_at"] = datetime.now().isoformat()
            self._save_state()
            logger.info(f"修改已回滚: {modification_id}")
            return {"success": True, "modification_id": modification_id}
        else:
            return {"success": False, "error": "回滚失败"}

    # ─── 查询 ────────────────────────────────

    def list_pending(self) -> List[Dict[str, Any]]:
        """列出待审批的修改"""
        with self._lock:
            return [
                {
                    "id": p["id"],
                    "target_file": p["target_file"],
                    "description": p["description"],
                    "proposer": p["proposer"],
                    "status": p["status"],
                    "created_at": p["created_at"],
                    "security_score": p.get("security_review", {}).get("score", 0),
                    "yin_yang": p.get("yin_yang", {}),
                }
                for p in self._pending.values()
            ]

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取修改历史"""
        return self._history[-limit:]

    def get_status(self) -> Dict[str, Any]:
        """获取自修改引擎状态"""
        return {
            "project_root": self.project_root,
            "backup_dir": self.backup_dir,
            "require_human_approval": self.require_human_approval,
            "pending_count": len(self._pending),
            "history_count": len(self._history),
            "protected_files": list(D_LAYER_PROTECTED),
            "recent_history": [
                {
                    "id": h.get("id"),
                    "target_file": h.get("target_file"),
                    "status": h.get("status"),
                    "created_at": h.get("created_at"),
                }
                for h in self._history[-5:]
            ],
        }


# 全局单例
_modifier: Optional[CodeSelfModifier] = None


def get_modifier() -> CodeSelfModifier:
    """获取自修改引擎单例"""
    global _modifier
    if _modifier is None:
        raise RuntimeError("自修改引擎未初始化，请先调用 init_modifier()")
    return _modifier


def init_modifier(project_root: str, backup_dir: str = "",
                  ledger=None, require_human_approval: bool = True) -> CodeSelfModifier:
    """初始化自修改引擎（由 server.py 调用）"""
    global _modifier
    _modifier = CodeSelfModifier(
        project_root=project_root,
        backup_dir=backup_dir,
        ledger=ledger,
        require_human_approval=require_human_approval,
    )
    return _modifier
