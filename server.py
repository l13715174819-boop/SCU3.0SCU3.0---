# -*- coding: utf-8 -*-
"""
SCU3 - 标准计算单元3 · 主入口
===============================
基于 v3 架构：三维度分离
  数据流：感知(W2) → 记忆(W1) → 执行(W1) → 认知(M) → 元认知(M) → 输出
  守卫点：① W2→W1 跨层  ② W1→M 跨层  ③ 工具守卫  ④ 周期审计  ⑤ 内容过滤
"""
import os
import sys
import time
import uuid
import logging
import secrets
import asyncio
import urllib.parse
from datetime import datetime
from typing import Dict, Any, Optional, List

# 确保包可导入
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from fastapi import FastAPI, Request, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

from d_layer.axioms import Operation
from w1_layer.ledger_runtime import LedgerRuntime
from w1_layer.memory import MemoryLayer
from w1_layer.action import ActionLayer
from w2_layer.perception import PerceptionLayer
from m_layer.cognition import CognitionLayer
from m_layer.metacognition import MetacognitionLayer
from guard.firewall import CUFGuard
from guard.whitelist import WhitelistManager
from guard.tool_guard import ToolGuard
from guard.content_filter import ContentFilter
from feedback.collector import FeedbackCollector
from m_layer.self_learning import init_engine as init_learning_engine
from m_layer.code_self_modify import init_modifier as init_code_modifier
from w1_layer.knowledge_store import get_store as get_knowledge_store

# ─── 日志 ────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("SCU3.main")

# ─── 初始化组件 ────────────────────────────────
DATA_DIR = os.path.join(BASE_DIR, "SCU3_data")
os.makedirs(DATA_DIR, exist_ok=True)

# W1 层运行时状态
ledger = LedgerRuntime(store_path=os.path.join(DATA_DIR, "ledger.json"))
whitelist = WhitelistManager(store_path=os.path.join(DATA_DIR, "whitelist.json"))

# 守卫横切层
guard = CUFGuard(ledger=ledger, whitelist=whitelist)
tool_guard = ToolGuard(ledger=ledger)
content_filter = ContentFilter()

# 反馈系统
feedback = FeedbackCollector(ledger=ledger)

# 业务流水线
perception = PerceptionLayer()
memory = MemoryLayer()
action = ActionLayer()
cognition = CognitionLayer()
metacog = MetacognitionLayer(ledger=ledger, guard=guard, whitelist=whitelist)

# 阶段2：自学习引擎（注入依赖并挂载到元认知层）
knowledge_store = get_knowledge_store()
learning_engine = init_learning_engine(
    ledger=ledger,
    knowledge_store=knowledge_store,
    feedback_collector=feedback,
    content_filter=content_filter,
    data_dir=DATA_DIR,
)
metacog.attach_learning_engine(learning_engine)

# 阶段3：代码自修改引擎（默认需人工审批）
code_modifier = init_code_modifier(
    project_root=BASE_DIR,
    backup_dir=os.path.join(DATA_DIR, "backups"),
    ledger=ledger,
    require_human_approval=True,
)

app = FastAPI(title="标准计算单元3 SCU3", version="3.0.0")

# 挂载静态文件目录（exports/images 等生成的文件可通过 /exports/ 访问）
from fastapi.staticfiles import StaticFiles
_exports_dir = os.path.join(BASE_DIR, "exports")
os.makedirs(_exports_dir, exist_ok=True)
app.mount("/exports", StaticFiles(directory=_exports_dir), name="exports")

# ─── APIRouter 域路由装配 ────────────────────────────────
# 从 api/*.py 引入域路由，server.py 只做装配
# 依赖注入：通过 api.deps.set_globals() 在 startup 时注入全局单例
from api.deps import set_globals as _api_set_globals
from api.ledger import router as _ledger_router
from api.system import router as _system_router
from api.mcp import router as _mcp_router
from api.modules import router as _modules_router
from api.memory import router as _memory_router
from api.distributed import router as _distributed_router
from api.plugins import router as _plugins_router
from api.chat import router as _chat_router
from api.llm import router as _llm_router
from api.learning import router as _learning_router
from api.self_modify import router as _self_modify_router
from api.agent import router as _agent_router
from api.tools import router as _tools_router
from api.task import router as _task_router
from api.conversation import router as _conversation_router
from api.permissions import router as _permissions_router
from api.multimodal import router as _multimodal_router
from api.voice import router as _voice_router
from api.vision import router as _vision_router
from api.knowledge import router as _knowledge_router
from api.local_model import router as _local_model_router
from api.browser import router as _browser_router
from api.integrations import router as _integrations_router
from api.misc import router as _misc_router
app.include_router(_ledger_router)
app.include_router(_system_router)
app.include_router(_mcp_router)
app.include_router(_modules_router)
app.include_router(_memory_router)
app.include_router(_distributed_router)
app.include_router(_plugins_router)
app.include_router(_chat_router)
app.include_router(_llm_router)
app.include_router(_learning_router)
app.include_router(_self_modify_router)
app.include_router(_agent_router)
app.include_router(_tools_router)
app.include_router(_task_router)
app.include_router(_conversation_router)
app.include_router(_permissions_router)
app.include_router(_multimodal_router)
app.include_router(_voice_router)
app.include_router(_vision_router)
app.include_router(_knowledge_router)
app.include_router(_local_model_router)
app.include_router(_browser_router)
app.include_router(_integrations_router)
app.include_router(_misc_router)


# ─── C4修复：API Key 认证中间件 ────────────────────────────────
# 安全策略：必须通过环境变量配置，未配置则使用开发模式默认Key并告警
# 生产环境务必设置 SCU3_API_KEY 和 SCU3_ADMIN_API_KEY 环境变量
API_KEY_ENV = "SCU3_API_KEY"
# 开发模式默认Key（仅当未配置环境变量时使用，启动时会输出显著告警）
_DEV_DEFAULT_API_KEY = "SCU3_dev_key_2026"
ADMIN_API_KEY_ENV = "SCU3_ADMIN_API_KEY"
_DEV_DEFAULT_ADMIN_KEY = "SCU3_admin_key_2026"

