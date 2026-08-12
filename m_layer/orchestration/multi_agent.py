# -*- coding: utf-8 -*-
"""
m_layer/multi_agent.py — 多Agent协作（M层）双模式版
=====================================================
v5.1升级：线程级 / 进程级 双模式并存，按需使用

能力对标：AI助手的Task子代理并行能力（Trae Agent 式）

两种模式:
  ① mode="thread"  —— 线程级并行（默认，轻量）
     · 同进程共享内存，共享 LLM 客户端和知识库
     · 适合工具调用密集型、需要共享状态的细粒度任务
     · 开销小，启动快（<1ms）
     · 并发数默认 4

  ② mode="process" —— 进程级隔离（独立上下文）
     · 每个子代理在独立子进程运行，拥有独立 LLM 会话
     · 上下文完全隔离，互不干扰
     · 适合深度探索型、长链路推理任务
     · 开销较大（进程启动 ~100ms），但隔离性强
     · 并发数默认 2（避免子进程过多占用内存）

用法:
    coord = MultiAgentCoordinator(mode="thread")   # 线程模式
    coord = MultiAgentCoordinator(mode="process")  # 进程模式

    coord.assign_subtask("搜索Python教程", specialty="search")
    coord.assign_subtask("分析代码质量", specialty="analysis")
    coord.assign_subtask("生成报告", specialty="writing",
                         depends_on=["搜索Python教程", "分析代码质量"])
    result = coord.execute_all()

架构归属：M层（Agent编排层）
依赖：task_executor（子Agent执行）
"""
import os
import sys
import time
import uuid
import json
import pickle
import logging
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from typing import Dict, Any, List, Optional, Union
from datetime import datetime

logger = logging.getLogger("SCU3.m.multi_agent")

# 确保项目根目录在 sys.path 中（进程模式子进程需要）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)


# ═══════════════════════════════════════════════════════════
#  子代理基类与实现
# ═══════════════════════════════════════════════════════════

