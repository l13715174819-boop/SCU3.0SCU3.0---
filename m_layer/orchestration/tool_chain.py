# -*- coding: utf-8 -*-
"""
m_layer/tool_chain.py — 多工具链式调用（M层）
==============================================
阶段4第二批：支持多工具按序链式执行，前一个的输出作为后一个的输入

能力对标：AI助手"工具A结果→工具B处理→工具C输出"的编排能力

功能:
  1. 定义工具链（有序工具列表）
  2. 链式执行：上一步输出自动传入下一步
  3. 支持管道转换器（提取/转换字段）
  4. 链中某步失败可配置停止或继续

用法:
    chain = ToolChain()
    chain.add("file_read", {"path": "data.txt"}, extract="content")
    chain.add("text_stats", {})  # 上一步的content自动传入
    chain.add("file_write", {"path": "stats.txt"})
    result = chain.execute()

架构归属：M层（认知层工具编排）
依赖：w1_layer/action
"""
import logging
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime

logger = logging.getLogger("SCU3.m.toolchain")


class ToolChain:
    """多工具链式调用

    用法:
        chain = ToolChain()
        chain.add("file_read", {"path": "readme.md"}, extract_field="content")
        chain.add("text_stats", input_field="content")
        result = chain.execute()
    """

    def __init__(self):
        self._action = None
        self._steps: List[Dict[str, Any]] = []

    def _get_action(self):
        if self._action is None:
            from w1_layer.action import ActionLayer
            self._action = ActionLayer()
        return self._action

    def add(self, tool: str, params: Dict[str, Any],
            extract_field: Optional[str] = None,
            input_field: Optional[str] = None,
            transform: Optional[Callable] = None,
            on_fail: str = "stop") -> "ToolChain":
        """添加链式步骤

        Args:
            tool: 工具名
            params: 工具参数
            extract_field: 从上一步结果中提取的字段（作为本步输入）
            input_field: 把提取的值注入到params的哪个字段
            transform: 可选的数据转换函数
            on_fail: 失败策略 stop|continue|skip

        Returns:
            self（支持链式调用）
        """
        self._steps.append({
            "tool": tool,
            "params": params,
            "extract_field": extract_field,
            "input_field": input_field,
            "transform": transform,
            "on_fail": on_fail,
        })
        return self

    def execute(self) -> Dict[str, Any]:
        """执行工具链

        Returns:
            {
                "success": bool,
                "steps": [每步结果],
                "final_result": 最后一步结果,
                "chain_length": int,
                "elapsed_ms": float,
            }
        """
        from time import time
        start = time()
        results = []
        prev_output = None
        chain_broken = False

        for i, step in enumerate(self._steps):
            step_result = {
                "step": i + 1,
                "tool": step["tool"],
                "status": "pending",
            }

            # 从上一步提取数据
            params = dict(step["params"])
            if prev_output is not None and step["extract_field"]:
                if isinstance(prev_output, dict):
                    extracted = prev_output.get(step["extract_field"], prev_output)
                else:
                    extracted = prev_output

                # 转换
                if step["transform"]:
                    try:
                        extracted = step["transform"](extracted)
                    except Exception as e:
                        step_result["status"] = "failed"
                        step_result["error"] = f"转换失败: {e}"
                        results.append(step_result)
                        if step["on_fail"] == "stop":
                            chain_broken = True
                            break
                        continue

                # 注入到参数
                if step["input_field"]:
                    params[step["input_field"]] = extracted
                else:
                    # 默认注入到第一个参数
                    if not params:
                        params = extracted if isinstance(extracted, dict) else {"input": extracted}

            # 执行工具
            try:
                tool_info = {
                    "tool": step["tool"],
                    "params": params,
                    "tool_type": self._get_action().TOOL_TYPES.get(step["tool"], "read"),
                }
                exec_result = self._get_action().execute(tool_info)

                if exec_result.get("success"):
                    step_result["status"] = "done"
                    step_result["result"] = exec_result.get("result", {})
                    prev_output = step_result["result"]
                else:
                    step_result["status"] = "failed"
                    step_result["error"] = exec_result.get("error", "")
                    if step["on_fail"] == "stop":
                        chain_broken = True
                        results.append(step_result)
                        break
                    elif step["on_fail"] == "skip":
                        step_result["status"] = "skipped"
                        # 保留上一步输出
                    # continue: 继续但用上一步输出

            except Exception as e:
                step_result["status"] = "failed"
                step_result["error"] = str(e)
                if step["on_fail"] == "stop":
                    chain_broken = True
                    results.append(step_result)
                    break

            results.append(step_result)

        elapsed = (time() - start) * 1000
        return {
            "success": not chain_broken and all(r["status"] == "done" for r in results),
            "steps": results,
            "final_result": results[-1].get("result") if results else None,
            "chain_length": len(self._steps),
            "executed_length": len(results),
            "chain_broken": chain_broken,
            "elapsed_ms": round(elapsed, 2),
        }

    def clear(self) -> "ToolChain":
        """清空链"""
        self._steps.clear()
        return self

    def describe(self) -> List[str]:
        """描述工具链（用于展示）"""
        return [
            f"{i+1}. {s['tool']}({s['params']})"
            + (f" ← extract:{s['extract_field']}" if s["extract_field"] else "")
            + (f" → input:{s['input_field']}" if s["input_field"] else "")
            for i, s in enumerate(self._steps)
        ]


def quick_chain(tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    """快速执行工具链

    Args:
        tools: [{tool, params, extract_field?, input_field?}, ...]

    Returns:
        执行结果
    """
    chain = ToolChain()
    for t in tools:
        chain.add(
            tool=t["tool"],
            params=t.get("params", {}),
            extract_field=t.get("extract_field"),
            input_field=t.get("input_field"),
            on_fail=t.get("on_fail", "stop"),
        )
    return chain.execute()