# 标记是否处于开发模式（使用默认Key）
_USING_DEV_API_KEY = False
_USING_DEV_ADMIN_KEY = False

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# 敏感端点列表（需要管理员Key）
ADMIN_ENDPOINTS = {
    "/whitelist/add", "/whitelist/list",
    "/audit/daily", "/status", "/history",
    "/knowledge/import", "/knowledge/delete",
}


def _get_configured_api_key() -> str:
    """获取配置的API Key（未配置环境变量时使用开发默认Key并标记告警）"""
    global _USING_DEV_API_KEY
    val = os.getenv(API_KEY_ENV)
    if val:
        _USING_DEV_API_KEY = False
        return val
    _USING_DEV_API_KEY = True
    return _DEV_DEFAULT_API_KEY


def _get_configured_admin_key() -> str:
    """获取配置的管理员Key"""
    global _USING_DEV_ADMIN_KEY
    val = os.getenv(ADMIN_API_KEY_ENV)
    if val:
        _USING_DEV_ADMIN_KEY = False
        return val
    _USING_DEV_ADMIN_KEY = True
    return _DEV_DEFAULT_ADMIN_KEY


def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """C4修复：API Key认证（secrets.compare_digest防时序攻击）"""
    expected = _get_configured_api_key()
    admin_expected = _get_configured_admin_key()
    # 使用 secrets.compare_digest 防时序攻击
    if api_key and (secrets.compare_digest(api_key, expected) or
                    secrets.compare_digest(api_key, admin_expected)):
        return api_key
    raise HTTPException(status_code=401, detail="无效的API Key")


def verify_admin_key(api_key: str = Security(api_key_header)) -> str:
    """C4修复：管理员Key认证（敏感端点）"""
    admin_expected = _get_configured_admin_key()
    if api_key and secrets.compare_digest(api_key, admin_expected):
        return api_key
    raise HTTPException(status_code=403, detail="需要管理员权限")


def _is_admin(api_key: str) -> bool:
    """非装饰器场景下的管理员判定（不抛异常，仅返回布尔）"""
    if not api_key:
        return False
    admin_expected = _get_configured_admin_key()
    return secrets.compare_digest(api_key, admin_expected)


def require_module(module_name: str):
    """模块可用性检查（可插拔性核心）

    检查模块是否在注册表中且已加载。
    若模块未注册或已卸载/禁用，抛出 503 异常。

    Args:
        module_name: 注册表中的模块名（如 "automation.browser"）

    Raises:
        HTTPException(503): 模块不可用时
    """
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        if not registry.is_available(module_name):
            m = registry._modules.get(module_name)
            if m is None:
                detail = f"模块未注册: {module_name}"
            elif m.disabled:
                detail = f"模块已禁用: {module_name}（请先 enable）"
            else:
                detail = f"模块未加载: {module_name}（请先 POST /modules/{module_name}/load）"
            raise HTTPException(status_code=503, detail=detail)
    except HTTPException:
        raise
    except Exception as e:
        # 注册表本身不可用时降级放行（不阻塞业务）
        logger.debug(f"模块检查异常（降级放行）: {module_name}: {e}")


# ─── 请求模型 ────────────────────────────────────
class ChatRequest(BaseModel):
    prompt: str
    user_id: str = "default_user"


class FeedbackRequest(BaseModel):
    kind: str
    pattern_key: str
    user_id: str = "default_user"


class WhitelistRequest(BaseModel):
    action: str
    source: str
    target: str
    contracts: Dict[str, Any]
    code_hash: str = ""
    ttl_hours: float = 24.0


# 方案C：缓存最近一次阴阳对子思考状态（供前端太极图展示）
_last_yin_yang_state: Dict[str, Any] = {
    "active": False,
    "gamma_yin": 0.0,
    "gamma_yang": 0.0,
    "endorsed": False,
    "timestamp": None,
    "yin_api": "DeepSeek-Chat",
    "yang_api": "Qwen-Plus",
}


