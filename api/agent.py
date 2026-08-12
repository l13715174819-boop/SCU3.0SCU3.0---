# -*- coding: utf-8 -*-
"""api/agent.py — Agent 编排路由

从 server.py 抽取的 Agent 编排相关路由：
  POST /agent/run                    — 完整 Agent 执行
  POST /agent/plan                   — 仅生成执行计划
  POST /agent/execute                — 执行已有计划
  GET  /agent/presets                — 列出预置 Agent 工作流
  GET  /agent/presets/{preset_id}    — 获取预置工作流详情
  POST /agent/presets/{preset_id}/run — 一键执行预置工作流
  GET  /agent/history                — Agent 执行历史
  GET  /agent/status                 — Agent 执行器状态
  POST /agent/learn                  — 触发 Agent 学习（管理员）
  GET  /agent/experience             — 查询类似任务的执行经验
  POST /agent/checkpoints            — 保存 Agent 任务检查点
  GET  /agent/checkpoints            — 列出所有 Agent 检查点
  POST /parallel/execute             — 并行执行计划
  POST /parallel/analyze             — 分析步骤依赖关系
  POST /branch/evaluate              — 评估条件并选择分支
  POST /multiagent/execute           — 多 Agent 协作执行
  POST /multiagent/thread            — 多 Agent 线程模式
  POST /multiagent/process           — 多 Agent 进程隔离模式
  POST /multiagent/mixed             — 多 Agent 混合模式
  GET  /multiagent/modes             — 获取多 Agent 模式说明
  GET  /agent/graph                  — Agent 架构图
  GET  /agent/parallel-steps         — 并行步骤状态
  GET  /agent/branches               — Agent 分支节点
"""
import time
import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key, get

logger = logging.getLogger("SCU3.api.agent")

router = APIRouter(tags=["agent"])


# ─── 请求模型 ────────────────────────────────
class AgentRunRequest(BaseModel):
    goal: str
    cleanup: bool = True
    reflect: bool = True


class AgentExecuteRequest(BaseModel):
    plan: dict
    task_id: str = ""


class AgentCheckpointRequest(BaseModel):
    task: str
    plan: Dict[str, Any] = {}
    current_step: int = 0
    step_context: Dict[str, Any] = {}


class ParallelExecuteRequest(BaseModel):
    plan: Dict[str, Any]
    task_id: str = ""


# ─── Agent 执行 ────────────────────────────────
@router.post("/agent/run")
async def agent_run(req: AgentRunRequest, api_key: str = Depends(verify_api_key)):
    """完整Agent执行：目标→拆解→执行→反思→清理（经 CUF 守卫审批）"""
    from m_layer.task_executor import get_executor
    from guard.workflow_guard import run_with_cuf_audit

    guard = get("guard")
    tool_guard = get("tool_guard")
    executor = get_executor()
    op_id = f"agent_run_{int(time.time())}"
    # Agent.Run 内部由 LLM 拆解，subtasks 在执行前不可预知
    # 此处用通用 subtask 占位（general），工具守卫按 read 审计
    placeholder_subtasks = [{"subtask": req.goal, "specialty": "general"}]

    def _execute():
        return executor.run(req.goal, cleanup=req.cleanup, reflect=req.reflect)

    result = run_with_cuf_audit(
        guard=guard, tool_guard=tool_guard,
        op_id=op_id, goal=req.goal,
        subtasks=placeholder_subtasks, execute_fn=_execute,
    )
    return JSONResponse(result)


@router.post("/agent/plan")
async def agent_plan(req: AgentRunRequest, api_key: str = Depends(verify_api_key)):
    """仅生成执行计划（不执行，但仍需守卫①②成对审计，符合 CUF 闭环约束）"""
    from m_layer.task_executor import get_executor
    from guard.workflow_guard import audit_workflow_entry, audit_workflow_exit

    guard = get("guard")
    executor = get_executor()
    op_id = f"agent_plan_{int(time.time())}"
    cuf_traces = []

    # 守卫①：W2→M 入口审计
    ok1, msg1, d1 = audit_workflow_entry(guard, op_id, req.goal)
    cuf_traces.append({"guard": "W2→M", "passed": ok1, "msg": msg1, "tax": d1.get("tax", 0)})
    if not ok1:
        return JSONResponse({"success": False, "error": f"CUF守卫①拦截: {msg1}",
                             "cuf_blocked": True, "cuf_traces": cuf_traces})

    plan = executor.create_plan(req.goal)

    # 守卫②：M→W2 出口审计（计划结果返回 API 层前必须审计）
    plan_summary = ""
    if isinstance(plan, dict):
        plan_summary = plan.get("goal", "") or str(plan.get("steps", ""))[:200]
    ok2, msg2, d2 = audit_workflow_exit(guard, op_id, plan_summary)
    cuf_traces.append({"guard": "M→W2", "passed": ok2, "msg": msg2, "tax": d2.get("tax", 0)})
    if not ok2:
        return JSONResponse({"success": False, "error": f"CUF守卫②拦截: {msg2}",
                             "cuf_blocked": True, "cuf_traces": cuf_traces})

    if isinstance(plan, dict):
        plan["cuf_audited"] = True
        plan["cuf_entry_tax"] = d1.get("tax", 0)
        plan["cuf_exit_tax"] = d2.get("tax", 0)
        plan["cuf_traces"] = cuf_traces
    return JSONResponse(plan)