class SubAgent:
    """线程级子Agent（同进程共享内存）"""

    def __init__(self, agent_id: str, specialty: str = "general"):
        self.agent_id = agent_id
        self.specialty = specialty
        self._executor = None

    def _get_executor(self):
        if self._executor is None:
            from m_layer.task_executor import get_executor
            self._executor = get_executor()
        return self._executor

    def execute(self, subtask: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """执行子任务"""
        logger.info(f"[线程] 子Agent {self.agent_id}({self.specialty}) 开始: {subtask[:50]}")
        result = self._get_executor().run(subtask, context=context or {})
        result["agent_id"] = self.agent_id
        result["specialty"] = self.specialty
        result["isolation"] = "thread"
        return result


class ProcessSubAgent:
    """进程级隔离子Agent（独立上下文窗口）

    每个子代理在独立子进程中运行，拥有:
    - 独立的 LLM 客户端实例
    - 独立的对话上下文
    - 独立的临时资源空间
    适合深度探索型任务，与主进程完全隔离。
    """

    def __init__(self, agent_id: str, specialty: str = "general"):
        self.agent_id = agent_id
        self.specialty = specialty

    def execute(self, subtask: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """在独立子进程中执行任务（真正进程隔离）

        通过 multiprocessing.Process 启动子进程，使用 Queue 回传结果。
        子进程拥有独立的 LLM 客户端、独立内存空间、独立上下文窗口。
        """
        logger.info(f"[进程] 子Agent {self.agent_id}({self.specialty}) 启动子进程: {subtask[:50]}")

        # 序列化输入参数（子进程通过 pickle 接收）
        payload = {
            "agent_id": self.agent_id,
            "specialty": self.specialty,
            "subtask": subtask,
            "context": context or {},
            "base_dir": _BASE_DIR,
        }

        # 使用 Queue 接收子进程结果
        result_queue: multiprocessing.Queue = multiprocessing.Queue()

        # 构建子进程（target 为模块级函数，可被 pickle）
        proc = multiprocessing.Process(
            target=_process_entry,
            args=(payload, result_queue),
            name=f"SubAgent-{self.agent_id}",
            daemon=False,
        )

        try:
            start_ts = time.time()
            proc.start()
            logger.info(f"[进程] 子进程已启动 PID={proc.pid} (agent={self.agent_id})")

            # 等待子进程完成（超时 300 秒，避免长时间挂起）
            proc.join(timeout=300)

            if proc.is_alive():
                # 超时未完成，强制终止
                logger.error(f"[进程] 子Agent {self.agent_id} 超时，强制终止")
                proc.terminate()
                proc.join(timeout=5)
                if proc.is_alive():
                    proc.kill()
                return {
                    "agent_id": self.agent_id,
                    "specialty": self.specialty,
                    "isolation": "process",
                    "success": False,
                    "error": "子进程执行超时（300秒），已强制终止",
                    "goal": subtask,
                    "elapsed_ms": round((time.time() - start_ts) * 1000, 2),
                }

            # 从队列获取结果
            if result_queue.empty():
                # 子进程退出但未返回结果（可能崩溃）
                exitcode = proc.exitcode
                logger.error(f"[进程] 子Agent {self.agent_id} 异常退出 exitcode={exitcode}")
                return {
                    "agent_id": self.agent_id,
                    "specialty": self.specialty,
                    "isolation": "process",
                    "success": False,
                    "error": f"子进程异常退出（exitcode={exitcode}）",
                    "goal": subtask,
                    "elapsed_ms": round((time.time() - start_ts) * 1000, 2),
                }

            result = result_queue.get_nowait()
            result["isolation"] = "process"
            result["pid"] = proc.pid
            return result

        except Exception as e:
            logger.error(f"[进程] 子Agent {self.agent_id} 执行失败: {e}")
            # 确保进程被清理
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=2)
            return {
                "agent_id": self.agent_id,
                "specialty": self.specialty,
                "isolation": "process",
                "success": False,
                "error": f"进程执行异常: {e}",
                "goal": subtask,
                "elapsed_ms": 0,
            }


def _process_entry(payload: Dict[str, Any], result_queue: multiprocessing.Queue):
    """子进程入口函数（模块级，可被 pickle）

    在子进程中执行任务，捕获所有异常，将结果或错误通过 Queue 回传。
    保证子进程不会因异常而静默崩溃。
    """
    try:
        result = _process_worker(payload)
        result_queue.put(result)
    except Exception as e:
        # 捕获所有异常，回传错误信息
        result_queue.put({
            "agent_id": payload.get("agent_id", "unknown"),
            "specialty": payload.get("specialty", "general"),
            "isolation": "process",
            "success": False,
            "error": f"子进程未捕获异常: {e}",
            "goal": payload.get("subtask", ""),
            "elapsed_ms": 0,
        })


def _process_worker(payload: Dict[str, Any]) -> Dict[str, Any]:
    """子进程工作函数（模块级，可被 pickle）

    在独立进程中:
    1. 重新初始化 sys.path
    2. 创建全新的 TaskExecutor 实例（独立 LLM 客户端）
    3. 执行任务
    4. 返回结果（必须可 pickle）
    """
    base_dir = payload["base_dir"]
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)

    agent_id = payload["agent_id"]
    specialty = payload["specialty"]
    subtask = payload["subtask"]
    context = payload["context"]

    # 在子进程中创建全新的执行器（独立 LLM 客户端和上下文）
    from m_layer.task_executor import TaskExecutor

    executor = TaskExecutor()
    result = executor.run(subtask, context=context, cleanup=True, reflect=True)

    # 过滤不可 pickle 的字段
    result["agent_id"] = agent_id
    result["specialty"] = specialty
    result["isolation"] = "process"
    result["pid"] = os.getpid()

    # 移除可能不可序列化的字段
    for key in list(result.keys()):
        try:
            pickle.dumps(result[key])
        except Exception:
            result[key] = str(result[key])

    return result


# ═══════════════════════════════════════════════════════════
#  多Agent协调器（双模式）
# ═══════════════════════════════════════════════════════════