# ─── 核心流程 ────────────────────────────────────
def process_request(prompt: str, user_id: str = "default_user") -> Dict[str, Any]:
    """完整请求处理：用户输入 → 守卫 → 流水线 → 汇合 → 过滤 → 输出

    插件钩子接入点：
      ① on_message  — 用户输入后、感知层处理前
      ② on_tool_call — 工具调用前（可拦截/修改参数）
      ③ on_response — 响应生成后、内容过滤后
    """
    # 方案C：缓存最近一次阴阳对子思考状态（供 /cognition/yin-yang 端点查询）
    global _last_yin_yang_state
    op_id = f"op_{uuid.uuid4().hex[:8]}"
    start = datetime.now()
    cuf_traces = []
    plugin_traces = []

    # 初始化插件管理器（在顶部初始化，避免后续try块依赖作用域）
    pm = None
    try:
        from m_layer.plugin_system import get_plugin_manager
        pm = get_plugin_manager()
    except Exception as e:
        logger.debug(f"插件管理器初始化失败: {e}")

    # 插件钩子①：on_message（用户消息进入）
    try:
        if pm is None:
            raise RuntimeError("插件管理器未初始化")
        msg_results = pm.trigger_hook("on_message", {"text": prompt, "user_id": user_id})
        if msg_results:
            plugin_traces.append({"hook": "on_message", "results": msg_results})
            # 允许插件修改消息（如 SafetyPlugin 拦截敏感词）
            for r in msg_results:
                if r.get("success") and isinstance(r.get("result"), dict):
                    if r["result"].get("blocked"):
                        merged = metacog.merge({"response": r["result"].get("message", "消息被插件拦截"),
                                               "blocked": True}, cuf_traces, op_id)
                        merged["plugin_traces"] = plugin_traces
                        return _build_response(merged, op_id, start)
                    if r["result"].get("modified_text"):
                        prompt = r["result"]["modified_text"]
    except Exception as e:
        logger.debug(f"插件钩子 on_message 异常: {e}")

    # 获取最近对话历史（用于 LLM 语义推理区分追问 vs 新话题）
    # 取最近会话的6条消息（约3轮），传给感知层
    recent_history = []
    try:
        from m_layer.conversation_context import get_conversation_manager
        cm = get_conversation_manager()
        sessions = cm.list_sessions(user_id, limit=1)
        if sessions:
            recent_history = cm.get_history_for_llm(sessions[0]["session_id"], limit=6)
    except Exception as e:
        logger.debug(f"获取对话历史失败（不阻塞）: {e}")

    # ① W2 感知层（传入历史，支持 LLM 语义推理区分追问 vs 新话题）
    ctx = perception.process(prompt, {"user_id": user_id}, history=recent_history)

    # 图片生成意图：直接调用图片生成工具，跳过LLM流程
    if ctx.get("intent") == "image_generation":
        try:
            import urllib.request as _urq
            import hashlib as _hl
            os.makedirs(os.path.join(BASE_DIR, "exports", "images"), exist_ok=True)
            img_prompt = prompt
            # 移除命令式前缀（"生成图片："、"画一张"等），提取实际描述
            import re as _re_img
            img_prompt = _re_img.sub(r'^(?:生成|创建|制作|画)\s*(?:一张|一幅|一个|张|幅|个)?\s*(?:图片|图|画|图像)?\s*[：:of]?\s*', '', prompt, count=1, flags=_re_img.I)
            if not img_prompt.strip():
                img_prompt = prompt  # 回退到原始输入

            encoded = urllib.parse.quote(img_prompt, safe="")
            seed = int(time.time()) % 1000000
            img_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1280&height=720&nologo=true&seed={seed}"
            logger.info(f"图片生成（对话内调用）: prompt={img_prompt[:50]}...")

            req_obj = _urq.Request(img_url, headers={"User-Agent": "SCU3/3.0"})
            with _urq.urlopen(req_obj, timeout=60) as resp:
                img_data = resp.read()

            name_hash = _hl.md5(f"{img_prompt}{seed}".encode()).hexdigest()[:8]
            fname = f"gen_{name_hash}.png"
            fpath = os.path.join(BASE_DIR, "exports", "images", fname)
            with open(fpath, "wb") as f:
                f.write(img_data)

            img_path_url = f"/exports/images/{fname}"
            ctx["response"] = f"[IMAGE]{img_path_url}[/IMAGE]\n已根据您的描述生成图片：{img_prompt}\n图片已保存到 exports/images/{fname}"
            ctx["cognition_ok"] = True
            ctx["llm_mode"] = "image_generation"
            ctx["image_generated"] = {"path": img_path_url, "prompt": img_prompt, "bytes": len(img_data)}
            logger.info(f"图片生成成功: {fname}, {len(img_data)} bytes")

            merged = metacog.merge(ctx, cuf_traces, op_id)
            return _build_response(merged, op_id, start)
        except Exception as e:
            logger.error(f"图片生成失败，降级到LLM对话: {e}", exc_info=True)
            ctx["intent"] = "conversation"  # 降级到普通对话

    # 工作流自动触发：感知层识别为 workflow:<preset_id> 时，直接调用预置工作流
    # 复用已修复的 CUF 审批链路（run_with_cuf_audit），不绕过守卫
    intent = ctx.get("intent", "")
    logger.info(f"[工作流路由检查] intent={intent!r}, prompt={prompt[:50]!r}")
    if intent.startswith("workflow:"):
        try:
            from m_layer.agent_presets import build_request
            from m_layer.multi_agent import quick_multi_agent, quick_mixed_agents
            from guard.workflow_guard import run_with_cuf_audit

            preset_id = intent.split(":", 1)[1]
            # 提取主题：去掉触发词前缀，保留核心描述
            topic = _extract_workflow_topic(prompt)
            if not topic:
                topic = prompt  # 回退到原始输入

            request_body = build_request(preset_id, topic)
            if not request_body:
                logger.warning(f"工作流自动触发失败：预置工作流不存在 preset_id={preset_id}")
                ctx["intent"] = "conversation"  # 降级
            else:
                logger.info(f"对话流自动触发工作流: preset={preset_id}, topic={topic[:50]}")
                subtasks = request_body["subtasks"]
                has_isolation = any("isolation" in st for st in subtasks)
                wf_op_id = f"chat_wf_{preset_id}_{int(time.time())}"

                def _execute_workflow():
                    if has_isolation:
                        result = quick_mixed_agents(subtasks)
                    else:
                        result = quick_multi_agent(subtasks, mode=request_body["mode"])
                    if isinstance(result, dict):
                        result["preset_id"] = preset_id
                        result["preset_name"] = request_body["goal"]
                    return result

                wf_result = run_with_cuf_audit(
                    guard=guard, tool_guard=tool_guard,
                    op_id=wf_op_id, goal=request_body["goal"],
                    subtasks=subtasks, execute_fn=_execute_workflow,
                )

                # 构造对话流响应：把工作流结果转为对话框可展示的完整报告
                if isinstance(wf_result, dict) and wf_result.get("success"):
                    # 优先拼接各子任务的完整输出，生成完整报告（用户要求"结果直接展示完整报告"）
                    final_output = _format_workflow_report(wf_result, request_body)
                    ctx["response"] = final_output or "工作流执行完成，但未生成输出内容。"
                    ctx["cognition_ok"] = True
                    ctx["llm_mode"] = f"workflow:{preset_id}"
                    ctx["workflow_result"] = {
                        "preset_id": preset_id,
                        "preset_name": request_body["goal"],
                        "cuf_audited": wf_result.get("cuf_audited", False),
                        "cuf_traces": wf_result.get("cuf_traces", []),
                        "execution_time": wf_result.get("execution_time", 0),
                        "completed": wf_result.get("completed", 0),
                        "failed": wf_result.get("failed", 0),
                    }
                    # 透传 CUF 审计轨迹到顶层（供 SSE meta 帧携带）
                    cuf_traces.extend(wf_result.get("cuf_traces", []))
                else:
                    # 工作流失败或被 CUF 拦截，降级到普通对话
                    err = wf_result.get("error", "未知错误") if isinstance(wf_result, dict) else str(wf_result)
                    logger.warning(f"工作流自动触发失败，降级到对话: {err}")
                    ctx["intent"] = "conversation"
                    ctx["workflow_error"] = err
                    # 若被 CUF 拦截，直接返回拦截信息
                    if isinstance(wf_result, dict) and wf_result.get("cuf_blocked"):
                        ctx["response"] = f"⚠ 工作流被 CUF 守卫拦截：{err}\n\n请稍后重试或调整输入。"
                        ctx["cognition_ok"] = True
                        ctx["llm_mode"] = "workflow_blocked"
                        cuf_traces.extend(wf_result.get("cuf_traces", []))
                        merged = metacog.merge(ctx, cuf_traces, op_id)
                        return _build_response(merged, op_id, start)

                # 工作流成功，直接返回（跳过后续 LLM 流程）
                if ctx.get("response"):
                    merged = metacog.merge(ctx, cuf_traces, op_id)
                    return _build_response(merged, op_id, start)
        except Exception as e:
            logger.error(f"工作流自动触发异常，降级到对话: {e}", exc_info=True)
            ctx["intent"] = "conversation"  # 降级

    # ② 守卫①：W2→W1 跨层审计
    op1 = Operation(
        source="W2", target="W1", action="layer_jump",
        op_id=f"{op_id}_g1", pattern_key="layer_jump:W2>W1",
    )
    ok1, msg1, d1 = guard.check(op1)
    cuf_traces.append({"guard": "W2→W1", "passed": ok1, "msg": msg1,
                        "tax": d1.get("tax", 0), "op_id": f"{op_id}_g1"})
    if not ok1:
        merged = metacog.merge(ctx, cuf_traces, op_id)
        return _build_response(merged, op_id, start)

    # ③ W1 记忆层（同层免审）
    ctx = memory.process(ctx)

    # ④ W1 执行层（同层免审，但工具调用需经工具守卫）
    ctx = action.process(ctx)
    if ctx.get("tool_pending"):
        tool_info = ctx["tool_info"]

        # 插件钩子②：on_tool_call（工具调用前）
        try:
            tool_results = pm.trigger_hook("on_tool_call",
                                           tool_info["tool"],
                                           tool_info.get("params", {}))
            if tool_results:
                plugin_traces.append({"hook": "on_tool_call", "tool": tool_info["tool"], "results": tool_results})
                for r in tool_results:
                    if r.get("success") and isinstance(r.get("result"), dict):
                        if r["result"].get("blocked"):
                            ctx["tool_result"] = {"success": False,
                                                  "error": r["result"].get("message", "工具调用被插件拦截")}
                            ctx["tool_pending"] = False
                            break
        except Exception as e:
            logger.debug(f"插件钩子 on_tool_call 异常: {e}")

        if ctx.get("tool_pending"):
            # 工具守卫审计
            ok_t, msg_t, d_t = tool_guard.check(
                tool_info["tool"], tool_info.get("tool_type", "read"),
                op_id=f"{op_id}_tool"
            )
            cuf_traces.append({"guard": "tool", "passed": ok_t, "msg": msg_t,
                                "tax": d_t.get("tax", 0), "op_id": f"{op_id}_tool"})
            if ok_t:
                ctx["tool_result"] = action.execute(tool_info)
            else:
                ctx["tool_result"] = {"success": False, "error": msg_t}

    # ⑤ 守卫②：W1→M 跨层审计
    op2 = Operation(
        source="W1", target="M", action="layer_jump",
        op_id=f"{op_id}_g2", pattern_key="layer_jump:W1>M",
    )
    ok2, msg2, d2 = guard.check(op2)
    cuf_traces.append({"guard": "W1→M", "passed": ok2, "msg": msg2,
                        "tax": d2.get("tax", 0), "op_id": f"{op_id}_g2"})
    if not ok2:
        merged = metacog.merge(ctx, cuf_traces, op_id)
        return _build_response(merged, op_id, start)

    # ⑥ M 认知层（同层免审）
    ctx = cognition.process(ctx)

    # 方案C：捕获阴阳对子思考状态（供 /cognition/yin-yang 端点查询）
    if ctx.get("yin_yang"):
        from datetime import datetime as _dt
        _last_yin_yang_state = {
            "active": True,
            "gamma_yin": ctx["yin_yang"].get("gamma_yin", 0.0),
            "gamma_yang": ctx["yin_yang"].get("gamma_yang", 0.0),
            "yin_passed": ctx["yin_yang"].get("yin_passed", False),
            "yang_passed": ctx["yin_yang"].get("yang_passed", False),
            "endorsed": ctx["yin_yang"].get("endorsed", False),
            "timestamp": _dt.now().isoformat(),
            "yin_api": "DeepSeek-Chat",
            "yang_api": "Qwen-Plus",
        }

    # ⑦ M 元认知层（汇合 + 补偿）
    merged = metacog.merge(ctx, cuf_traces, op_id)

    # ⑧ 内容过滤（输出脱敏，修复 WARN #4）
    filtered, warnings = content_filter.filter(merged.get("response", ""))
    merged["response"] = filtered
    if warnings:
        merged["filter_warnings"] = warnings

    # 插件钩子③：on_response（响应生成后）
    try:
        resp_results = pm.trigger_hook("on_response", {"text": filtered, "op_id": op_id})
        if resp_results:
            plugin_traces.append({"hook": "on_response", "results": resp_results})
            for r in resp_results:
                if r.get("success") and isinstance(r.get("result"), dict):
                    if r["result"].get("modified_text"):
                        merged["response"] = r["result"]["modified_text"]
    except Exception as e:
        logger.debug(f"插件钩子 on_response 异常: {e}")

    if plugin_traces:
        merged["plugin_traces"] = plugin_traces

    # 存储对话到记忆层（开启上下文联系的关键：recall才能拿到历史）
    try:
        memory.store(prompt, merged.get("response", ""), user_id)
    except Exception as e:
        logger.debug(f"存储对话历史失败: {e}")

    return _build_response(merged, op_id, start)


