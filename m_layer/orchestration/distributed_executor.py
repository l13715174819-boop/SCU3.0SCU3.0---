# -*- coding: utf-8 -*-
"""
m_layer/distributed_executor.py — 分布式任务执行器（M层）
============================================================
v5.0第三批：跨节点分布式任务执行能力

能力对标：AI助手的分布式/多节点协同执行能力

功能:
  1. 任务分片与结果合并（DistributedExecutor）
  2. 工作节点管理（WorkerNode）：注册、心跳、能力声明、状态管理
  3. 节点注册表（WorkerRegistry）：节点发现、健康检查、负载均衡
  4. 任务分发器（TaskDispatcher）：分发、收集、超时、重试、进度追踪
  5. 工作节点服务端（WorkerServer）：基于http.server的HTTP接口
  6. 本地多进程模拟分布式（无远程节点时降级使用）
  7. 故障处理：心跳超时检测、任务迁移、幂等性保证
  8. 状态持久化到 SCU3_data/distributed_state.json

架构归属：M层（分布式执行层）
依赖：标准库（multiprocessing, http.server, urllib.request, json）
"""
import os
import json
import time
import uuid
import queue
import logging
import threading
import multiprocessing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Any, List, Optional, Callable, Union
from datetime import datetime
from urllib import request as url_request
from urllib.error import URLError, HTTPError
from core.abc import StatusableMixin

logger = logging.getLogger("SCU3.m.distributed")


# ─── 默认配置 ────────────────────────────────────
_DEFAULT_HEARTBEAT_TIMEOUT = 30.0      # 心跳超时阈值（秒）
_DEFAULT_TASK_TIMEOUT = 60.0           # 单任务默认超时（秒）
_DEFAULT_MAX_RETRIES = 3               # 任务最大重试次数
_DEFAULT_WORKER_PORT = 9700            # 工作节点默认端口
_DEFAULT_LOCAL_WORKERS = max(2, (multiprocessing.cpu_count() or 2) - 1)


# ─── 异常定义 ────────────────────────────────────
class DistributedError(Exception):
    """分布式执行基础异常"""


class WorkerUnavailableError(DistributedError):
    """无可用工作节点"""


class TaskTimeoutError(DistributedError):
    """任务执行超时"""


class TaskMigrationError(DistributedError):
    """任务迁移失败"""


