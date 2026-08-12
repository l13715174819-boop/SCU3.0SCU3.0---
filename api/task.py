# -*- coding: utf-8 -*-
"""api/task.py — 任务持久化与可视化路由

从 server.py 抽取的任务与可视化路由：
  GET    /temp/resources            — 列出临时资源
  POST   /temp/cleanup              — 清理临时资源
  GET    /temp/history              — 清理历史
  POST   /task/checkpoint           — 保存任务检查点
  GET    /task/checkpoint/{task_id} — 加载任务检查点
  DELETE /task/checkpoint/{task_id} — 删除任务检查点
  GET    /task/checkpoints          — 列出所有可恢复的检查点
  POST   /visualize/plan            — 生成执行计划的 Mermaid 流程图
  POST   /visualize/report          — 生成执行报告的状态图
  POST   /visualize/multiagent      — 生成多 Agent 协作图
"""
import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

from api.deps import verify_api_key

logger = logging.getLogger("SCU3.api.task")

router = APIRouter(tags=["task"])


# ─── 请求模型 ────────────────────────────────
class CheckpointRequest(BaseModel):
    task_id: str
    plan: Dict[str, Any]
    current_step: int
    step_context: Dict[str, Any] = {}
    status: str = "running"


class VisualizePlanRequest(BaseModel):
    plan: Dict[str, Any]


class VisualizeReportRequest(BaseModel):
    report: Dict[str, Any]


class VisualizeMultiAgentRequest(BaseModel):
    report: Dict[str, Any]


# ─── 辅助函数 ────────────────────────────────
def _render_visualization(mermaid: str, fmt: str, title: str) -> Any:
    """根据format渲染可视化结果"""
    from m_layer.visualizer import get_visualizer
    viz = get_visualizer()
    if fmt == "html":
        return HTMLResponse(viz.to_html(mermaid, title))
    elif fmt == "markdown":
        return JSONResponse({"success": True, "markdown": viz.to_markdown(mermaid, title)})
    else:  # mermaid
        return JSONResponse({"success": True, "mermaid": mermaid})


# ─── 临时资源管理 ────────────────────────────────
@router.get("/temp/resources")
async def temp_resources(task_id: str = "", api_key: str = Depends(verify_api_key)):
    """列出临时资源"""
    from w1_layer.temp_manager import get_temp_manager
    return JSONResponse(get_temp_manager().list_temp_resources(task_id or None))


@router.post("/temp/cleanup")
async def temp_cleanup(req: dict, api_key: str = Depends(verify_api_key)):
    """清理临时资源（task_id或全部）"""
    from w1_layer.temp_manager import get_temp_manager
    tm = get_temp_manager()
    task_id = req.get("task_id", "")
    force = req.get("force", False)
    if task_id:
        result = tm.cleanup(task_id, force=force)
    else:
        result = tm.cleanup_all(force=force)
    return JSONResponse(result)


@router.get("/temp/history")
async def temp_history(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """清理历史"""
    from w1_layer.temp_manager import get_temp_manager
    return JSONResponse({"history": get_temp_manager().get_history(limit)})


# ─── 任务持久化 ────────────────────────────────
@router.post("/task/checkpoint")
async def task_checkpoint_save(req: CheckpointRequest,
                                api_key: str = Depends(verify_api_key)):
    """保存任务检查点"""
    try:
        from m_layer.task_persistence import get_task_persistence
        ok = get_task_persistence().save_checkpoint(
            req.task_id, req.plan, req.current_step, req.step_context, req.status)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/task/checkpoint/{task_id}")
async def task_checkpoint_load(task_id: str, api_key: str = Depends(verify_api_key)):
    """加载任务检查点"""
    try:
        from m_layer.task_persistence import get_task_persistence
        cp = get_task_persistence().load_checkpoint(task_id)
        if cp is None:
            return JSONResponse({"success": False, "error": "检查点不存在"})
        return JSONResponse({"success": True, "checkpoint": cp})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.delete("/task/checkpoint/{task_id}")
async def task_checkpoint_delete(task_id: str, api_key: str = Depends(verify_api_key)):
    """删除任务检查点"""
    try:
        from m_layer.task_persistence import get_task_persistence
        ok = get_task_persistence().delete_checkpoint(task_id)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/task/checkpoints")
async def task_checkpoints_list(api_key: str = Depends(verify_api_key)):
    """列出所有可恢复的检查点"""
    try:
        from m_layer.task_persistence import get_task_persistence
        cps = get_task_persistence().list_resumable()
        return JSONResponse({"success": True, "checkpoints": cps})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 可视化 ────────────────────────────────
@router.post("/visualize/plan")
async def visualize_plan(req: VisualizePlanRequest, format: str = "mermaid",
                          api_key: str = Depends(verify_api_key)):
    """生成执行计划的Mermaid流程图"""
    try:
        from m_layer.visualizer import get_visualizer
        mermaid = get_visualizer().plan_to_mermaid(req.plan)
        return _render_visualization(mermaid, format, "执行计划流程图")
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/visualize/report")
async def visualize_report(req: VisualizeReportRequest, format: str = "mermaid",
                            api_key: str = Depends(verify_api_key)):
    """生成执行报告的状态图"""
    try:
        from m_layer.visualizer import get_visualizer
        mermaid = get_visualizer().report_to_mermaid(req.report)
        return _render_visualization(mermaid, format, "执行报告状态图")
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/visualize/multiagent")
async def visualize_multiagent(req: VisualizeMultiAgentRequest, format: str = "mermaid",
                                api_key: str = Depends(verify_api_key)):
    """生成多Agent协作图"""
    try:
        from m_layer.visualizer import get_visualizer
        mermaid = get_visualizer().multi_agent_to_mermaid(req.report)
        return _render_visualization(mermaid, format, "多Agent协作图")
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