def _extract_workflow_topic(prompt: str) -> str:
    """从用户输入中提取工作流主题（去掉触发词前缀）

    示例：
      "深度研究 Python异步编程" → "Python异步编程"
      "分析一下人工智能的发展趋势" → "人工智能的发展趋势"
      "完整代码方案 用户登录系统" → "用户登录系统"
      "研究一下" → "" (无主题，调用方回退到原始输入)
    """
    import re as _re
    text = prompt.strip()

    # 强信号词前缀（按长度降序匹配，避免短词先匹配）
    strong_prefixes = [
        r"深度研究(?:报告)?", r"全面调研", r"深度调研", r"专题研究",
        r"研究报告", r"研究一下", r"调研一下", r"深入调研",
        r"完整代码方案", r"代码方案", r"实现方案", r"技术方案",
        r"写个方案", r"给个方案", r"完整方案",
        r"决策分析", r"帮我决策", r"帮我做决定", r"决策一下",
        r"决定一下", r"帮我选择", r"选择分析",
        r"创作一篇", r"写一篇", r"写篇文章", r"创作内容",
        r"写个文案",
        r"排查bug", r"调试问题", r"排查问题", r"bug排查",
        r"调试bug", r"定位bug", r"排查一下",
        r"学习路径", r"学习路线", r"系统学习", r"学习计划",
        r"学习规划", r"怎么学", r"学习指南",
    ]
    # 宽松动词前缀
    loose_prefixes = [
        r"分析一下", r"研究一下", r"调研一下", r"写一下",
        r"了解一下", r"梳理一下", r"梳理下", r"整理一下",
        r"整理下", r"探讨一下", r"讨论一下",
    ]

    all_prefixes = strong_prefixes + loose_prefixes
    for prefix in all_prefixes:
        m = _re.match(rf"^{prefix}[\s，,：:的了吧呢啊哈]*", text, _re.I)
        if m:
            topic = text[m.end():].strip()
            if len(topic) >= 2:
                return topic
            break  # 匹配到前缀但主题太短，退出循环
    return ""


