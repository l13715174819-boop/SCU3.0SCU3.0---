# -*- coding: utf-8 -*-
"""api/distributed.py — 分布式执行路由

从 server.py 抽取的 8 个分布式执行路由：
  POST /distributed/execute                       — 分布式执行任务
  POST /distributed/split                         — 任务分片
  POST /distributed/merge                         — 结果合并
  GET  /distributed/workers                       — 列出工作节点
  POST /distributed/workers/add                   — 添加工作节点（管理员）
  POST /distributed/workers/{worker_id}/remove    — 移除工作节点（管理员）
  GET  /distributed/health                        — 分布式健康检查
  GET  /distributed/status                        — 分布式状态（线程/进程模式详情）
"""
import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key

logger = logging.getLogger("SCU3.api.distributed")

router = APIRouter(tags=["distributed"])


# ─── 请求模型 ────────────────────────────────
class DistributedExecuteRequest(BaseModel):
    task: Dict[str, Any]
    workers: int = 2
    capability_requirement: Dict[str, Any] = {}
    merge_strategy: str = "concat"


class TaskSplitRequest(BaseModel):
    task: Dict[str, Any]
    n: int = 2


class TaskMergeRequest(BaseModel):
    subtask_results: list
    strategy: str = "concat"


class WorkerAddRequest(BaseModel):
    url: str = ""
    capabilities: Dict[str, Any] = {}
    local: bool = True


# ─── 路由 ────────────────────────────────
@router.post("/distributed/execute")
async def distributed_execute(req: DistributedExecuteRequest,
                              api_key: str = Depends(verify_api_key)):
    """分布式执行任务"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        result = get_distributed_executor().execute_distributed(
            req.task, workers=req.workers,
            capability_requirement=req.capability_requirement or None,
            merge_strategy=req.merge_strategy)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/distributed/split")
async def distributed_split(req: TaskSplitRequest,
                            api_key: str = Depends(verify_api_key)):
    """任务分片"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        subtasks = get_distributed_executor().split_task(req.task, req.n)
        return JSONResponse({"success": True, "subtasks": subtasks,
                             "count": len(subtasks)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/distributed/merge")
async def distributed_merge(req: TaskMergeRequest,
                            api_key: str = Depends(verify_api_key)):
    """结果合并"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        merged = get_distributed_executor().merge_results(req.subtask_results, req.strategy)
        return JSONResponse({"success": True, "result": merged})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/distributed/workers")
async def distributed_workers(api_key: str = Depends(verify_api_key)):
    """列出工作节点"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        registry = get_distributed_executor().registry
        workers = [w.to_dict() for w in registry.list_workers()]
        return JSONResponse({"success": True, "workers": workers,
                             "counts": registry.count()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/distributed/workers/add")
async def distributed_workers_add(req: WorkerAddRequest,
                                  api_key: str = Depends(verify_admin_key)):
    """添加工作节点（需管理员权限）"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        executor = get_distributed_executor()
        if req.local:
            worker = executor.add_local_worker(capabilities=req.capabilities or None)
        else:
            worker = executor.add_remote_worker(req.url, req.capabilities or None)
        return JSONResponse({"success": True, "worker_id": worker.worker_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/distributed/workers/{worker_id}/remove")
async def distributed_workers_remove(worker_id: str,
                                     api_key: str = Depends(verify_admin_key)):
    """移除工作节点（需管理员权限）"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        ok = get_distributed_executor().registry.remove_worker(worker_id)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/distributed/health")
async def distributed_health(api_key: str = Depends(verify_api_key)):
    """分布式健康检查"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        result = get_distributed_executor().health_check()
        return JSONResponse({"success": True, "health": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/distributed/status")
async def distributed_status(task: str = "", api_key: str = Depends(verify_api_key)):
    """分布式状态：从多Agent协调器取线程/进程模式真实状态"""
    try:
        from m_layer.multi_agent import _coord_instances
        workers_online = 0
        workers_total = 0
        tasks_running = 0
        tasks_completed = 0
        mode_details = {}
        for mode, coord in _coord_instances.items():
            if coord is None:
                continue
            subtasks = getattr(coord, "_subtasks", [])
            results = getattr(coord, "_results", {})
            running = sum(1 for s in subtasks if s.get("status") == "running")
            completed = sum(1 for s in subtasks if s.get("status") == "done")
            max_workers = getattr(coord, "max_agents", 0)
            workers_total += max_workers
            workers_online += min(max_workers, max(0, max_workers - running))
            tasks_running += running
            tasks_completed += len(results) or completed
            mode_details[mode] = {
                "max_workers": max_workers,
                "subtasks_total": len(subtasks),
                "running": running,
                "completed": len(results) or completed,
            }

        if task:
            return JSONResponse({"success": True, "data": {
                "shards": [],
                "progress": 0,
                "task": task,
                "mode_details": mode_details,
            }})
        return JSONResponse({"success": True, "data": {
            "workers_online": workers_online,
            "workers_total": workers_total,
            "tasks_running": tasks_running,
            "tasks_completed": tasks_completed,
            "mode_details": mode_details,
        }})
    except Exception as e:
        logger.error(f"分布式状态查询失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})
