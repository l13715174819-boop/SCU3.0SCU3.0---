# -*- coding: utf-8 -*-
"""
m_layer/retry_strategy.py — 失败重试与策略切换（M层）
======================================================
阶段4第二批：执行失败时自动重试或切换策略

能力对标：AI助手"第一种方法失败→换思路→重试"的错误恢复能力

功能:
  1. 指数退避重试（默认3次，间隔递增）
  2. 策略切换（同一目标多种实现方式，逐个尝试）
  3. 回退策略（全部失败后返回降级结果）

用法:
    retry = RetryStrategy(max_retries=3, backoff="exponential")

    # 简单重试
    result = retry.retry(func, arg1, arg2)

    # 策略切换
    result = retry.try_strategies([
        {"name": "策略A", "func": func_a, "args": (1, 2)},
        {"name": "策略B", "func": func_b, "args": (1, 2)},
    ])

架构归属：M层（认知层错误恢复）
"""
import time
import logging
from typing import Dict, Any, List, Optional, Callable, Tuple
from datetime import datetime

logger = logging.getLogger("SCU3.m.retry")


class RetryStrategy:
    """失败重试与策略切换

    用法:
        retry = RetryStrategy(max_retries=3)
        # 简单重试
        result = retry.retry(risky_func, "arg1", kwarg="val")
        # 策略切换
        result = retry.try_strategies([
            {"name": "直接计算", "func": calc_direct, "args": (expr,)},
            {"name": "分步计算", "func": calc_stepwise, "args": (expr,)},
        ])
    """

    def __init__(self, max_retries: int = 3, backoff: str = "exponential",
                 base_delay: float = 0.1, max_delay: float = 2.0):
        """
        Args:
            max_retries: 最大重试次数
            backoff: 退避策略 exponential|linear|fixed
            base_delay: 基础延迟（秒）
            max_delay: 最大延迟（秒）
        """
        self.max_retries = max_retries
        self.backoff = backoff
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._history: List[Dict] = []

    def retry(self, func: Callable, *args, **kwargs) -> Dict[str, Any]:
        """简单重试：同一函数多次尝试

        Args:
            func: 可调用对象
            *args, **kwargs: 函数参数

        Returns:
            {
                "success": bool,
                "result": Any,
                "error": str|None,
                "attempts": int,
                "total_delay": float,
            }
        """
        last_error = None
        total_delay = 0.0

        for attempt in range(1, self.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                return {
                    "success": True,
                    "result": result,
                    "error": None,
                    "attempts": attempt,
                    "total_delay": total_delay,
                }
            except Exception as e:
                last_error = str(e)
                logger.info(f"重试 {attempt}/{self.max_retries} 失败: {e}")

                if attempt < self.max_retries:
                    delay = self._calc_delay(attempt)
                    total_delay += delay
                    time.sleep(delay)

        return {
            "success": False,
            "result": None,
            "error": last_error,
            "attempts": self.max_retries,
            "total_delay": total_delay,
        }

    def try_strategies(self, strategies: List[Dict[str, Any]]) -> Dict[str, Any]:
        """策略切换：逐个尝试不同实现方式

        Args:
            strategies: [{name, func, args?, kwargs?, retry?}, ...]

        Returns:
            {
                "success": bool,
                "result": Any,
                "winning_strategy": str,
                "attempts": [{strategy, attempt, success, error}],
                "total_attempts": int,
            }
        """
        all_attempts = []
        total_attempts = 0

        for strat in strategies:
            name = strat.get("name", "未命名策略")
            func = strat["func"]
            args = strat.get("args", ())
            kwargs = strat.get("kwargs", {})
            retry_count = strat.get("retry", 1)

            logger.info(f"尝试策略: {name} (重试{retry_count}次)")

            for attempt in range(1, retry_count + 1):
                total_attempts += 1
                try:
                    result = func(*args, **kwargs)
                    all_attempts.append({
                        "strategy": name,
                        "attempt": attempt,
                        "success": True,
                        "error": None,
                    })
                    return {
                        "success": True,
                        "result": result,
                        "winning_strategy": name,
                        "attempts": all_attempts,
                        "total_attempts": total_attempts,
                    }
                except Exception as e:
                    err = str(e)
                    all_attempts.append({
                        "strategy": name,
                        "attempt": attempt,
                        "success": False,
                        "error": err,
                    })
                    logger.info(f"策略'{name}'第{attempt}次失败: {err}")
                    if attempt < retry_count:
                        delay = self._calc_delay(attempt)
                        time.sleep(delay)

        # 所有策略都失败
        self._history.append({
            "time": datetime.now().isoformat(),
            "strategies_tried": len(strategies),
            "total_attempts": total_attempts,
            "success": False,
        })

        return {
            "success": False,
            "result": None,
            "winning_strategy": None,
            "attempts": all_attempts,
            "total_attempts": total_attempts,
            "errors": [a["error"] for a in all_attempts if a["error"]],
        }

    def with_fallback(self, primary: Callable, fallback: Callable,
                      *args, **kwargs) -> Dict[str, Any]:
        """主策略+回退策略

        Args:
            primary: 主策略函数
            fallback: 回退策略函数
            *args, **kwargs: 传给两个函数的参数

        Returns:
            执行结果 + used_fallback标记
        """
        result = self.retry(primary, *args, **kwargs)
        if result["success"]:
            result["used_fallback"] = False
            return result

        # 主策略失败，尝试回退
        logger.info("主策略失败，切换到回退策略")
        fb_result = self.retry(fallback, *args, **kwargs)
        fb_result["used_fallback"] = fb_result["success"]
        return fb_result

    def _calc_delay(self, attempt: int) -> float:
        """计算退避延迟"""
        if self.backoff == "exponential":
            delay = self.base_delay * (2 ** (attempt - 1))
        elif self.backoff == "linear":
            delay = self.base_delay * attempt
        else:  # fixed
            delay = self.base_delay
        return min(delay, self.max_delay)

    def get_history(self, limit: int = 20) -> List[Dict]:
        """获取重试历史"""
        return list(reversed(self._history[-limit:]))


# ─── 便捷函数 ────────────────────────────────────

def retry_on_fail(func: Callable, *args, max_retries: int = 3, **kwargs) -> Any:
    """简单重试便捷函数

    Returns:
        函数结果（成功）或抛出最后一次异常（失败）
    """
    retry = RetryStrategy(max_retries=max_retries)
    result = retry.retry(func, *args, **kwargs)
    if result["success"]:
        return result["result"]
    raise Exception(result["error"])