# 工作流子任务 specialty 中文标签（用于报告标题）
_SPECIALTY_LABELS = {
    "search": "资料搜集",
    "analysis": "深度分析",
    "writing": "报告撰写",
    "coding": "代码实现",
    "general": "综合处理",
}


def _extract_subtask_output(r: Dict) -> str:
    """从单个子任务结果（task_executor report）中提取可读输出文本

    task_executor 返回的 report 结构：
      {goal, success, steps:[{action, status, result:{output/abs_path/...}}], ...}
    优先取最后一步的 result.output；其次取 result.abs_path（文件写入）；
    再退到 goal 作为占位。
    """
    if not isinstance(r, dict):
        return ""
    # 直接 output 字段（部分简化路径）
    output = r.get("output")
    if output and isinstance(output, str) and len(output) >= 10:
        return output
    # 从 steps 中提取最后一步的有效输出
    steps = r.get("steps") or []
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        result = step.get("result")
        if isinstance(result, dict):
            out = result.get("output")
            if out and isinstance(out, str) and len(out) >= 10:
                return out
            abs_path = result.get("abs_path")
            if abs_path:
                return f"(已生成文件: {abs_path})"
    # 退化：返回 goal 作为占位
    goal = r.get("goal", "")
    return f"(任务目标: {goal})" if goal else ""


def _format_workflow_report(wf_result: Dict, request_body: Dict) -> str:
    """把工作流执行结果格式化为对话框可展示的完整报告

    结构：
      # 工作流报告：<preset_name>
      > 主题：<topic> | 子任务：<completed>/<total> 成功 | 耗时：<x>s
      ---
      ## 【资料搜集】<subtask_goal>
      <output>
      ## 【深度分析】<subtask_goal>
      <output>
      ...
    """
    parts = []
    preset_name = request_body.get("goal", "工作流执行报告")
    completed = wf_result.get("completed", 0)
    total = wf_result.get("total_subtasks", 0)
    failed = wf_result.get("failed", 0)
    elapsed_ms = wf_result.get("elapsed_ms") or wf_result.get("execution_time", 0)
    elapsed_s = round(elapsed_ms / 1000, 1) if isinstance(elapsed_ms, (int, float)) else "?"
    cuf_audited = wf_result.get("cuf_audited", False)

    # 报告头
    header = f"# 工作流报告：{preset_name}\n"
    header += f"> 子任务：{completed}/{total} 成功"
    if failed:
        header += f"，{failed} 失败"
    header += f" | 耗时：{elapsed_s}s"
    if cuf_audited:
        header += " | CUF审计✓"
    header += "\n\n---\n"
    parts.append(header)

    # 各子任务输出
    results = wf_result.get("results") or {}
    if results:
        # 按 subtasks 原始顺序输出（results 是 dict，顺序可能乱）
        subtasks_order = wf_result.get("subtasks") or []
        ordered_sids = [st.get("subtask_id", "") for st in subtasks_order if isinstance(st, dict)]
        sids = [sid for sid in ordered_sids if sid in results]
        sids += [sid for sid in results.keys() if sid not in sids]  # 补充未在 order 中的

        for sid in sids:
            r = results.get(sid)
            if not isinstance(r, dict):
                continue
            specialty = r.get("specialty", "general")
            label = _SPECIALTY_LABELS.get(specialty, specialty)
            goal = r.get("goal", "")
            success = r.get("success", False)
            status_tag = "✓" if success else "✗"
            output = _extract_subtask_output(r)
            section = f"## 【{label}】{status_tag} {goal[:80]}\n{output}\n"
            parts.append(section)
    else:
        # 无 results，退到 summary
        summary = wf_result.get("summary", "")
        if summary:
            parts.append(summary + "\n")

    return "\n".join(parts)


def _build_response(merged: Dict, op_id: str, start: datetime) -> Dict[str, Any]:
    elapsed = (datetime.now() - start).total_seconds() * 1000
    # 原则五落地：强制内容过滤（双保险，即使process_request未过滤也会在此过滤）
    response_text = merged.get("response", "")
    filtered, warnings = content_filter.filter(response_text)
    if warnings and "filter_warnings" not in merged:
        merged["filter_warnings"] = warnings
    resp = {
        "success": not merged.get("blocked", False),
        "op_id": op_id,
        "response": filtered,  # 使用过滤后的文本
        "pattern_key": f"chat:{'tool' if 'tool_result' in merged else 'plain'}",
        "cuf_traces": merged.get("cuf_traces", []),
        "plugin_traces": merged.get("plugin_traces", []),
        "compensated": merged.get("compensated", False),
        "refunds": merged.get("refunds", []),
        "filter_warnings": merged.get("filter_warnings", []),
        "elapsed_ms": round(elapsed, 2),
        "balance": round(ledger.balance(), 4),
    }
    # 透传工作流自动触发的元信息（供前端识别工作流响应并展示审计轨迹）
    if merged.get("llm_mode"):
        resp["llm_mode"] = merged["llm_mode"]
    if merged.get("workflow_result"):
        resp["workflow_result"] = merged["workflow_result"]
    return resp