# =============================================================================
# WorkerNode — 工作节点
# =============================================================================
class WorkerNode:
    """工作节点：表示一个可执行任务的远程或本地节点

    用法:
        worker = WorkerNode(url="http://127.0.0.1:9700")
        worker.register(capabilities={"cpu": 4, "memory": 8192})
        status = worker.heartbeat()
        result = worker.execute(task)
    """

    # 节点状态枚举
    STATUS_IDLE = "idle"
    STATUS_BUSY = "busy"
    STATUS_OFFLINE = "offline"

    def __init__(self, url: str = "", worker_id: str = "",
                 capabilities: Optional[Dict[str, Any]] = None,
                 local_handler: Optional[Callable] = None):
        """
        Args:
            url: 节点HTTP地址（如 http://127.0.0.1:9700）
            worker_id: 节点ID（不传则自动生成）
            capabilities: 节点能力声明
            local_handler: 本地执行回调（本地模式时使用，避免HTTP）
        """
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        self.url = url.rstrip("/") if url else ""
        self.capabilities: Dict[str, Any] = capabilities or {}
        self.status: str = WorkerNode.STATUS_IDLE
        self.local_handler = local_handler  # 本地模式回调
        self.last_heartbeat: float = time.time()
        self.registered_at: str = datetime.now().isoformat()
        # 统计信息
        self.tasks_total: int = 0
        self.tasks_success: int = 0
        self.tasks_failed: int = 0
        self.current_task_id: Optional[str] = None

    def register(self, url: str = "", capabilities: Optional[Dict[str, Any]] = None) -> bool:
        """节点注册

        Args:
            url: 节点地址（覆盖原有）
            capabilities: 能力声明（覆盖原有）

        Returns:
            是否注册成功
        """
        if url:
            self.url = url.rstrip("/")
        if capabilities:
            self.capabilities = capabilities

        # 默认能力
        self.capabilities.setdefault("cpu", 1)
        self.capabilities.setdefault("memory", 1024)
        self.capabilities.setdefault("gpu", 0)
        self.capabilities.setdefault("special_tools", [])

        # 远程模式：尝试调用远程注册接口
        if self.url and not self.local_handler:
            payload = json.dumps({
                "worker_id": self.worker_id,
                "capabilities": self.capabilities,
            }).encode("utf-8")
            try:
                req = url_request.Request(
                    f"{self.url}/register",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with url_request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "ok":
                        self.status = WorkerNode.STATUS_IDLE
                        self.last_heartbeat = time.time()
                        logger.info(f"节点注册成功: {self.worker_id} @ {self.url}")
                        return True
                    return False
            except (URLError, HTTPError, OSError) as e:
                logger.warning(f"远程注册失败({self.url}): {e}，降级为本地模式")
                self.local_handler = self._default_local_handler
                self.status = WorkerNode.STATUS_IDLE
                return True

        # 本地模式：直接标记为可用
        self.status = WorkerNode.STATUS_IDLE
        self.last_heartbeat = time.time()
        logger.info(f"本地节点注册: {self.worker_id} (cpu={self.capabilities.get('cpu')})")
        return True

    def heartbeat(self) -> Dict[str, Any]:
        """发送心跳，返回节点状态

        Returns:
            {"worker_id", "status", "capabilities", "tasks_total", "timestamp"}
        """
        if self.url and not self.local_handler:
            try:
                req = url_request.Request(
                    f"{self.url}/heartbeat?worker_id={self.worker_id}",
                    method="GET",
                )
                with url_request.urlopen(req, timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.status = data.get("status", self.status)
                    self.last_heartbeat = time.time()
                    return data
            except (URLError, HTTPError, OSError) as e:
                logger.warning(f"心跳失败({self.worker_id}): {e}")
                self.status = WorkerNode.STATUS_OFFLINE
                return {"worker_id": self.worker_id, "status": self.status,
                        "error": str(e)}

        # 本地模式
        self.last_heartbeat = time.time()
        return {
            "worker_id": self.worker_id,
            "status": self.status,
            "capabilities": self.capabilities,
            "tasks_total": self.tasks_total,
            "timestamp": datetime.now().isoformat(),
        }

    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """执行任务

        Args:
            task: 任务定义 {
                "task_id": str,
                "type": str,         # 任务类型
                "payload": Any,      # 任务负载
                "timeout": float,    # 超时（秒）
            }

        Returns:
            {"task_id", "success", "result", "error", "elapsed_ms", "worker_id"}
        """
        task_id = task.get("task_id", "unknown")
        timeout = task.get("timeout", _DEFAULT_TASK_TIMEOUT)
        self.status = WorkerNode.STATUS_BUSY
        self.current_task_id = task_id
        start_time = time.time()

        try:
            if self.local_handler:
                result = self.local_handler(task)
            elif self.url:
                result = self._execute_remote(task, timeout)
            else:
                raise DistributedError(f"节点 {self.worker_id} 无可用执行方式")

            elapsed = (time.time() - start_time) * 1000
            self.tasks_total += 1

            if isinstance(result, dict) and result.get("success", True):
                self.tasks_success += 1
                logger.info(f"节点 {self.worker_id} 完成任务 {task_id} ({elapsed:.0f}ms)")
                return {
                    "task_id": task_id,
                    "success": True,
                    "result": result.get("result", result) if isinstance(result, dict) else result,
                    "error": None,
                    "elapsed_ms": round(elapsed, 2),
                    "worker_id": self.worker_id,
                }
            else:
                self.tasks_failed += 1
                err = result.get("error", "未知错误") if isinstance(result, dict) else str(result)
                return {
                    "task_id": task_id,
                    "success": False,
                    "result": None,
                    "error": err,
                    "elapsed_ms": round(elapsed, 2),
                    "worker_id": self.worker_id,
                }
        except Exception as e:
            self.tasks_total += 1
            self.tasks_failed += 1
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"节点 {self.worker_id} 执行任务 {task_id} 异常: {e}")
            return {
                "task_id": task_id,
                "success": False,
                "result": None,
                "error": str(e),
                "elapsed_ms": round(elapsed, 2),
                "worker_id": self.worker_id,
            }
        finally:
            self.status = WorkerNode.STATUS_IDLE
            self.current_task_id = None

    def _execute_remote(self, task: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """通过HTTP远程执行任务"""
        payload = json.dumps(task).encode("utf-8")
        req = url_request.Request(
            f"{self.url}/execute",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with url_request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def is_alive(self, timeout_threshold: float = _DEFAULT_HEARTBEAT_TIMEOUT) -> bool:
        """检查节点是否存活（基于心跳超时）"""
        return (time.time() - self.last_heartbeat) < timeout_threshold \
            and self.status != WorkerNode.STATUS_OFFLINE

    def can_satisfy(self, requirement: Dict[str, Any]) -> bool:
        """检查节点能力是否满足需求"""
        for key, required in requirement.items():
            if key == "special_tools":
                node_tools = set(self.capabilities.get("special_tools", []))
                needed = set(required) if isinstance(required, list) else {required}
                if not needed.issubset(node_tools):
                    return False
            else:
                if self.capabilities.get(key, 0) < required:
                    return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """序列化节点信息"""
        return {
            "worker_id": self.worker_id,
            "url": self.url,
            "status": self.status,
            "capabilities": self.capabilities,
            "last_heartbeat": self.last_heartbeat,
            "registered_at": self.registered_at,
            "tasks_total": self.tasks_total,
            "tasks_success": self.tasks_success,
            "tasks_failed": self.tasks_failed,
        }

    @staticmethod
    def _default_local_handler(task: Dict[str, Any]) -> Dict[str, Any]:
        """默认本地处理：直接返回负载（占位实现）"""
        return {"success": True, "result": task.get("payload")}


# =============================================================================
# WorkerRegistry — 节点注册表
# =============================================================================
class WorkerRegistry:
    """工作节点注册表：管理节点生命周期与负载均衡

    用法:
        registry = WorkerRegistry()
        registry.add_worker(worker)
        worker = registry.find_available({"cpu": 2})
        worker = registry.select_worker(strategy="round_robin")
    """

    # 负载均衡策略
    STRATEGY_ROUND_ROBIN = "round_robin"
    STRATEGY_LEAST_BUSY = "least_busy"
    STRATEGY_CAPABILITY_MATCH = "capability_match"

    def __init__(self):
        self._workers: Dict[str, WorkerNode] = {}
        self._lock = threading.RLock()
        self._rr_index = 0  # 轮询索引

    def add_worker(self, worker: WorkerNode) -> bool:
        """添加节点"""
        with self._lock:
            if worker.worker_id in self._workers:
                logger.warning(f"节点已存在: {worker.worker_id}，将更新")
            self._workers[worker.worker_id] = worker
            logger.info(f"节点已加入注册表: {worker.worker_id} "
                        f"(共{len(self._workers)}个节点)")
            return True

    def remove_worker(self, worker_id: str) -> bool:
        """移除节点"""
        with self._lock:
            if worker_id in self._workers:
                worker = self._workers.pop(worker_id)
                worker.status = WorkerNode.STATUS_OFFLINE
                logger.info(f"节点已移除: {worker_id}")
                return True
            logger.warning(f"移除节点失败，未找到: {worker_id}")
            return False

    def get_worker(self, worker_id: str) -> Optional[WorkerNode]:
        """按ID获取节点"""
        with self._lock:
            return self._workers.get(worker_id)

    def list_workers(self, status: Optional[str] = None) -> List[WorkerNode]:
        """列出节点"""
        with self._lock:
            workers = list(self._workers.values())
        if status:
            workers = [w for w in workers if w.status == status]
        return workers

    def find_available(self, capability_requirement: Optional[Dict[str, Any]] = None) -> Optional[WorkerNode]:
        """查找满足能力需求的可用节点

        Args:
            capability_requirement: 能力需求，如 {"cpu": 2, "memory": 1024, "gpu": 1}

        Returns:
            可用节点或None
        """
        with self._lock:
            candidates = [w for w in self._workers.values()
                          if w.status == WorkerNode.STATUS_IDLE]
        if capability_requirement:
            candidates = [w for w in candidates if w.can_satisfy(capability_requirement)]
        if not candidates:
            return None
        # 默认返回第一个
        return candidates[0]

    def select_worker(self, strategy: str = STRATEGY_ROUND_ROBIN,
                      capability_requirement: Optional[Dict[str, Any]] = None) -> Optional[WorkerNode]:
        """负载均衡选择节点

        Args:
            strategy: round_robin | least_busy | capability_match
            capability_requirement: 能力需求（capability_match策略时使用）

        Returns:
            选中的节点或None
        """
        with self._lock:
            available = [w for w in self._workers.values()
                         if w.status == WorkerNode.STATUS_IDLE]
            if capability_requirement:
                available = [w for w in available if w.can_satisfy(capability_requirement)]

            if not available:
                return None

            if strategy == self.STRATEGY_ROUND_ROBIN:
                # 轮询
                self._rr_index %= len(available)
                selected = available[self._rr_index]
                self._rr_index += 1
            elif strategy == self.STRATEGY_LEAST_BUSY:
                # 最少负载：失败率最低 + 任务最少
                selected = min(available,
                               key=lambda w: (w.tasks_failed, w.tasks_total))
            elif strategy == self.STRATEGY_CAPABILITY_MATCH:
                # 能力匹配：选择最贴近需求（资源最少占用，留大节点给重任务）
                if not capability_requirement:
                    selected = available[0]
                else:
                    def score(w: WorkerNode) -> int:
                        return sum(w.capabilities.get(k, 0) for k in capability_requirement
                                   if isinstance(w.capabilities.get(k, 0), (int, float)))
                    selected = min(available, key=score)
            else:
                logger.warning(f"未知策略: {strategy}，使用默认轮询")
                selected = available[0]

            return selected

    def health_check_all(self) -> Dict[str, Dict[str, Any]]:
        """对所有节点进行健康检查

        Returns:
            {worker_id: {"alive": bool, "status": str, "latency_ms": float}}
        """
        results: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            workers = list(self._workers.values())

        for worker in workers:
            start = time.time()
            try:
                hb = worker.heartbeat()
                latency = (time.time() - start) * 1000
                alive = hb.get("status") != WorkerNode.STATUS_OFFLINE
                results[worker.worker_id] = {
                    "alive": alive,
                    "status": hb.get("status", "unknown"),
                    "latency_ms": round(latency, 2),
                }
            except Exception as e:
                results[worker.worker_id] = {
                    "alive": False,
                    "status": WorkerNode.STATUS_OFFLINE,
                    "error": str(e),
                    "latency_ms": -1,
                }
                worker.status = WorkerNode.STATUS_OFFLINE

        # 清理失联节点
        offline_ids = [wid for wid, info in results.items()
                       if not info["alive"]]
        for wid in offline_ids:
            logger.warning(f"节点失联: {wid}")

        return results

    def count(self) -> Dict[str, int]:
        """节点统计"""
        with self._lock:
            workers = list(self._workers.values())
        return {
            "total": len(workers),
            "idle": sum(1 for w in workers if w.status == WorkerNode.STATUS_IDLE),
            "busy": sum(1 for w in workers if w.status == WorkerNode.STATUS_BUSY),
            "offline": sum(1 for w in workers if w.status == WorkerNode.STATUS_OFFLINE),
        }

    def to_dict(self) -> Dict[str, Any]:
        """序列化注册表"""
        with self._lock:
            return {wid: w.to_dict() for wid, w in self._workers.items()}


# =============================================================================
# TaskDispatcher — 任务分发器
# =============================================================================
class TaskDispatcher:
    """任务分发器：负责任务的派发、结果收集、超时处理、失败重试与进度追踪

    用法:
        dispatcher = TaskDispatcher(registry)
        task_id = dispatcher.dispatch(task, worker)
        result = dispatcher.collect(task_id)
    """

    # 任务状态
    TASK_DISPATCHED = "dispatched"
    TASK_RUNNING = "running"
    TASK_DONE = "done"
    TASK_FAILED = "failed"
    TASK_TIMEOUT = "timeout"
    TASK_RETRYING = "retrying"

    def __init__(self, registry: WorkerRegistry,
                 max_retries: int = _DEFAULT_MAX_RETRIES,
                 task_timeout: float = _DEFAULT_TASK_TIMEOUT):
        self.registry = registry
        self.max_retries = max_retries
        self.task_timeout = task_timeout
        # 任务表：task_id -> 任务记录
        self._tasks: Dict[str, Dict[str, Any]] = {}
        # 结果缓存：task_id -> result（幂等性）
        self._results: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        # 后台监控线程
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()

    def dispatch(self, task: Dict[str, Any], worker: WorkerNode) -> str:
        """分发任务到指定节点

        Args:
            task: 任务定义（含payload、type等）
            worker: 目标节点

        Returns:
            task_id
        """
        task_id = task.get("task_id") or f"task_{uuid.uuid4().hex[:8]}"
        task["task_id"] = task_id
        task.setdefault("timeout", self.task_timeout)

        record = {
            "task_id": task_id,
            "task": task,
            "worker_id": worker.worker_id,
            "status": self.TASK_DISPATCHED,
            "attempts": 0,
            "dispatched_at": datetime.now().isoformat(),
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
            "progress": 0.0,
        }

        with self._lock:
            # 幂等性：若已有完成结果，直接返回
            if task_id in self._results:
                logger.info(f"任务 {task_id} 命中幂等缓存，跳过重复分发")
                return task_id
            self._tasks[task_id] = record

        # 异步执行
        thread = threading.Thread(
            target=self._execute_with_retry,
            args=(task_id, worker),
            daemon=True,
        )
        thread.start()
        logger.info(f"任务已分发: {task_id} -> 节点 {worker.worker_id}")
        return task_id

    def _execute_with_retry(self, task_id: str, worker: WorkerNode) -> None:
        """带重试的任务执行"""
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return
            record["status"] = self.TASK_RUNNING
            record["started_at"] = datetime.now().isoformat()

        task = record["task"]
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            with self._lock:
                record["attempts"] = attempt
                record["progress"] = min(0.9, attempt / (self.max_retries + 1))

            # 选择当前worker（首次用传入的，重试时换节点）
            current_worker = worker if attempt == 1 else self._select_retry_worker()
            if current_worker is None:
                last_error = "无可用重试节点"
                break

            try:
                result = current_worker.execute(task)
                if result.get("success"):
                    with self._lock:
                        record["status"] = self.TASK_DONE
                        record["result"] = result.get("result")
                        record["completed_at"] = datetime.now().isoformat()
                        record["progress"] = 1.0
                        self._results[task_id] = result
                    logger.info(f"任务 {task_id} 成功 (尝试{attempt}次)")
                    return
                else:
                    last_error = result.get("error", "未知错误")
                    logger.warning(f"任务 {task_id} 第{attempt}次失败: {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"任务 {task_id} 第{attempt}次异常: {e}")

            if attempt < self.max_retries:
                with self._lock:
                    record["status"] = self.TASK_RETRYING
                time.sleep(0.2 * attempt)  # 简单退避

        # 全部失败
        with self._lock:
            record["status"] = self.TASK_FAILED
            record["error"] = last_error
            record["completed_at"] = datetime.now().isoformat()
        logger.error(f"任务 {task_id} 最终失败: {last_error}")

    def _select_retry_worker(self) -> Optional[WorkerNode]:
        """为重试选择新节点"""
        return self.registry.select_worker(strategy=WorkerRegistry.STRATEGY_LEAST_BUSY)

    def collect(self, task_id: str, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """收集任务结果（阻塞等待）

        Args:
            task_id: 任务ID
            timeout: 等待超时（秒），None表示使用任务默认超时

        Returns:
            任务结果或None（超时/失败）
        """
        wait_timeout = timeout or self.task_timeout
        deadline = time.time() + wait_timeout

        while time.time() < deadline:
            with self._lock:
                record = self._tasks.get(task_id)
            if not record:
                return None
            if record["status"] in (self.TASK_DONE, self.TASK_FAILED, self.TASK_TIMEOUT):
                return record.get("result") if record["status"] == self.TASK_DONE else None
            time.sleep(0.1)

        # 超时
        self.timeout_handler(task_id)
        return None

    def collect_now(self, task_id: str) -> Optional[Dict[str, Any]]:
        """非阻塞获取结果"""
        with self._lock:
            record = self._tasks.get(task_id)
        if record and record["status"] == self.TASK_DONE:
            return record.get("result")
        return None

    def timeout_handler(self, task_id: str) -> None:
        """超时处理：标记超时并触发迁移"""
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return
            if record["status"] in (self.TASK_DONE, self.TASK_FAILED):
                return
            record["status"] = self.TASK_TIMEOUT
            record["error"] = "任务超时"
            record["completed_at"] = datetime.now().isoformat()
        logger.warning(f"任务 {task_id} 超时，尝试迁移")
        try:
            self.retry(task_id, None)
        except TaskMigrationError as e:
            logger.error(f"任务 {task_id} 迁移失败: {e}")

    def retry(self, task_id: str, new_worker: Optional[WorkerNode]) -> bool:
        """失败重试：将任务迁移到新节点

        Args:
            task_id: 任务ID
            new_worker: 新节点（None则自动选择）

        Returns:
            是否成功启动重试
        """
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                raise TaskMigrationError(f"任务不存在: {task_id}")

        worker = new_worker or self._select_retry_worker()
        if worker is None:
            raise TaskMigrationError(f"无可用节点用于重试: {task_id}")

        logger.info(f"任务 {task_id} 迁移到节点 {worker.worker_id}")
        thread = threading.Thread(
            target=self._execute_with_retry,
            args=(task_id, worker),
            daemon=True,
        )
        thread.start()
        return True

    def get_progress(self, task_id: str) -> Dict[str, Any]:
        """获取任务进度"""
        with self._lock:
            record = self._tasks.get(task_id)
        if not record:
            return {"task_id": task_id, "status": "unknown", "progress": 0.0}
        return {
            "task_id": task_id,
            "status": record["status"],
            "progress": record.get("progress", 0.0),
            "attempts": record.get("attempts", 0),
            "worker_id": record.get("worker_id"),
            "dispatched_at": record.get("dispatched_at"),
            "completed_at": record.get("completed_at"),
        }

    def start_monitor(self, interval: float = 10.0) -> None:
        """启动后台监控线程（节点心跳检测）"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
        )
        self._monitor_thread.start()
        logger.info(f"任务监控线程已启动 (间隔{interval}s)")

    def stop_monitor(self) -> None:
        """停止监控线程"""
        self._stop_monitor.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)

    def _monitor_loop(self, interval: float) -> None:
        """监控循环：检测超时任务与失联节点"""
        while not self._stop_monitor.is_set():
            try:
                # 检测超时任务
                now = time.time()
                with self._lock:
                    task_ids = list(self._tasks.keys())
                for tid in task_ids:
                    with self._lock:
                        rec = self._tasks.get(tid)
                    if not rec or rec["status"] in (self.TASK_DONE, self.TASK_FAILED,
                                                    self.TASK_TIMEOUT):
                        continue
                    started = rec.get("started_at")
                    if started:
                        try:
                            started_ts = datetime.fromisoformat(started).timestamp()
                            task_timeout = rec["task"].get("timeout", self.task_timeout)
                            if now - started_ts > task_timeout:
                                self.timeout_handler(tid)
                        except (ValueError, TypeError):
                            pass
            except Exception as e:
                logger.warning(f"监控循环异常: {e}")
            self._stop_monitor.wait(interval)

    def to_dict(self) -> Dict[str, Any]:
        """序列化任务表"""
        with self._lock:
            return {tid: {k: v for k, v in rec.items() if k != "task"}
                    for tid, rec in self._tasks.items()}


# =============================================================================
# WorkerServer — 工作节点HTTP服务端
# =============================================================================
class WorkerServer:
    """工作节点服务端：基于http.server的HTTP接口

    提供端点:
      - POST /register     节点注册
      - GET  /heartbeat    心跳
      - POST /execute      任务执行
      - GET  /health       健康检查
      - GET  /status       节点状态

    用法:
        server = WorkerServer(port=9700, handler=my_task_handler)
        server.start()  # 非阻塞启动
        ...
        server.stop()
    """

    def __init__(self, port: int = _DEFAULT_WORKER_PORT,
                 handler: Optional[Callable[[Dict], Dict]] = None,
                 host: str = "127.0.0.1"):
        """
        Args:
            port: 监听端口
            handler: 任务处理回调，接收task dict，返回result dict
            host: 监听地址
        """
        self.port = port
        self.host = host
        self.handler = handler or self._default_handler
        self._task_queue: queue.Queue = queue.Queue()
        self._results: Dict[str, Dict] = {}
        self._results_lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._worker_threads: List[threading.Thread] = []
        self._running = False
        # 节点自身能力
        self.capabilities: Dict[str, Any] = {
            "cpu": multiprocessing.cpu_count() or 1,
            "memory": 4096,
            "gpu": 0,
            "special_tools": [],
        }

    def start(self, background: bool = True) -> bool:
        """启动服务"""
        if self._running:
            return True

        # 创建请求处理器类（闭包绑定self）
        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # 静默默认日志
                logger.debug(f"[WorkerServer] {self.address_string()} - {fmt % args}")

            def _send_json(self, code: int, data: Dict):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> Dict:
                length = int(self.headers.get("Content-Length", 0))
                if length == 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    return json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return {}

            def do_POST(self):
                if self.path == "/register":
                    data = self._read_body()
                    wid = data.get("worker_id", "anonymous")
                    caps = data.get("capabilities", {})
                    server_ref.capabilities.update(caps)
                    logger.info(f"节点注册: {wid}")
                    self._send_json(200, {"status": "ok", "worker_id": wid})
                elif self.path == "/execute":
                    task = self._read_body()
                    task_id = task.get("task_id", "unknown")
                    server_ref._task_queue.put(task)
                    # 同步等待结果（简化实现）
                    result = server_ref._process_task(task)
                    self._send_json(200, result)
                else:
                    self._send_json(404, {"error": "not found"})

            def do_GET(self):
                if self.path.startswith("/heartbeat"):
                    self._send_json(200, {
                        "status": "idle" if server_ref._task_queue.qsize() == 0 else "busy",
                        "queue_size": server_ref._task_queue.qsize(),
                        "capabilities": server_ref.capabilities,
                        "timestamp": datetime.now().isoformat(),
                    })
                elif self.path == "/health":
                    self._send_json(200, {
                        "status": "ok",
                        "queue_size": server_ref._task_queue.qsize(),
                        "results_cached": len(server_ref._results),
                    })
                elif self.path == "/status":
                    self._send_json(200, {
                        "running": server_ref._running,
                        "port": server_ref.port,
                        "queue_size": server_ref._task_queue.qsize(),
                        "capabilities": server_ref.capabilities,
                    })
                else:
                    self._send_json(404, {"error": "not found"})

        try:
            self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
            self._running = True
            if background:
                self._thread = threading.Thread(
                    target=self._server.serve_forever,
                    daemon=True,
                )
                self._thread.start()
                logger.info(f"WorkerServer 启动于 {self.host}:{self.port} (后台)")
            else:
                logger.info(f"WorkerServer 启动于 {self.host}:{self.port} (前台)")
                self._server.serve_forever()
            return True
        except OSError as e:
            logger.error(f"WorkerServer 启动失败: {e}")
            self._running = False
            return False

    def stop(self) -> None:
        """停止服务"""
        self._running = False
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as e:
                logger.warning(f"WorkerServer 停止异常: {e}")
            self._server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        logger.info("WorkerServer 已停止")

    def _process_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """处理单个任务（同步）"""
        task_id = task.get("task_id", "unknown")
        start = time.time()
        try:
            result = self.handler(task)
            elapsed = (time.time() - start) * 1000
            with self._results_lock:
                self._results[task_id] = result
            return {
                "task_id": task_id,
                "success": True,
                "result": result.get("result", result) if isinstance(result, dict) else result,
                "elapsed_ms": round(elapsed, 2),
            }
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return {
                "task_id": task_id,
                "success": False,
                "error": str(e),
                "elapsed_ms": round(elapsed, 2),
            }

    @staticmethod
    def _default_handler(task: Dict[str, Any]) -> Dict[str, Any]:
        """默认任务处理器：返回payload"""
        return {"success": True, "result": task.get("payload")}


# =============================================================================
# 本地多进程模拟模式
# =============================================================================
def _local_worker_process(task: Dict[str, Any]) -> Dict[str, Any]:
    """本地工作进程函数（必须在模块顶层以支持pickle）

    Args:
        task: {"task_id", "type", "payload", "handler_module", "handler_func"}

    Returns:
        {"task_id", "success", "result", "error"}
    """
    task_id = task.get("task_id", "unknown")
    start = time.time()
    try:
        # 如果指定了handler，动态导入并调用
        handler_module = task.get("handler_module")
        handler_func = task.get("handler_func")
        payload = task.get("payload")

        if handler_module and handler_func:
            import importlib
            mod = importlib.import_module(handler_module)
            func = getattr(mod, handler_func)
            result = func(payload)
        else:
            # 默认：直接返回payload
            result = payload

        return {
            "task_id": task_id,
            "success": True,
            "result": result,
            "error": None,
            "elapsed_ms": round((time.time() - start) * 1000, 2),
        }
    except Exception as e:
        return {
            "task_id": task_id,
            "success": False,
            "result": None,
            "error": str(e),
            "elapsed_ms": round((time.time() - start) * 1000, 2),
        }


class LocalMultiProcessExecutor:
    """本地多进程模拟分布式执行

    当无远程节点时，使用multiprocessing.Pool模拟分布式。
    将任务分片到多个进程并行执行。

    用法:
        local = LocalMultiProcessExecutor(workers=4)
        results = local.execute_batch(subtasks)
    """

    def __init__(self, workers: int = _DEFAULT_LOCAL_WORKERS):
        self.workers = max(1, workers)
        self._pool: Optional[multiprocessing.Pool] = None

    def _get_pool(self) -> multiprocessing.Pool:
        if self._pool is None:
            self._pool = multiprocessing.Pool(processes=self.workers)
            logger.info(f"本地进程池启动: {self.workers}个worker")
        return self._pool

    def execute_batch(self, subtasks: List[Dict[str, Any]],
                      timeout: float = _DEFAULT_TASK_TIMEOUT) -> List[Dict[str, Any]]:
        """批量并行执行子任务

        Args:
            subtasks: 子任务列表
            timeout: 单任务超时

        Returns:
            结果列表（与subtasks顺序对应）
        """
        if not subtasks:
            return []
        pool = self._get_pool()
        # 异步提交所有任务
        async_results = []
        for st in subtasks:
            ar = pool.apply_async(_local_worker_process, (st,))
            async_results.append(ar)

        # 收集结果
        results = []
        for i, ar in enumerate(async_results):
            try:
                res = ar.get(timeout=timeout)
                results.append(res)
            except multiprocessing.TimeoutError:
                logger.warning(f"本地子任务{i}超时")
                results.append({
                    "task_id": subtasks[i].get("task_id", f"local_{i}"),
                    "success": False,
                    "result": None,
                    "error": "本地执行超时",
                })
            except Exception as e:
                logger.error(f"本地子任务{i}异常: {e}")
                results.append({
                    "task_id": subtasks[i].get("task_id", f"local_{i}"),
                    "success": False,
                    "result": None,
                    "error": str(e),
                })
        return results

    def execute_single(self, task: Dict[str, Any],
                       timeout: float = _DEFAULT_TASK_TIMEOUT) -> Dict[str, Any]:
        """执行单个任务"""
        return _local_worker_process(task)

    def close(self) -> None:
        """关闭进程池"""
        if self._pool:
            self._pool.close()
            self._pool.join()
            self._pool = None
            logger.info("本地进程池已关闭")


# =============================================================================
# DistributedExecutor — 分布式执行器（主类）
# =============================================================================
class DistributedExecutor(StatusableMixin):
    """分布式任务执行器：协调任务分片、节点调度、结果合并

    用法:
        executor = DistributedExecutor()
        # 添加节点（远程）
        worker = WorkerNode(url="http://127.0.0.1:9701")
        worker.register(capabilities={"cpu": 4})
        executor.registry.add_worker(worker)
        # 执行
        result = executor.execute_distributed(task, workers=4)

        # 无远程节点时自动降级为本地多进程
        result = executor.execute_distributed(task, workers=4)
    """

    def __init__(self, data_dir: Optional[str] = None,
                 use_local_fallback: bool = True):
        """
        Args:
            data_dir: 状态持久化目录
            use_local_fallback: 无远程节点时是否使用本地多进程
        """
        if data_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, "SCU3_data")
        self._data_dir = data_dir
        os.makedirs(self._data_dir, exist_ok=True)
        self._state_file = os.path.join(self._data_dir, "distributed_state.json")

        self.registry = WorkerRegistry()
        self.dispatcher = TaskDispatcher(self.registry)
        self.use_local_fallback = use_local_fallback
        self._local_executor: Optional[LocalMultiProcessExecutor] = None
        self._state_lock = threading.Lock()

        # 加载已持久化的状态
        self._load_state()

    # ─── 核心接口 ────────────────────────────────────

    def execute_distributed(self, task: Dict[str, Any],
                            workers: int = 2,
                            capability_requirement: Optional[Dict[str, Any]] = None,
                            merge_strategy: str = "concat") -> Dict[str, Any]:
        """分布式执行任务

        Args:
            task: 任务定义 {
                "goal": str,
                "payload": Any,        # 可分片的负载（list或dict）
                "type": str,
                "timeout": float,
            }
            workers: 期望工作节点数（也是分片数）
            capability_requirement: 节点能力需求
            merge_strategy: 结果合并策略 concat|sum|avg|max|min|custom

        Returns:
            {
                "task_id": str,
                "success": bool,
                "result": Any,           # 合并后的最终结果
                "subtasks": [...],
                "subtask_results": [...],
                "workers_used": int,
                "mode": "distributed"|"local",
                "elapsed_ms": float,
            }
        """
        task_id = task.get("task_id") or f"dist_{uuid.uuid4().hex[:8]}"
        task["task_id"] = task_id
        start_time = time.time()

        # 任务分片
        subtasks = self.split_task(task, workers)
        logger.info(f"任务 {task_id} 分片为 {len(subtasks)} 个子任务")

        # 选择执行模式
        available = self.registry.find_available(capability_requirement)
        if available is not None or not self.use_local_fallback:
            mode = "distributed"
            subtask_results = self._execute_on_workers(
                subtasks, workers, capability_requirement, task.get("timeout"))
        else:
            mode = "local"
            logger.info(f"无可用远程节点，任务 {task_id} 降级为本地多进程模式")
            subtask_results = self._execute_local(subtasks, workers)

        # 合并结果
        final_result = self.merge_results(subtask_results, strategy=merge_strategy)
        elapsed = (time.time() - start_time) * 1000
        success = all(r.get("success") for r in subtask_results)

        report = {
            "task_id": task_id,
            "success": success,
            "result": final_result,
            "subtasks": subtasks,
            "subtask_results": subtask_results,
            "workers_used": len(subtask_results),
            "mode": mode,
            "elapsed_ms": round(elapsed, 2),
            "completed_at": datetime.now().isoformat(),
        }

        # 持久化状态
        self._save_state()
        logger.info(f"分布式任务 {task_id} 完成: success={success}, "
                    f"mode={mode}, elapsed={elapsed:.0f}ms")
        return report

    def split_task(self, task: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
        """任务分片：将任务拆分为n个子任务

        支持的payload类型:
          - list: 按元素分片
          - dict: 按key分组
          - str/int: 不可分片，复制为n份
          - callable负载：通过handler_module/handler_func指定

        Args:
            task: 原始任务
            n: 分片数

        Returns:
            子任务列表
        """
        n = max(1, n)
        task_id = task.get("task_id", "dist")
        payload = task.get("payload")
        timeout = task.get("timeout", _DEFAULT_TASK_TIMEOUT)

        subtasks: List[Dict[str, Any]] = []

        if isinstance(payload, list):
            # 列表分片：均分元素
            chunk_size = (len(payload) + n - 1) // n
            for i in range(n):
                chunk = payload[i * chunk_size:(i + 1) * chunk_size]
                subtasks.append({
                    "task_id": f"{task_id}_sub{i}",
                    "parent_id": task_id,
                    "type": task.get("type", "default"),
                    "payload": chunk,
                    "chunk_index": i,
                    "chunk_total": n,
                    "timeout": timeout,
                    "handler_module": task.get("handler_module"),
                    "handler_func": task.get("handler_func"),
                })
        elif isinstance(payload, dict):
            # dict分片：按key分组
            keys = list(payload.keys())
            chunk_size = (len(keys) + n - 1) // n
            for i in range(n):
                chunk_keys = keys[i * chunk_size:(i + 1) * chunk_size]
                chunk = {k: payload[k] for k in chunk_keys}
                subtasks.append({
                    "task_id": f"{task_id}_sub{i}",
                    "parent_id": task_id,
                    "type": task.get("type", "default"),
                    "payload": chunk,
                    "chunk_index": i,
                    "chunk_total": n,
                    "timeout": timeout,
                    "handler_module": task.get("handler_module"),
                    "handler_func": task.get("handler_func"),
                })
        else:
            # 不可分片：复制为n份（每个worker处理相同任务，用于冗余/对比）
            for i in range(n):
                subtasks.append({
                    "task_id": f"{task_id}_sub{i}",
                    "parent_id": task_id,
                    "type": task.get("type", "default"),
                    "payload": payload,
                    "chunk_index": i,
                    "chunk_total": n,
                    "timeout": timeout,
                    "handler_module": task.get("handler_module"),
                    "handler_func": task.get("handler_func"),
                })

        return subtasks

    def merge_results(self, subtask_results: List[Dict[str, Any]],
                      strategy: str = "concat") -> Any:
        """合并子任务结果

        Args:
            subtask_results: 子任务结果列表
            strategy: 合并策略
                - concat: 列表拼接（默认）
                - sum: 数值求和
                - avg: 数值平均
                - max: 取最大值
                - min: 取最小值
                - dict_merge: 字典合并
                - first: 取第一个成功结果

        Returns:
            合并后的最终结果
        """
        # 提取成功结果
        success_results = [r.get("result") for r in subtask_results if r.get("success")]
        if not success_results:
            errors = [r.get("error") for r in subtask_results if not r.get("success")]
            return {"success": False, "errors": errors or ["全部子任务失败"]}

        if strategy == "concat":
            # 列表拼接
            merged: List = []
            for r in success_results:
                if isinstance(r, list):
                    merged.extend(r)
                elif isinstance(r, dict) and "result" in r:
                    merged.append(r["result"])
                else:
                    merged.append(r)
            return merged
        elif strategy == "sum":
            nums = [self._to_number(r) for r in success_results]
            return sum(n for n in nums if n is not None)
        elif strategy == "avg":
            nums = [self._to_number(r) for r in success_results if self._to_number(r) is not None]
            return sum(nums) / len(nums) if nums else 0
        elif strategy == "max":
            nums = [self._to_number(r) for r in success_results if self._to_number(r) is not None]
            return max(nums) if nums else None
        elif strategy == "min":
            nums = [self._to_number(r) for r in success_results if self._to_number(r) is not None]
            return min(nums) if nums else None
        elif strategy == "dict_merge":
            merged_dict: Dict[str, Any] = {}
            for r in success_results:
                if isinstance(r, dict):
                    merged_dict.update(r)
            return merged_dict
        elif strategy == "first":
            return success_results[0]
        else:
            logger.warning(f"未知合并策略: {strategy}，使用concat")
            return success_results

    @staticmethod
    def _to_number(value: Any) -> Optional[Union[int, float]]:
        """转换为数值"""
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, dict):
            for k in ("value", "result", "sum", "count"):
                if k in value and isinstance(value[k], (int, float)):
                    return value[k]
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # ─── 执行模式 ────────────────────────────────────

    def _execute_on_workers(self, subtasks: List[Dict[str, Any]],
                            workers: int,
                            capability_requirement: Optional[Dict[str, Any]],
                            timeout: Optional[float]) -> List[Dict[str, Any]]:
        """在远程工作节点上执行子任务"""
        results: List[Dict[str, Any]] = [None] * len(subtasks)  # type: ignore
        threads: List[threading.Thread] = []

        def _run_subtask(idx: int, subtask: Dict[str, Any]):
            worker = self.registry.select_worker(
                strategy=WorkerRegistry.STRATEGY_LEAST_BUSY,
                capability_requirement=capability_requirement,
            )
            if worker is None:
                results[idx] = {
                    "task_id": subtask.get("task_id"),
                    "success": False,
                    "result": None,
                    "error": "无可用节点",
                    "worker_id": None,
                }
                return
            task_id = self.dispatcher.dispatch(subtask, worker)
            result = self.dispatcher.collect(task_id, timeout=timeout)
            results[idx] = {
                "task_id": subtask.get("task_id"),
                "success": result is not None,
                "result": result,
                "error": None if result is not None else "执行失败或超时",
                "worker_id": worker.worker_id,
            }

        # 并行分发
        for i, st in enumerate(subtasks):
            t = threading.Thread(target=_run_subtask, args=(i, st))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=timeout or _DEFAULT_TASK_TIMEOUT)

        # 处理未完成的
        for i, r in enumerate(results):
            if r is None:
                results[i] = {
                    "task_id": subtasks[i].get("task_id"),
                    "success": False,
                    "result": None,
                    "error": "线程未返回",
                    "worker_id": None,
                }
        return results

    def _execute_local(self, subtasks: List[Dict[str, Any]],
                       workers: int) -> List[Dict[str, Any]]:
        """本地多进程执行子任务"""
        if self._local_executor is None:
            self._local_executor = LocalMultiProcessExecutor(workers=workers)
        return self._local_executor.execute_batch(subtasks)

    # ─── 节点管理 ────────────────────────────────────

    def add_remote_worker(self, url: str,
                          capabilities: Optional[Dict[str, Any]] = None) -> WorkerNode:
        """添加远程工作节点"""
        worker = WorkerNode(url=url, capabilities=capabilities)
        worker.register(capabilities=capabilities)
        self.registry.add_worker(worker)
        return worker

    def add_local_worker(self, handler: Optional[Callable] = None,
                         capabilities: Optional[Dict[str, Any]] = None) -> WorkerNode:
        """添加本地工作节点（同进程内执行）"""
        worker = WorkerNode(
            worker_id=f"local_{uuid.uuid4().hex[:6]}",
            capabilities=capabilities,
            local_handler=handler or WorkerNode._default_local_handler,
        )
        worker.register(capabilities=capabilities)
        self.registry.add_worker(worker)
        return worker

    def start_local_server(self, port: int = _DEFAULT_WORKER_PORT,
                           handler: Optional[Callable] = None) -> WorkerServer:
        """启动本地工作节点服务端"""
        server = WorkerServer(port=port, handler=handler)
        server.start(background=True)
        return server

    def health_check(self) -> Dict[str, Any]:
        """整体健康检查"""
        workers_health = self.registry.health_check_all()
        counts = self.registry.count()
        return {
            "workers": workers_health,
            "summary": counts,
            "timestamp": datetime.now().isoformat(),
        }

    # ─── 状态持久化 ────────────────────────────────────

    def _save_state(self) -> None:
        """持久化状态到磁盘"""
        state = {
            "saved_at": datetime.now().isoformat(),
            "workers": self.registry.to_dict(),
            "tasks": self.dispatcher.to_dict(),
        }
        try:
            with self._state_lock:
                with open(self._state_file, "w", encoding="utf-8") as f:
                    f.write(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"状态持久化失败: {e}")

    def _load_state(self) -> None:
        """从磁盘加载状态"""
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                state = json.loads(f.read())
            # 恢复节点（仅恢复信息，不自动连接）
            for wid, winfo in state.get("workers", {}).items():
                worker = WorkerNode(
                    url=winfo.get("url", ""),
                    worker_id=wid,
                    capabilities=winfo.get("capabilities", {}),
                )
                worker.status = winfo.get("status", WorkerNode.STATUS_OFFLINE)
                worker.registered_at = winfo.get("registered_at", worker.registered_at)
                worker.tasks_total = winfo.get("tasks_total", 0)
                worker.tasks_success = winfo.get("tasks_success", 0)
                worker.tasks_failed = winfo.get("tasks_failed", 0)
                # 加载时标记为offline，需心跳确认
                worker.status = WorkerNode.STATUS_OFFLINE
                self.registry.add_worker(worker)
            logger.info(f"已加载 {len(state.get('workers', {}))} 个节点状态")
        except Exception as e:
            logger.warning(f"状态加载失败: {e}")

    def shutdown(self) -> None:
        """关闭执行器，释放资源"""
        self.dispatcher.stop_monitor()
        if self._local_executor:
            self._local_executor.close()
            self._local_executor = None
        self._save_state()
        logger.info("DistributedExecutor 已关闭")


# =============================================================================
# 单例
# =============================================================================
_distributed_instance: Optional[DistributedExecutor] = None
_instance_lock = threading.Lock()


def get_distributed_executor() -> DistributedExecutor:
    """获取分布式执行器单例

    Returns:
        DistributedExecutor 单例实例
    """
    global _distributed_instance
    if _distributed_instance is None:
        with _instance_lock:
            if _distributed_instance is None:
                _distributed_instance = DistributedExecutor()
                logger.info("DistributedExecutor 单例已创建")
    return _distributed_instance