class MultiAgentCoordinator:
    """多Agent协作协调器（双模式）

    用法:
        # 线程模式（轻量、共享上下文）
        coord = MultiAgentCoordinator(mode="thread")
        coord.assign_subtask("搜索Python教程", specialty="search")
        result = coord.execute_all()

        # 进程模式（隔离、独立上下文）
        coord = MultiAgentCoordinator(mode="process")
        coord.assign_subtask("深度分析架构", specialty="analysis")
        result = coord.execute_all()
    """

    # 模式默认配置
    MODE_CONFIG = {
        "thread": {
            "max_workers": 4,
            "description": "线程级并行（轻量、共享上下文）",
        },
        "process": {
            "max_workers": 2,
            "description": "进程级隔离（独立上下文、深度探索）",
        },
    }

    def __init__(self, mode: str = "thread", max_agents: Optional[int] = None):
        """初始化协调器

        Args:
            mode: 执行模式
                "thread"  - 线程级并行（默认，轻量，共享上下文）
                "process" - 进程级隔离（独立上下文，深度探索）
            max_agents: 最大并发数（None 则使用模式默认值）
        """
        if mode not in self.MODE_CONFIG:
            raise ValueError(f"无效模式 '{mode}'，可选: {list(self.MODE_CONFIG.keys())}")

        self.mode = mode
        config = self.MODE_CONFIG[mode]
        self.max_agents = max_agents if max_agents is not None else config["max_workers"]
        self._subtasks: List[Dict] = []
        self._results: Dict[str, Dict] = {}
        self._lock = threading.Lock()

        logger.info(f"MultiAgentCoordinator 初始化: mode={mode}, max_agents={self.max_agents}")

    def assign_subtask(self, subtask: str, specialty: str = "general",
                       depends_on: Optional[List[str]] = None,
                       subtask_id: str = "",
                       isolation: Optional[str] = None) -> str:
        """分配子任务

        Args:
            subtask: 子任务描述
            specialty: Agent专长（search/analysis/writing/coding/general）
            depends_on: 依赖的其他子任务ID
            subtask_id: 自定义ID（不传则自动生成）
            isolation: 单任务隔离模式覆盖
                None    - 使用协调器默认模式
                "thread" - 强制线程模式
                "process" - 强制进程模式

        Returns:
            子任务ID
        """
        sid = subtask_id or f"sub_{uuid.uuid4().hex[:6]}"
        # 验证 isolation 覆盖
        if isolation is not None and isolation not in self.MODE_CONFIG:
            raise ValueError(f"无效 isolation '{isolation}'")

        self._subtasks.append({
            "subtask_id": sid,
            "subtask": subtask,
            "specialty": specialty,
            "depends_on": depends_on or [],
            "isolation": isolation or self.mode,  # 任务级隔离模式
            "status": "pending",
        })
        return sid

    def execute_all(self) -> Dict[str, Any]:
        """并行执行所有子任务

        Returns:
            {
                "mode": "thread"|"process"|"mixed",
                "total_subtasks": int,
                "completed": int,
                "failed": int,
                "results": {subtask_id: result},
                "summary": "汇总报告",
                "elapsed_ms": float,
                "completed_at": str,
            }
        """
        start_time = time.time()
        total = len(self._subtasks)

        if total == 0:
            return {"mode": self.mode, "total_subtasks": 0, "results": {}, "summary": "无子任务"}

        completed_ids = set()
        failed_ids = set()

        # 检测是否为混合模式（部分任务指定 isolation 覆盖）
        isolations = set(st["isolation"] for st in self._subtasks)
        exec_mode = "mixed" if len(isolations) > 1 else self.mode

        # 线程池处理 thread 任务，进程池处理 process 任务
        # 简化实现：使用线程池统一调度，内部根据 isolation 选择执行方式
        with ThreadPoolExecutor(max_workers=self.max_agents) as pool:
            pending = {}

            while len(completed_ids) + len(failed_ids) < total:
                # 找出可执行的子任务（依赖已满足）
                ready = []
                for st in self._subtasks:
                    sid = st["subtask_id"]
                    if sid in completed_ids or sid in failed_ids:
                        continue
                    deps = st["depends_on"]
                    if all(d in completed_ids for d in deps):
                        ready.append(st)

                for st in ready:
                    sid = st["subtask_id"]
                    # 根据任务级 isolation 选择子代理类型
                    if st["isolation"] == "process":
                        agent = ProcessSubAgent(agent_id=sid, specialty=st["specialty"])
                    else:
                        agent = SubAgent(agent_id=sid, specialty=st["specialty"])

                    # 收集依赖结果作为上下文
                    dep_context = {}
                    for dep_id in st["depends_on"]:
                        if dep_id in self._results:
                            # 仅传递可序列化的关键字段（进程模式要求）
                            dep_result = self._results[dep_id]
                            dep_context[f"dep_{dep_id}"] = {
                                "goal": dep_result.get("goal", ""),
                                "success": dep_result.get("success", False),
                                "output": dep_result.get("output", ""),
                                "summary": dep_result.get("summary", ""),
                            }

                    future = pool.submit(agent.execute, st["subtask"], dep_context)
                    pending[future] = sid
                    st["status"] = "running"

                if not pending:
                    # 无可执行任务，标记剩余为失败
                    for st in self._subtasks:
                        sid = st["subtask_id"]
                        if sid not in completed_ids and sid not in failed_ids:
                            st["status"] = "skipped"
                            failed_ids.add(sid)
                    break

                # 等待任一完成
                for future in as_completed(pending):
                    sid = pending[future]
                    try:
                        result = future.result()
                        with self._lock:
                            self._results[sid] = result
                        completed_ids.add(sid)
                        for st in self._subtasks:
                            if st["subtask_id"] == sid:
                                st["status"] = "done"
                                break
                    except Exception as e:
                        logger.error(f"子任务 {sid} 失败: {e}")
                        failed_ids.add(sid)
                        for st in self._subtasks:
                            if st["subtask_id"] == sid:
                                st["status"] = "failed"
                                break
                    del pending[future]
                    break

        elapsed = (time.time() - start_time) * 1000
        summary = self._generate_summary()

        return {
            "mode": exec_mode,
            "total_subtasks": total,
            "completed": len(completed_ids),
            "failed": len(failed_ids),
            "results": dict(self._results),
            "subtasks": self._subtasks,
            "summary": summary,
            "elapsed_ms": round(elapsed, 2),
            "completed_at": datetime.now().isoformat(),
        }

    def _generate_summary(self) -> str:
        """生成汇总报告"""
        lines = [f"多Agent协作完成: {len(self._results)}/{len(self._subtasks)}个子任务成功"]
        for sid, result in self._results.items():
            goal = result.get("goal", "")[:50]
            success = result.get("success", False)
            elapsed = result.get("elapsed_ms", 0)
            isolation = result.get("isolation", "?")
            tag = "进程" if isolation == "process" else "线程"
            lines.append(f"  [{'✓' if success else '✗'}] {sid}[{tag}]: {goal} ({elapsed:.0f}ms)")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════════════