# ─── 路由 ────────────────────────────────────
# 注意：/@vite/client, /, /health, /status, /history, /help, /favicon.ico,
#       /self-check, /self-check/quick 已迁移到 api/system.py（通过 APIRouter 装配）
# /health, /status, /history 已迁移到 api/system.py（通过 APIRouter 装配）


# ─── 顶栏探活端点（无认证，供前端状态栏轮询） ────────────────────────────────
# ─── 知识库端点（任务2.2 RAG） ────────────────────────────────
from pydantic import BaseModel as PydanticModel

class KnowledgeRequest(PydanticModel):
    content: str
    source: str = ""
# ─── LLM平台管理端点（阶段1：多平台+本地模型） ────────────────────────────────
class PlatformSwitchRequest(PydanticModel):
    platform: str
    model: str = ""
# ─── 自学习闭环端点（阶段2） ────────────────────────────────
# ─── 代码自修改端点（阶段3） ────────────────────────────────
class CodeModificationRequest(PydanticModel):
    target_file: str
    description: str
    new_code: str
    proposer: str = "manual"
    reasoning: str = ""
    mode: str = "replace"  # replace / append / prepend

class ModificationActionRequest(PydanticModel):
    modification_id: str
    reason: str = ""
class AutoProposeRequest(PydanticModel):
    trigger: str = "manual"            # manual / feedback / defect
    feedback: str = ""
    description: str = ""
# ─── Agent端点（阶段4：任务自拆解+多步执行+脚本自清理） ────────────────────────────────
class AgentRunRequest(PydanticModel):
    goal: str
    cleanup: bool = True
    reflect: bool = True

class AgentExecuteRequest(PydanticModel):
    plan: dict
    task_id: str = ""

class CodeGenRequest(PydanticModel):
    requirement: str
    execute: bool = True

class ToolChainRequest(PydanticModel):
    tools: list  # [{tool, params, extract_field?, input_field?, on_fail?}]
# ─── 预置 Agent 工作流 ────────────────────────────────
# ─── 代码生成端点 ────────────────────────────────
# ─── 工具链端点 ────────────────────────────────
# ─── 任务模板端点 ────────────────────────────────
# ─── 工具偏好端点 ────────────────────────────────
# ─── 临时资源管理端点 ────────────────────────────────
# ─── 多轮对话端点 ────────────────────────────────
class ConversationStartRequest(PydanticModel):
    user_id: str = "default_user"
    metadata: Dict[str, Any] = {}

class ConversationMessageRequest(PydanticModel):
    role: str  # user/assistant/system
    content: str
    extra: Dict[str, Any] = {}
# ─── 扩展工具端点 ────────────────────────────────
class ExtendedToolCallRequest(PydanticModel):
    tool: str
    params: Dict[str, Any] = {}
# ─── 任务持久化端点 ────────────────────────────────
class CheckpointRequest(PydanticModel):
    task_id: str
    plan: Dict[str, Any]
    current_step: int
    step_context: Dict[str, Any] = {}
    status: str = "running"
# /agent/checkpoints 别名端点（前端使用，与 /task/checkpoint 对齐）
class AgentCheckpointRequest(PydanticModel):
    task: str
    plan: Dict[str, Any] = {}
    current_step: int = 0
    step_context: Dict[str, Any] = {}
# ─── 并行执行端点 ────────────────────────────────
class ParallelExecuteRequest(PydanticModel):
    plan: Dict[str, Any]
    task_id: str = ""
# ─── 条件分支端点 ────────────────────────────────
# ─── 多Agent端点 ────────────────────────────────
# ─── 自然语言工具选择端点 ────────────────────────────────
class ToolSelectRequest(PydanticModel):
    query: str
    context: Dict[str, Any] = {}
    max_tools: int = 3
# ─── 可视化端点 ────────────────────────────────
class VisualizePlanRequest(PydanticModel):
    plan: Dict[str, Any]

class VisualizeReportRequest(PydanticModel):
    report: Dict[str, Any]

class VisualizeMultiAgentRequest(PydanticModel):
    report: Dict[str, Any]

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
# ─── 工具权限端点 ────────────────────────────────
class PermissionCheckRequest(PydanticModel):
    user_level: str  # guest/user/power_user/admin 或 L0~L3
    tool_name: str

class ConfirmCreateRequest(PydanticModel):
    tool_name: str
    user_id: str

class ConfirmResolveRequest(PydanticModel):
    confirmed: bool
    resolver: str = ""

class ApprovalCreateRequest(PydanticModel):
    tool_name: str
    user_id: str

class ApprovalResolveRequest(PydanticModel):
    approved: bool
    approver: str = "admin"

class ElevationRequest(PydanticModel):
    user_id: str
    requested_level: str
    reason: str
# ─── 插件系统端点 ────────────────────────────────
# /plugins, /plugins/{name}/enable|disable|config, /plugins/load,
# /plugins/metrics, /plugins/list, /plugins/toggle, /plugins/stats,
# /plugins/market/* (18 个路由) 已迁移到 api/plugins.py（通过 APIRouter 装配）


# ─── MCP协议端点 ────────────────────────────────
# /mcp/* (8 个路由) 已迁移到 api/mcp.py（通过 APIRouter 装配）



# ─── 多模态端点 ────────────────────────────────
class MultimodalProcessRequest(PydanticModel):
    input_data: Any  # 文本/文件路径/混合字典
    modality: str = ""  # text/image/audio/video/mixed，空则自动检测

class MultimodalPathRequest(PydanticModel):
    path: str
# ─── 语音IO端点 ────────────────────────────────
class VoiceRecognizeRequest(PydanticModel):
    audio_data: str  # base64编码的音频数据
    format: str = "wav"
    language: str = "zh"

class VoiceSynthesizeRequest(PydanticModel):
    text: str
    lang: str = "zh"
    rate: int = 150
    pitch: int = 50
    volume: float = 1.0