@router.post("/agent/execute")
async def agent_execute(req: AgentExecuteRequest, api_key: str = Depends(verify_api_key)):
    """执行已有计划（经 CUF 守卫审批）"""
    from m_layer.task_executor import get_executor
    from guard.workflow_guard import run_with_cuf_audit

    guard = get("guard")
    tool_guard = get("tool_guard")
    executor = get_executor()
    op_id = f"agent_exec_{int(time.time())}"
    # 从 plan 中提取步骤作为 subtasks 用于工具守卫审计
    plan_steps = req.plan.get("steps", []) if isinstance(req.plan, dict) else []
    subtasks = []
    for s in plan_steps:
        subtasks.append({
            "subtask": s.get("name", s.get("task", "")),
            "specialty": "general",
        })
    if not subtasks:
        subtasks = [{"subtask": "execute_plan", "specialty": "general"}]

    def _execute():
        return executor.execute_plan(req.plan, task_id=req.task_id or None)

    result = run_with_cuf_audit(
        guard=guard, tool_guard=tool_guard,
        op_id=op_id, goal=req.plan.get("goal", "execute_plan") if isinstance(req.plan, dict) else "execute_plan",
        subtasks=subtasks, execute_fn=_execute,
    )
    return JSONResponse(result)


# ─── 预置 Agent 工作流 ────────────────────────────────
@router.get("/agent/presets")
async def agent_presets_list(api_key: str = Depends(verify_api_key)):
    """列出预置 Agent 工作流（基于 TRAE AI 工作模式）"""
    from m_layer.agent_presets import list_presets
    return JSONResponse({"success": True, "presets": list_presets()})


@router.get("/agent/presets/{preset_id}")
async def agent_preset_detail(preset_id: str, api_key: str = Depends(verify_api_key)):
    """获取预置工作流详情"""
    from m_layer.agent_presets import get_preset
    p = get_preset(preset_id)
    if not p:
        return JSONResponse({"success": False, "error": "预置工作流不存在"}, status_code=404)
    return JSONResponse({"success": True, "preset": p})


@router.post("/agent/presets/{preset_id}/run")
async def agent_preset_run(preset_id: str, req: dict, api_key: str = Depends(verify_api_key)):
    """一键执行预置工作流（经 CUF 守卫审批）

    body: {"topic": "用户输入的主题"}
    内部自动构建 subtasks 并调用 multiagent/execute

    审批链路：守卫①(W2→M) → 工具守卫(每子任务) → 执行 → 守卫②(M→W2)
    """
    from m_layer.agent_presets import build_request
    from m_layer.multi_agent import quick_multi_agent, quick_mixed_agents
    from guard.workflow_guard import run_with_cuf_audit

    guard = get("guard")
    tool_guard = get("tool_guard")
    topic = (req.get("topic") or "").strip()
    if not topic:
        return JSONResponse({"success": False, "error": "请输入主题"})

    request_body = build_request(preset_id, topic)
    if not request_body:
        return JSONResponse({"success": False, "error": "预置工作流不存在"}, status_code=404)

    subtasks = request_body["subtasks"]
    has_isolation = any("isolation" in st for st in subtasks)
    op_id = f"preset_{preset_id}_{int(time.time())}"

    def _execute():
        if has_isolation:
            result = quick_mixed_agents(subtasks)
        else:
            result = quick_multi_agent(subtasks, mode=request_body["mode"])
        if isinstance(result, dict):
            result["preset_id"] = preset_id
            result["preset_name"] = request_body["goal"]
        return result

    result = run_with_cuf_audit(
        guard=guard, tool_guard=tool_guard,
        op_id=op_id, goal=request_body["goal"],
        subtasks=subtasks, execute_fn=_execute,
    )
    return JSONResponse(result)


