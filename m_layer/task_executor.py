# -*- coding: utf-8 -*-
"""
m_layer/task_executor.py — 多步任务执行器（M层）
==================================================
阶段4第一批：按计划循环执行步骤，维护状态，传递上下文

能力对标：AI助手"按计划逐步执行→遇到错误处理→完成汇总"环节

核心循环:
  1. 从计划中取出下一个待执行步骤
  2. 检查依赖步骤是否已完成
  3. 调用对应工具执行
  4. 记录结果到上下文（供后续步骤使用）
  5. 临时资源注册到temp_manager
  6. 失败时重试或跳过
  7. 全部完成后→反思→清理临时资源

特性:
  - 完全自主执行（无需人工干预）
  - 步骤间上下文传递（上一步输出→下一步输入）
  - 临时文件自动清理
  - 执行后自动反思

架构归属：M层（认知层 orchestrator）
依赖：task_planner, action(ActionLayer), temp_manager, reflection
"""
import os
import json
import time
import uuid
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("scu2.m.executor")


class TaskExecutor:
    """多步任务执行器

    用法:
        executor = TaskExecutor()
        # 完整流程：拆解→执行→反思→清理
        result = executor.run("分析readme.md的词频并生成报告")
        # 或分步控制
        plan = executor.create_plan("目标")
        result = executor.execute_plan(plan)
    """

    def __init__(self):
        self._planner = None
        self._action = None
        self._temp_mgr = None
        self._reflection = None
        # 执行历史
        self._history: List[Dict] = []

    def _get_planner(self):
        if self._planner is None:
            from m_layer.task_planner import get_planner
            self._planner = get_planner()
        return self._planner

    def _get_action(self):
        if self._action is None:
            from w1_layer.action import ActionLayer
            self._action = ActionLayer()
        return self._action

    def _get_temp_mgr(self):
        if self._temp_mgr is None:
            from w1_layer.temp_manager import get_temp_manager
            self._temp_mgr = get_temp_manager()
        return self._temp_mgr

    def _get_reflection(self):
        if self._reflection is None:
            from m_layer.reflection import get_reflection_engine
            self._reflection = get_reflection_engine()
        return self._reflection

    def run(self, goal: str, context: Optional[Dict] = None,
            cleanup: bool = True, reflect: bool = True) -> Dict[str, Any]:
        """完整执行流程：拆解→执行→反思→清理

        Args:
            goal: 用户自然语言目标
            context: 额外上下文
            cleanup: 是否自动清理临时资源
            reflect: 是否执行后反思

        Returns:
            完整执行报告
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        start_time = time.time()

        logger.info(f" Agent任务启动: {task_id} | 目标: {goal[:80]}")

        # ① 拆解任务
        plan = self._get_planner().plan(goal, context)

        # 澄清请求：无法拆解
        if plan.get("source") == "clarify":
            return {
                "task_id": task_id,
                "goal": goal,
                "success": False,
                "plan": plan,
                "steps": [],
                "clarify": plan.get("clarify", "无法拆解目标"),
                "elapsed_ms": round((time.time() - start_time) * 1000, 2),
            }

        # ② 执行计划
        report = self.execute_plan(plan, task_id=task_id)
        report["goal"] = goal
        report["task_id"] = task_id
        report["plan_source"] = plan.get("source", "unknown")

        # ③ 反思
        if reflect:
            try:
                reflection = self._get_reflection().reflect(report)
                report["reflection"] = reflection
            except Exception as e:
                logger.warning(f"反思失败: {e}")
                report["reflection"] = {"error": str(e)}

        # ④ 清理临时资源
        if cleanup:
            try:
                cleanup_report = self._get_temp_mgr().cleanup(task_id)
                report["cleanup"] = cleanup_report
            except Exception as e:
                logger.warning(f"清理失败: {e}")
                report["cleanup"] = {"error": str(e)}

        report["elapsed_ms"] = round((time.time() - start_time) * 1000, 2)

        # 记录历史
        self._history.append({
            "task_id": task_id,
            "goal": goal,
            "success": report.get("success", False),
            "steps_total": len(report.get("steps", [])),
            "steps_done": sum(1 for s in report.get("steps", []) if s.get("status") == "done"),
            "elapsed_ms": report["elapsed_ms"],
            "time": datetime.now().isoformat(),
        })
        if len(self._history) > 50:
            self._history.pop(0)

        logger.info(f" Agent任务完成: {task_id} | 成功={report.get('success')} | "
                    f"耗时={report['elapsed_ms']}ms")
        return report

    def create_plan(self, goal: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """仅创建计划（不执行）"""
        return self._get_planner().plan(goal, context)

    def execute_plan(self, plan: Dict[str, Any],
                     task_id: Optional[str] = None) -> Dict[str, Any]:
        """执行已有计划

        Args:
            plan: 来自task_planner的计划
            task_id: 任务ID（不传则自动生成）

        Returns:
            执行报告
        """
        task_id = task_id or f"task_{uuid.uuid4().hex[:8]}"
        steps = plan.get("steps", [])
        start_time = time.time()

        report = {
            "task_id": task_id,
            "goal": plan.get("goal", ""),
            "steps": [],
            "success": True,
            "errors": [],
            "step_context": {},  # 步骤间共享的上下文
        }

        if not steps:
            report["success"] = False
            report["errors"].append("计划为空，无步骤可执行")
            report["elapsed_ms"] = round((time.time() - start_time) * 1000, 2)
            return report

        action = self._get_action()
        temp_mgr = self._get_temp_mgr()

        # 逐步执行
        for step in steps:
            step_id = step.get("step_id", 0)
            step_action = step.get("action", "")
            step_params = dict(step.get("params", {}))
            depends_on = step.get("depends_on", [])
            is_temporary = step.get("is_temporary", False)

            step_report = {
                "step_id": step_id,
                "action": step_action,
                "description": step.get("description", ""),
                "status": "pending",
                "started_at": datetime.now().isoformat(),
            }

            # 检查依赖
            dep_ok = True
            for dep_id in depends_on:
                dep_step = next((s for s in report["steps"] if s["step_id"] == dep_id), None)
                if not dep_step or dep_step.get("status") != "done":
                    step_report["status"] = "skipped"
                    step_report["error"] = f"依赖步骤{dep_id}未完成"
                    report["steps"].append(step_report)
                    dep_ok = False
                    break

            if not dep_ok:
                continue

            # 上下文注入：把前一步的结果注入到当前步骤的参数中
            step_params = self._inject_context(step_params, report["step_context"], step_id)

            # 执行工具
            step_report["status"] = "running"
            step_start = time.time()

            try:
                tool_info = {
                    "tool": step_action,
                    "params": step_params,
                    "tool_type": action.TOOL_TYPES.get(step_action, "read"),
                }
                result = action.execute(tool_info)

                step_report["elapsed_ms"] = round((time.time() - step_start) * 1000, 2)
                step_report["completed_at"] = datetime.now().isoformat()

                if result.get("success"):
                    step_report["status"] = "done"
                    step_report["result"] = result.get("result", {})

                    # 保存到上下文（供后续步骤使用）
                    report["step_context"][f"step{step_id}_result"] = step_report["result"]

                    # 临时资源注册
                    if is_temporary:
                        self._register_temp_resources(task_id, step_action, step_report["result"], temp_mgr)

                    # 如果是file_write，注册产生的文件
                    if step_action == "file_write":
                        written_path = step_report["result"].get("abs_path", "")
                        if written_path and is_temporary:
                            temp_mgr.register(task_id, written_path, is_dir=False)

                else:
                    step_report["status"] = "failed"
                    step_report["error"] = result.get("error", "执行失败")
                    report["errors"].append(f"步骤{step_id}失败: {step_report['error']}")
                    # 完全自主模式：记录失败但继续执行后续步骤
                    logger.warning(f"步骤{step_id}({step_action})失败: {step_report['error']}")

            except Exception as e:
                step_report["status"] = "failed"
                step_report["error"] = str(e)
                step_report["elapsed_ms"] = round((time.time() - step_start) * 1000, 2)
                step_report["completed_at"] = datetime.now().isoformat()
                report["errors"].append(f"步骤{step_id}异常: {e}")
                logger.error(f"步骤{step_id}异常: {e}", exc_info=True)

            report["steps"].append(step_report)

        # 汇总
        total = len(report["steps"])
        done = sum(1 for s in report["steps"] if s["status"] == "done")
        failed = sum(1 for s in report["steps"] if s["status"] == "failed")
        skipped = sum(1 for s in report["steps"] if s["status"] == "skipped")

        report["success"] = failed == 0 and done > 0
        report["steps_total"] = total
        report["steps_done"] = done
        report["steps_failed"] = failed
        report["steps_skipped"] = skipped
        report["elapsed_ms"] = round((time.time() - start_time) * 1000, 2)

        return report

    def _inject_context(self, params: Dict, context: Dict, current_step_id: int) -> Dict:
        """把前序步骤的结果注入到当前步骤参数中

        支持的占位符:
          ${step1_result} → 替换为步骤1的结果
          ${step1_result.content} → 替换为步骤1结果的content字段
        """
        import re
        params_str = json.dumps(params) if isinstance(params, dict) else str(params)

        # 替换 ${stepN_result} 或 ${stepN_result.field}
        def replace_placeholder(match):
            ref = match.group(1)  # step1_result 或 step1_result.content
            parts = ref.split(".", 1)
            ctx_key = parts[0]
            if ctx_key in context:
                value = context[ctx_key]
                if len(parts) > 1:
                    # 取字段
                    field = parts[1]
                    if isinstance(value, dict):
                        return str(value.get(field, ""))
                return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            return match.group(0)

        params_str = re.sub(r'\$\{(step\d+_result(?:\.\w+)?)\}', replace_placeholder, params_str)

        try:
            return json.loads(params_str)
        except (json.JSONDecodeError, TypeError):
            return params

    def _register_temp_resources(self, task_id: str, action: str,
                                  result: Dict, temp_mgr) -> None:
        """从执行结果中识别临时资源并注册"""
        try:
            # file_write 产生的文件
            if action == "file_write" and isinstance(result, dict):
                path = result.get("abs_path", "")
                if path:
                    temp_mgr.register(task_id, path, is_dir=False)

            # code_run 产生的输出文件（如果代码写了文件）
            if action == "code_run" and isinstance(result, dict):
                output = result.get("output", "")
                # 检查输出中是否有文件路径线索
                import re
                paths = re.findall(r'(/[\w/.-]+\.\w+|sandbox/[\w/.-]+)', output)
                for p in paths[:5]:  # 最多注册5个
                    temp_mgr.register(task_id, p, is_dir=False)
        except Exception as e:
            logger.warning(f"注册临时资源失败: {e}")

    def get_history(self, limit: int = 20) -> List[Dict]:
        """获取执行历史"""
        return list(reversed(self._history[-limit:]))

    def get_status(self) -> Dict[str, Any]:
        """获取执行器状态"""
        return {
            "history_count": len(self._history),
            "total_tasks": len(self._history),
            "successful_tasks": sum(1 for h in self._history if h.get("success")),
            "failed_tasks": sum(1 for h in self._history if not h.get("success")),
            "avg_elapsed_ms": (
                sum(h.get("elapsed_ms", 0) for h in self._history) / len(self._history)
                if self._history else 0
            ),
        }


# ─── 单例 ────────────────────────────────────
_executor_instance: Optional[TaskExecutor] = None


def get_executor() -> TaskExecutor:
    """获取任务执行器单例"""
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = TaskExecutor()
    return _executor_instance