# ─── 分布式执行端点 ────────────────────────────────
# /distributed/execute, /distributed/split, /distributed/merge,
# /distributed/workers, /distributed/workers/add,
# /distributed/workers/{worker_id}/remove, /distributed/health
# (7 个路由) 已迁移到 api/distributed.py（通过 APIRouter 装配）
# 注意：/distributed/status 保留在 server.py（耦合多Agent协调器全局）


# ─── 向量数据库端点（v5.0优化） ────────────────────────────────
class VectorSearchRequest(PydanticModel):
    query: str
    top_k: int = 5
    threshold: float = 0.3
# ─── 本地模型端点（v5.0优化） ────────────────────────────────
class LocalModelLoadRequest(PydanticModel):
    model_name: str
    quantization: str = "auto"  # auto/4bit/8bit/none
    device: str = "auto"  # auto/cuda/cpu/mps

class ModelTypeSwitchRequest(PydanticModel):
    target_type: str  # text / vl
    model_name: str = ""  # 为空自动选择
    quantization: str = "auto"
    device: str = "auto"

class VisionChatRequest(PydanticModel):
    prompt: str
    image_path: str = ""
    image_url: str = ""
    image_base64: str = ""
    system_prompt: str = "default"
    temperature: float = 0.7
    max_tokens: int = 1024
    auto_switch: bool = True  # 自动从 text 切换到 vl
# ─── 视觉对话端点（v5.1 VL 集成） ────────────────────────────────
# ─── 自动化能力端点（v5.1：浏览器/截屏/网页抓取/桌面控制） ────────────────────────────────

class BrowserNavigateRequest(PydanticModel):
    url: str
    headless: bool = True
    wait_until: str = "domcontentloaded"  # load/domcontentloaded/networkidle
    viewport_width: int = 1280
    viewport_height: int = 720

class BrowserActionRequest(PydanticModel):
    selector: str = ""
    value: str = ""
    key: str = ""
    pixels: int = 500
    direction: str = "down"  # down/up
    full_page: bool = False
    timeout: int = 30000
    delay: int = 50

class WebFetchRequest(PydanticModel):
    url: str
    max_length: int = 10000
    article_mode: bool = False  # 文章正文模式

class ScreenCaptureRequest(PydanticModel):
    monitor: int = 1
    left: int = 0
    top: int = 0
    width: int = 0  # 0=全屏
    height: int = 0
    save_to_file: bool = True

class DesktopActionRequest(PydanticModel):
    action: str  # click/type/press/hotkey/scroll/move/drag/screenshot
    x: int = 0
    y: int = 0
    text: str = ""
    key: str = ""
    keys: List[str] = []  # 组合键
    button: str = "left"  # left/right/middle
    clicks: int = 1
    pixels: int = 0  # 滚动格数
    dx: int = 0  # 拖拽偏移
    dy: int = 0

class VisionAnalyzeScreenRequest(PydanticModel):
    prompt: str = "描述屏幕上的内容"
    monitor: int = 1
    region: List[int] = []  # [left, top, width, height]，为空则全屏
    auto_switch: bool = True  # 自动切换到 VL 模型
    max_tokens: int = 1024
# ─── 浏览器自动化 ─────────────────────────
# ─── 网页抓取 ─────────────────────────
# ─── 屏幕截图 ─────────────────────────
# ─── 桌面控制 ─────────────────────────
# ─── VL + 截屏联动：看屏幕 ─────────────────────────
# ─── 实时语音监听端点（v5.2 新增） ────────────────────────────────

# 语音监听事件队列（前端轮询获取）
_voice_events: List[Dict[str, Any]] = []

class VoiceListenStartRequest(PydanticModel):
    wake_word: str = ""  # 为空则直通模式（任何语音都触发）
    language: str = "zh"
    device_index: int = -1  # -1=默认设备
    auto_chat: bool = True  # 识别到语音后自动调用 LLM 生成回复
# ─── 功能模块管理端点（v5.2 新增） ────────────────────────────────
# /modules/* (8 个路由) 已迁移到 api/modules.py（通过 APIRouter 装配）


# ─── 启动时注册内置模块 ────────────────────────────────────

@app.on_event("startup")
async def _register_modules_on_startup():
    """启动时注册内置功能模块到注册表 + 启动周期审计定时器 + D层完整性校验"""
    # 注入全局单例到 api.deps（供 api/*.py 的路由访问）
    _api_set_globals(
        ledger=ledger,
        whitelist=whitelist,
        guard=guard,
        tool_guard=tool_guard,
        metacog=metacog,
        memory=memory,
        feedback=feedback,
        learning_engine=learning_engine,
        code_modifier=code_modifier,
        api_key=_get_configured_api_key(),
        admin_key=_get_configured_admin_key(),
    )

    try:
        from m_layer.module_registry import register_builtin_modules
        register_builtin_modules()
        logger.info("内置模块已注册到 ModuleRegistry")
    except Exception as e:
        logger.warning(f"注册内置模块失败（不影响核心功能）: {e}")

    # D层完整性启动校验
    try:
        from guard.d_layer_integrity import verify_on_startup
        verify_on_startup()
        logger.info("D层完整性校验通过")
    except Exception as e:
        logger.warning(f"D层完整性校验失败（不阻塞启动）: {e}")

    # 账本就绪强校验：启动时验证可写（防止运行时才发现磁盘满/权限问题）
    try:
        test_bal = ledger.balance()
        # 通过 refund+pay_tax 配对验证读写完整性（0金额，不影响余额）
        ledger.refund(0.0, reason="startup_ready_check", op_id="startup_ready")
        logger.info(f"账本就绪校验通过: 余额={test_bal:.2f}E, 可读写")
    except Exception as e:
        logger.error(f"⚠️ 账本就绪校验失败：{e}")
        logger.error("账本不可写将导致 CUF 审计全部失败，请检查 SCU3_data/ 目录权限和磁盘空间")
        # 不阻塞启动，但标记就绪状态供 /status 查询
        app.state.ledger_ready = False
    else:
        app.state.ledger_ready = True

    # 周期审计定时器（每24小时自动触发一次 daily_audit）
    # 使用 threading.Event 实现可中断的等待，支持优雅停止
    try:
        import threading
        _audit_stop_event = threading.Event()

        def _periodic_audit():
            # 24小时拆成 96 个 15 分钟切片，便于及时响应停止信号
            interval_slices = 96
            slice_seconds = 15 * 60
            while not _audit_stop_event.is_set():
                # 等待一个完整周期（可被中断）
                for _ in range(interval_slices):
                    if _audit_stop_event.wait(slice_seconds):
                        return
                try:
                    metacog.daily_audit(force=False)
                    logger.info("周期审计自动执行完成")
                except Exception as ae:
                    logger.warning(f"周期审计自动执行失败: {ae}")

        t = threading.Thread(target=_periodic_audit, daemon=True, name="periodic_audit")
        t.start()
        # 暴露停止事件供 shutdown 钩子使用
        app.state.audit_stop_event = _audit_stop_event
        logger.info("周期审计定时器已启动（24小时间隔，可中断）")
    except Exception as e:
        logger.warning(f"周期审计定时器启动失败: {e}")