@router.get("/agent/history")
async def agent_history(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """Agent执行历史"""
    from m_layer.task_executor import get_executor
    return JSONResponse({"history": get_executor().get_history(limit)})


@router.get("/agent/status")
async def agent_status(api_key: str = Depends(verify_api_key)):
    """Agent执行器状态"""
    from m_layer.task_executor import get_executor
    return JSONResponse(get_executor().get_status())


@router.post("/agent/learn")
async def agent_learn(api_key: str = Depends(verify_admin_key)):
    """触发Agent学习（从历史中积累经验）"""
    from m_layer.task_executor import get_executor
    from m_layer.agent_learning import get_agent_learning
    history = get_executor().get_history(100)
    report = get_agent_learning().learn_from_history(history)
    return JSONResponse(report)


@router.get("/agent/experience")
async def agent_experience(goal: str = "", api_key: str = Depends(verify_api_key)):
    """查询类似任务的执行经验"""
    from m_layer.agent_learning import get_agent_learning
    exp = get_agent_learning().query_experience(goal)
    return JSONResponse(exp)


# ─── Agent 检查点（/task/checkpoint 的前端友好别名） ────────────────────────────────
@router.post("/agent/checkpoints")
async def agent_checkpoints_save(req: AgentCheckpointRequest,
                                  api_key: str = Depends(verify_api_key)):
    """保存Agent任务检查点（/task/checkpoint 的前端友好别名）"""
    try:
        from m_layer.task_persistence import get_task_persistence
        ok = get_task_persistence().save_checkpoint(
            req.task, req.plan, req.current_step, req.step_context, "running")
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/agent/checkpoints")
async def agent_checkpoints_list(api_key: str = Depends(verify_api_key)):
    """列出所有Agent检查点"""
    try:
        from m_layer.task_persistence import get_task_persistence
        cps = get_task_persistence().list_resumable()
        return JSONResponse({"success": True, "checkpoints": cps})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 并行执行 ────────────────────────────────
@router.post("/parallel/execute")
async def parallel_execute(req: ParallelExecuteRequest,
                            api_key: str = Depends(verify_api_key)):
    """并行执行计划（无依赖步骤并行，经 CUF 守卫审批）"""
    from guard.workflow_guard import run_with_cuf_audit

    guard = get("guard")
    tool_guard = get("tool_guard")
    op_id = f"parallel_{int(time.time())}"
    plan_steps = req.plan.get("steps", []) if isinstance(req.plan, dict) else []
    subtasks = [{"subtask": s.get("name", ""), "specialty": "general"} for s in plan_steps]
    if not subtasks:
        subtasks = [{"subtask": "parallel_execute", "specialty": "general"}]

    def _execute():
        from m_layer.parallel_executor import get_parallel_executor
        return get_parallel_executor().execute_parallel(req.plan, req.task_id)

    result = run_with_cuf_audit(
        guard=guard, tool_guard=tool_guard,
        op_id=op_id, goal=req.plan.get("goal", "parallel_execute") if isinstance(req.plan, dict) else "parallel_execute",
        subtasks=subtasks, execute_fn=_execute,
    )
    return JSONResponse(result)


@router.post("/parallel/analyze")
async def parallel_analyze(req: ParallelExecuteRequest,
                            api_key: str = Depends(verify_api_key)):
    """分析步骤依赖关系，构建DAG"""
    try:
        from m_layer.parallel_executor import get_parallel_executor
        steps = req.plan.get("steps", [])
        dep_graph = get_parallel_executor()._build_dependency_graph(steps)
        return JSONResponse({"success": True, "dependency_graph": dep_graph,
                             "steps_count": len(steps)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 条件分支 ────────────────────────────────
@router.post("/branch/evaluate")
async def branch_evaluate(req: dict, api_key: str = Depends(verify_api_key)):
    """评估条件并选择分支

    body: {"conditions": [{name, left, op, right}], "branches": [{condition, expected, step}], "context": {...}}
    """
    try:
        from m_layer.condition_branch import get_condition_branch
        cb = get_condition_branch()
        for cond in req.get("conditions", []):
            cb.add_condition(cond["name"], cond["left"], cond["op"], cond["right"])
        for br in req.get("branches", []):
            cb.add_branch(br["condition"], br["expected"], br["step"])
        result = cb.evaluate(req.get("context", {}))
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 多 Agent 协作 ────────────────────────────────
@router.post("/multiagent/execute")
async def multiagent_execute(req: dict, api_key: str = Depends(verify_api_key)):
    """多Agent协作执行（双模式：线程级 / 进程级隔离，经 CUF 守卫审批）

    body:
    {
        "mode": "thread"|"process",  // 可选，默认 thread
        "subtasks": [
            {
                "subtask": "任务描述",
                "specialty": "search|analysis|writing|coding|general",  // 可选
                "depends_on": ["其他子任务ID"],  // 可选，依赖关系
                "isolation": "thread|process"   // 可选，任务级覆盖
            }
        ]
    }

    审批链路：守卫①(W2→M) → 工具守卫(每子任务) → 执行 → 守卫②(M→W2)
    """
    from m_layer.multi_agent import quick_multi_agent, quick_mixed_agents
    from guard.workflow_guard import run_with_cuf_audit

    guard = get("guard")
    tool_guard = get("tool_guard")
    mode = req.get("mode", "thread")
    subtasks = req.get("subtasks", [])
    if not subtasks:
        return JSONResponse({"success": False, "error": "subtasks 不能为空"})

    has_isolation_override = any("isolation" in st for st in subtasks)
    op_id = f"multiagent_{int(time.time())}"
    goal = req.get("goal", f"多Agent协作({len(subtasks)}个子任务)")

    def _execute():
        if has_isolation_override:
            return quick_mixed_agents(subtasks)
        else:
            return quick_multi_agent(subtasks, mode=mode)

    result = run_with_cuf_audit(
        guard=guard, tool_guard=tool_guard,
        op_id=op_id, goal=goal,
        subtasks=subtasks, execute_fn=_execute,
    )
    return JSONResponse(result)


@router.post("/multiagent/thread")
async def multiagent_thread(req: dict, api_key: str = Depends(verify_api_key)):
    """多Agent协作 - 线程模式专用端点

    body: {"subtasks": [{subtask, specialty?, depends_on?}]}
    """
    try:
        from m_layer.multi_agent import quick_thread_agents
        result = quick_thread_agents(req.get("subtasks", []))
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/multiagent/process")
async def multiagent_process(req: dict, api_key: str = Depends(verify_api_key)):
    """多Agent协作 - 进程隔离模式专用端点

    body: {"subtasks": [{subtask, specialty?, depends_on?}]}

    每个子代理在独立子进程中运行，拥有独立 LLM 客户端和上下文窗口。
    适合深度探索型、长链路推理任务。
    注意：子任务参数必须可 pickle 序列化。
    """
    try:
        from m_layer.multi_agent import quick_process_agents
        result = quick_process_agents(req.get("subtasks", []))
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/multiagent/mixed")
async def multiagent_mixed(req: dict, api_key: str = Depends(verify_api_key)):
    """多Agent协作 - 混合模式专用端点

    body: {"subtasks": [{subtask, specialty?, depends_on?, isolation: "thread"|"process"}]}

    根据每个任务的 isolation 字段决定使用线程还是进程:
      - 轻量任务（搜索、计算）→ isolation="thread"
      - 重量任务（深度分析、长链路推理）→ isolation="process"
    """
    try:
        from m_layer.multi_agent import quick_mixed_agents
        result = quick_mixed_agents(req.get("subtasks", []))
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/multiagent/modes")
async def multiagent_modes(api_key: str = Depends(verify_api_key)):
    """获取多Agent模式说明"""
    return JSONResponse({
        "modes": {
            "thread": {
                "description": "线程级并行（轻量、共享上下文）",
                "max_workers": 4,
                "overhead": "<1ms",
                "use_case": "工具调用密集型、共享状态的细粒度任务",
                "endpoint": "/multiagent/thread",
            },
            "process": {
                "description": "进程级隔离（独立上下文、深度探索）",
                "max_workers": 2,
                "overhead": "~100ms",
                "use_case": "深度探索型、长链路推理任务",
                "endpoint": "/multiagent/process",
            },
            "mixed": {
                "description": "混合模式（按任务指定隔离级别）",
                "max_workers": 6,
                "overhead": "按任务",
                "use_case": "复杂工作流，部分轻量+部分重量任务",
                "endpoint": "/multiagent/mixed",
            }
        },
        "default": "thread",
        "unified_endpoint": "/multiagent/execute",
        "note": "通过 /multiagent/execute 的 mode 参数或任务级 isolation 字段切换"
    })


# ─── Agent 可视化数据 ────────────────────────────────
@router.get("/agent/graph")
async def agent_graph(api_key: str = Depends(verify_api_key)):
    """Agent 架构图：基于真实四层架构 + CUF守卫 + 多Agent协作生成 Mermaid 图"""
    try:
        from m_layer.task_executor import get_executor
        executor = get_executor()
        status = executor.get_status()

        # 真实架构：W2感知→W1记忆/工具→M任务编排→D公理
        # 含 CUF 守卫①(W2→M)、工具守卫(W1内)、守卫②(M→W2)
        mermaid = (
            "graph TD\n"
            "    U([用户输入]:::user)\n"
            "    W2[W2 感知层<br/>意图识别 + 工作流路由]:::w2\n"
            "    G1{{守卫① W2→M<br/>CUF审计+熵税}}:::guard\n"
            "    M[M 任务编排层<br/>多Agent协作 + 计划拆解]:::m\n"
            "    W1[W1 记忆/工具层<br/>15核心+16扩展工具]:::w1\n"
            "    G3{{工具守卫<br/>每子任务审计}}:::guard\n"
            "    D[(D 公理层<br/>账本+防火墙+不变量)]:::d\n"
            "    G2{{守卫② M→W2<br/>出口审计+熵税}}:::guard\n"
            "    OUT([输出结果<br/>含审计轨迹]):::user\n"
            f"    U --> W2\n"
            f"    W2 -->|跨层| G1\n"
            f"    G1 -->|通过| M\n"
            f"    M -->|工具调用| W1\n"
            f"    W1 -->|工具守卫| G3\n"
            f"    G3 -->|审计通过| W1\n"
            f"    W1 -->|结果回流| M\n"
            f"    M -->|状态查询| D\n"
            f"    M -->|跨层| G2\n"
            f"    G2 -->|通过| OUT\n"
            f"    classDef user fill:#f9f,stroke:#333\n"
            f"    classDef w2 fill:#e1f5ff,stroke:#0288d1\n"
            f"    classDef w1 fill:#e8f5e9,stroke:#388e3c\n"
            f"    classDef m fill:#fff3e0,stroke:#f57c00\n"
            f"    classDef d fill:#fce4ec,stroke:#c2185b\n"
            f"    classDef guard fill:#fff9c4,stroke:#fbc02d,stroke-dasharray: 5 5\n"
        )
        return JSONResponse({"success": True, "data": {
            "mermaid": mermaid,
            "layers": ["W2", "W1", "M", "D"],
            "guards": ["g1_W2_M", "g3_tool", "g2_M_W2"],
            "executor_status": status,
        }})
    except Exception as e:
        logger.error(f"Agent 图查询失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/agent/parallel-steps")
async def agent_parallel_steps(api_key: str = Depends(verify_api_key)):
    """并行步骤：从多Agent协调器取当前子任务状态"""
    try:
        from m_layer.multi_agent import get_multi_agent_coordinator, _coord_instances
        steps = []
        # 汇总所有模式的协调器子任务
        for mode, coord in _coord_instances.items():
            if coord is None:
                continue
            for st in getattr(coord, "_subtasks", []):
                steps.append({
                    "step_id": st.get("subtask_id", ""),
                    "subtask": st.get("subtask", ""),
                    "specialty": st.get("specialty", "general"),
                    "isolation": st.get("isolation", mode),
                    "depends_on": st.get("depends_on", []),
                    "status": st.get("status", "pending"),
                    "mode": mode,
                })
        return JSONResponse({"success": True, "data": {
            "steps": steps,
            "total": len(steps),
            "modes": list(_coord_instances.keys()),
        }})
    except Exception as e:
        logger.error(f"并行步骤查询失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/agent/branches")
async def agent_branches(api_key: str = Depends(verify_api_key)):
    """Agent 分支：从执行历史取最近任务作为分支节点"""
    try:
        from m_layer.task_executor import get_executor
        history = get_executor().get_history(limit=10)
        branches = [{
            "task_id": h.get("task_id", ""),
            "goal": h.get("goal", "")[:80],
            "success": h.get("success", False),
            "steps_total": h.get("steps_total", 0),
            "steps_done": h.get("steps_done", 0),
            "elapsed_ms": h.get("elapsed_ms", 0),
            "time": h.get("time", ""),
        } for h in history]
        return JSONResponse({"success": True, "data": {
            "branches": branches,
            "total": len(branches),
        }})
    except Exception as e:
        logger.error(f"Agent 分支查询失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})