def quick_multi_agent(subtasks: List[Dict[str, Any]],
                      mode: str = "thread") -> Dict[str, Any]:
    """快速多Agent执行

    Args:
        subtasks: [{subtask, specialty?, depends_on?, isolation?}, ...]
        mode: 执行模式 "thread"|"process"

    Returns:
        执行结果
    """
    coord = MultiAgentCoordinator(mode=mode)
    for st in subtasks:
        coord.assign_subtask(
            subtask=st["subtask"],
            specialty=st.get("specialty", "general"),
            depends_on=st.get("depends_on"),
            isolation=st.get("isolation"),  # 支持任务级覆盖
        )
    return coord.execute_all()


def quick_thread_agents(subtasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """快速线程模式（轻量、共享上下文）"""
    return quick_multi_agent(subtasks, mode="thread")


def quick_process_agents(subtasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """快速进程模式（隔离、独立上下文）

    每个子代理在独立进程中运行，适合深度探索任务。
    注意：进程模式需要子任务参数可 pickle 序列化。
    """
    return quick_multi_agent(subtasks, mode="process")


def quick_mixed_agents(subtasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """快速混合模式

    根据每个任务的 isolation 字段决定使用线程还是进程:
      - 轻量任务（搜索、计算）→ isolation="thread"
      - 重量任务（深度分析、长链路推理）→ isolation="process"
    """
    coord = MultiAgentCoordinator(mode="thread", max_agents=6)  # 线程池统一调度
    for st in subtasks:
        coord.assign_subtask(
            subtask=st["subtask"],
            specialty=st.get("specialty", "general"),
            depends_on=st.get("depends_on"),
            isolation=st.get("isolation", "thread"),  # 默认线程，按需指定 process
        )
    return coord.execute_all()


# ─── 单例 ────────────────────────────────────
_coord_instances: Dict[str, Optional[MultiAgentCoordinator]] = {
    "thread": None,
    "process": None,
}


def get_multi_agent_coordinator(mode: str = "thread") -> MultiAgentCoordinator:
    """获取多Agent协调器单例（按模式缓存）"""
    if mode not in _coord_instances:
        mode = "thread"
    if _coord_instances[mode] is None:
        _coord_instances[mode] = MultiAgentCoordinator(mode=mode)
    return _coord_instances[mode]
