# -*- coding: utf-8 -*-
"""
m_layer/parallel_executor.py — 并行步骤执行器（M层）
====================================================
v5.0第二批：无依赖的步骤并行执行，提升效率

能力对标：AI助手的并行工具调用能力

功能:
  1. 分析步骤依赖关系，构建DAG
  2. 无依赖的步骤并行执行
  3. 有依赖的步骤按序执行
  4. 线程池管理（默认4线程）

架构归属：M层（执行层增强）
依赖：concurrent.futures, task_planner
"""
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional, Set
from datetime import datetime

logger = logging.getLogger("scu2.m.parallel")


class ParallelExecutor:
    """并行步骤执行器

    用法:
        executor = ParallelExecutor()
        result = executor.execute_parallel(plan, task_id="task_001")
    """

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self._action = None

    def _get_action(self):
        if self._action is None:
            from w1_layer.action import ActionLayer
            self._action = ActionLayer()
        return self._action

    def execute_parallel(self, plan: Dict[str, Any],
                         task_id: str = "") -> Dict[str, Any]:
        """并行执行计划

        Args:
            plan: 执行计划（来自TaskPlanner）
            task_id: 任务ID

        Returns:
            执行报告
        """
        task_id = task_id or f"parallel_{int(time.time()*1000)}"
        steps = plan.get("steps", [])
        start_time = time.time()

        if not steps:
            return {"task_id": task_id, "success": False, "errors": ["计划为空"]}

        # 执行
        results = {}
        step_reports = {}
        completed: Set[int] = set()
        failed: Set[int] = set()

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            pending_futures = {}

            while len(completed) + len(failed) < len(steps):
                # 找出可执行的步骤（依赖已满足）
                ready = []
                for step in steps:
                    sid = step.get("step_id", 0)
                    if sid in completed or sid in failed:
                        continue
                    deps = step.get("depends_on", [])
                    if all(d in completed for d in deps):
                        ready.append(step)

                # 提交并行执行
                for step in ready:
                    sid = step.get("step_id", 0)
                    # 注入上下文
                    params = self._inject_context(dict(step.get("params", {})), results, sid)
                    future = pool.submit(self._execute_step, step, params)
                    pending_futures[future] = sid

                if not pending_futures:
                    # 没有可执行的，可能所有剩余步骤依赖失败
                    for step in steps:
                        sid = step.get("step_id", 0)
                        if sid not in completed and sid not in failed:
                            step_reports[sid] = {
                                "step_id": sid,
                                "action": step.get("action", ""),
                                "status": "skipped",
                                "error": "依赖步骤未完成",
                            }
                            failed.add(sid)
                    break

                # 等待至少一个完成
                for future in as_completed(pending_futures):
                    sid = pending_futures[future]
                    try:
                        step_report = future.result()
                        step_reports[sid] = step_report
                        if step_report["status"] == "done":
                            completed.add(sid)
                            results[f"step{sid}_result"] = step_report.get("result", {})
                        else:
                            failed.add(sid)
                    except Exception as e:
                        step_reports[sid] = {
                            "step_id": sid,
                            "status": "failed",
                            "error": str(e),
                        }
                        failed.add(sid)

                    # 移除已处理的future
                    del pending_futures[future]
                    break  # 重新评估ready

        elapsed = (time.time() - start_time) * 1000
        total = len(steps)
        done_count = len(completed)

        return {
            "task_id": task_id,
            "goal": plan.get("goal", ""),
            "steps": [step_reports.get(s.get("step_id", 0), {}) for s in steps],
            "success": len(failed) == 0 and done_count > 0,
            "steps_total": total,
            "steps_done": done_count,
            "steps_failed": len(failed),
            "elapsed_ms": round(elapsed, 2),
            "parallel_executed": True,
            "max_workers": self.max_workers,
        }

    def _execute_step(self, step: Dict, params: Dict) -> Dict:
        """执行单个步骤"""
        action = self._get_action()
        tool_info = {
            "tool": step.get("action", ""),
            "params": params,
            "tool_type": action.TOOL_TYPES.get(step.get("action", ""), "read"),
        }
        step_start = time.time()
        report = {
            "step_id": step.get("step_id", 0),
            "action": step.get("action", ""),
            "description": step.get("description", ""),
            "status": "running",
            "started_at": datetime.now().isoformat(),
        }

        try:
            result = action.execute(tool_info)
            report["elapsed_ms"] = round((time.time() - step_start) * 1000, 2)
            if result.get("success"):
                report["status"] = "done"
                report["result"] = result.get("result", {})
            else:
                report["status"] = "failed"
                report["error"] = result.get("error", "")
        except Exception as e:
            report["status"] = "failed"
            report["error"] = str(e)

        return report

    def _build_dependency_graph(self, steps: List[Dict]) -> Dict[int, List[int]]:
        """构建依赖图"""
        graph = {}
        for step in steps:
            sid = step.get("step_id", 0)
            deps = step.get("depends_on", [])
            graph[sid] = deps
        return graph

    def _inject_context(self, params: Dict, context: Dict, current_step: int) -> Dict:
        """注入上下文（与task_executor相同逻辑）"""
        import json
        import re
        params_str = json.dumps(params) if isinstance(params, dict) else str(params)

        def replace_placeholder(match):
            ref = match.group(1)
            parts = ref.split(".", 1)
            ctx_key = parts[0]
            if ctx_key in context:
                value = context[ctx_key]
                if len(parts) > 1 and isinstance(value, dict):
                    return str(value.get(parts[1], ""))
                return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            return match.group(0)

        params_str = re.sub(r'\$\{(step\d+_result(?:\.\w+)?)\}', replace_placeholder, params_str)
        try:
            return json.loads(params_str)
        except (json.JSONDecodeError, TypeError):
            return params


# ─── 单例 ────────────────────────────────────
_parallel_instance: Optional[ParallelExecutor] = None


def get_parallel_executor() -> ParallelExecutor:
    """获取并行执行器单例"""
    global _parallel_instance
    if _parallel_instance is None:
        _parallel_instance = ParallelExecutor()
    return _parallel_instance