@app.on_event("shutdown")
async def _graceful_shutdown():
    """优雅关闭：通知周期审计线程退出"""
    try:
        stop_evt = getattr(app.state, "audit_stop_event", None)
        if stop_evt is not None:
            stop_evt.set()
            logger.info("已通知周期审计线程停止")
    except Exception as e:
        logger.warning(f"shutdown 钩子异常: {e}")


# ─── 前端补全端点（favicon + 别名 + 功能桩） ────────────────────────────────
# /favicon.ico 已迁移到 api/system.py（通过 APIRouter 装配）


# ─── 路径别名（前端调用名 → 后端已有路由） ────────────────────────────────
# /plugins/list 已迁移到 api/plugins.py（通过 APIRouter 装配）
# ─── 三级记忆管理端点（L1工作/L2语义/L3情景） ────────────────────────────────
# /memory/* (7 个路由) 已迁移到 api/memory.py（通过 APIRouter 装配）
# /plugins/toggle, /plugins/stats 已迁移到 api/plugins.py（通过 APIRouter 装配）
# ─── 插件市场（自动下载/加载/卸载） ────────────────────────────────
# /plugins/toggle, /plugins/stats, /plugins/market/* (11 个路由)
# 已迁移到 api/plugins.py（通过 APIRouter 装配）


# ─── 经验存储（学习沉淀） ────────────────────────────────
# ─── 自进化引擎（自动总结不足+生成方案+提交审核） ────────────────────────────────
# ─── 帮助中心 ────────────────────────────────
# /help 已迁移到 api/system.py（通过 APIRouter 装配）


# ─── 邮件/日历（未集成，返回未配置状态） ────────────────────────────────
# ─── 资讯/热搜（未集成，返回空） ────────────────────────────────
# ─── CUF 活动流 + 快速检查 ────────────────────────────────
# /cuf/*, /ledger/* 已迁移到 api/ledger.py
# /self-check, /self-check/quick 已迁移到 api/system.py


# ─── 权限状态 ────────────────────────────────
# ─── Agent 可视化 ────────────────────────────────
# ─── 分布式状态 ────────────────────────────────
# /distributed/status 已迁移到 api/distributed.py（通过 APIRouter 装配）


# ─── 代码自修改提案（别名） ────────────────────────────────
# ─── 图片生成（Pollinations 免配置） ────────────────────────────────
_SIZE_MAP = {
    "landscape_16_9": (1280, 720),
    "landscape_4_3": (1152, 864),
    "square_hd": (1024, 1024),
    "square": (1024, 1024),
    "portrait_4_3": (864, 1152),
    "portrait_16_9": (720, 1280),
}
# ─── 图片后端列表 ────────────────────────────────
# ─── 图片对话（VL模型） ────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    logger.info("🚀 标准计算单元3 SCU3 启动 (v3 架构 + Agent能力)")

    # 安全告警：使用默认Key时显著提示
    _get_configured_api_key()  # 触发标记
    _get_configured_admin_key()
    if _USING_DEV_API_KEY or _USING_DEV_ADMIN_KEY:
        warn_msg = (
            "\n" + "=" * 70 + "\n"
            "⚠️  安全告警：正在使用开发模式默认 API Key/Admin Key\n"
            "    生产环境务必配置环境变量：\n"
            "      set SCU3_API_KEY=<your_strong_key>\n"
            "      set SCU3_ADMIN_API_KEY=<your_strong_admin_key>\n"
            "    默认Key已公开在源码中，不安全！\n"
            + "=" * 70
        )
        logger.warning(warn_msg)
        print(warn_msg)

    # C4修复：默认监听127.0.0.1（生产环境用反向代理）
    host = os.getenv("SCU3_HOST", "127.0.0.1")
    port = int(os.getenv("SCU3_PORT", "8300"))
    if host in ("0.0.0.0", "::"):
        logger.warning(f"⚠️ 服务监听 {host}，将暴露至所有网卡！仅开发测试用。")

    # 启动前熵税余额检查：余额过低时自动补齐一次（不占用限频窗口）
    try:
        from d_layer.axioms import MIN_BALANCE, INITIAL_BUDGET, MAX_SINGLE_TRANSACTION
        current_balance = ledger.balance()
        if current_balance < MIN_BALANCE:
            # 直接补充到初始预算（绕过 _ensure_min_balance 限频，仅启动时执行一次）
            shortage = INITIAL_BUDGET - current_balance
            # 分次补齐，每次不超过 MAX_SINGLE_TRANSACTION
            while shortage > 0:
                batch = min(shortage, MAX_SINGLE_TRANSACTION - 1)
                ok, msg = ledger.replenish(batch, auth_token=os.getenv("SCU3_LEDGER_AUTH", ""),
                                            reason="启动自动补齐（余额低于MIN_BALANCE）")
                if not ok:
                    logger.warning(f"启动自动补齐失败: {msg}")
                    break
                shortage -= batch
            logger.info(f"💰 启动自动补齐完成, 新余额={ledger.balance():.2f}E")
        else:
            logger.info(f"💰 熵税余额充足: {current_balance:.2f}E")
    except Exception as e:
        logger.warning(f"启动余额检查失败（不阻塞）: {e}")

    uvicorn.run(app, host=host, port=port, log_level="info")
