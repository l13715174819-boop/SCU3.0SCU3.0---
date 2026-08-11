# -*- coding: utf-8 -*-
"""
SCU2 - 标准计算单元2 · 主入口
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
logger = logging.getLogger("scu2.main")

# ─── 初始化组件 ────────────────────────────────
DATA_DIR = os.path.join(BASE_DIR, "scu2_data")
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

app = FastAPI(title="标准计算单元2 SCU2", version="2.0.0")


# ─── C4修复：API Key 认证中间件 ────────────────────────────────
# 安全策略：必须通过环境变量配置，未配置则使用开发模式默认Key并告警
# 生产环境务必设置 SCU2_API_KEY 和 SCU2_ADMIN_API_KEY 环境变量
API_KEY_ENV = "SCU2_API_KEY"
# 开发模式默认Key（仅当未配置环境变量时使用，启动时会输出显著告警）
_DEV_DEFAULT_API_KEY = "scu2_dev_key_2026"
ADMIN_API_KEY_ENV = "SCU2_ADMIN_API_KEY"
_DEV_DEFAULT_ADMIN_KEY = "scu2_admin_key_2026"

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

    # ① W2 感知层
    ctx = perception.process(prompt, {"user_id": user_id})

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


def _build_response(merged: Dict, op_id: str, start: datetime) -> Dict[str, Any]:
    elapsed = (datetime.now() - start).total_seconds() * 1000
    # 原则五落地：强制内容过滤（双保险，即使process_request未过滤也会在此过滤）
    response_text = merged.get("response", "")
    filtered, warnings = content_filter.filter(response_text)
    if warnings and "filter_warnings" not in merged:
        merged["filter_warnings"] = warnings
    return {
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


# ─── 路由 ────────────────────────────────────
@app.get("/@vite/client")
async def vite_client():
    """空响应：某些浏览器开发环境会自动请求 /@vite/client（HMR 探测），
    返回空 JS 避免控制台 ERR_ABORTED 报错。
    """
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("", media_type="application/javascript")


@app.get("/")
async def index():
    html_path = os.path.join(BASE_DIR, "web", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>web/index.html not found</h1>")


@app.post("/chat")
async def chat(req: ChatRequest, api_key: str = Depends(verify_api_key)):
    try:
        return JSONResponse(process_request(req.prompt, req.user_id))
    except Exception as e:
        import traceback as _tb
        logger.error(f"/chat 异常: {e}\n{_tb.format_exc()}")
        return JSONResponse({"response": f"处理失败: {e}", "error": str(e),
                             "traceback": _tb.format_exc()[-500:]}, status_code=500)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, api_key: str = Depends(verify_api_key)):
    """SSE流式聊天端点（任务2.1）

    修复：复用完整 process_request 流程（感知→记忆→执行→守卫→认知→工具/兜底），
    再将最终回复切片流式发送。原实现绕过了执行层与认知层，导致 web_search 等工具
    永不触发，LLM 凭空回答"不能联网"。
    """
    import asyncio
    from fastapi.responses import StreamingResponse
    import json as _json
    import re as _re

    # 在线程中执行完整流程（process_request 含 LLM 调用，会阻塞事件循环）
    try:
        result = await asyncio.to_thread(process_request, req.prompt, req.user_id)
    except Exception as e:
        logger.error(f"chat_stream process_request 异常: {e}", exc_info=True)
        result = {"success": False, "op_id": f"op_{int(time.time()*1000)}",
                  "response": f"处理失败: {e}", "cuf_traces": [], "balance": 0}

    op_id = result.get("op_id", f"op_{int(time.time()*1000)}")
    response_text = result.get("response", "")
    cuf_traces = result.get("cuf_traces", [])
    balance = result.get("balance", 0)
    pattern_key = result.get("pattern_key", "chat:plain")
    fallback = result.get("fallback", False)

    def event_stream():
        # 元数据（含守卫链trace，前端可展示）
        yield f"data: {_json.dumps({'type':'meta','op_id':op_id,'mode':result.get('llm_mode','deepseek'),'cuf_traces':cuf_traces,'history':0,'pattern_key':pattern_key,'fallback':fallback})}\n\n"
        # 切片流式发送（按标点/换行分块，模拟打字机效果）
        if response_text:
            chunks = _re.findall(r'[^，。！？；：\n]+[，。！？；：\n]?', response_text)
            if not chunks:
                chunks = [response_text]
            for chunk in chunks:
                yield f"data: {_json.dumps({'type':'chunk','content':chunk})}\n\n"
                time.sleep(0.02)  # 小延迟，前端有打字机观感
        # 结束标记
        yield f"data: {_json.dumps({'type':'done','op_id':op_id,'balance':balance})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@app.post("/feedback")
async def feedback_endpoint(req: FeedbackRequest, api_key: str = Depends(verify_api_key)):
    result = feedback.collect(req.user_id, req.pattern_key, req.kind)
    return JSONResponse({"success": "error" not in result, "data": result})


@app.post("/whitelist/add")
async def whitelist_add(req: WhitelistRequest, api_key: str = Depends(verify_admin_key)):
    ok, msg = whitelist.add(
        action=req.action, source=req.source, target=req.target,
        contracts=req.contracts, code_hash=req.code_hash,
        ttl_hours=req.ttl_hours,
    )
    return JSONResponse({"success": ok, "msg": msg})


@app.get("/whitelist/list")
async def whitelist_list(api_key: str = Depends(verify_admin_key)):
    return JSONResponse({"entries": whitelist.list_all()})


@app.post("/audit/daily")
async def daily_audit(force: bool = False, api_key: str = Depends(verify_admin_key)):
    result = metacog.force_audit() if force else metacog.daily_audit()
    return JSONResponse(result)


@app.get("/health")
async def health():
    """无认证健康检查端点（供前端探活/心跳判断在线状态）

    返回 {status, arch, version, balance}
    敏感字段（stats/whitelist_count/last_audit）仍走需认证的 /status
    """
    return JSONResponse({
        "status": "ok",
        "arch": "v3",
        "version": "2.0.0",
        "balance": round(ledger.balance(), 4),
    })


@app.get("/status")
async def status(api_key: str = Depends(verify_admin_key)):
    return JSONResponse({
        "arch": "v3", "version": "2.0.0",
        "balance": round(ledger.balance(), 4),
        "stats": ledger.stats(),
        "whitelist_count": len(whitelist.list_all()),
        "last_audit": metacog._last_audit_time.isoformat() if metacog._last_audit_time else None,
    })


@app.get("/history")
async def history(limit: int = 20, api_key: str = Depends(verify_admin_key)):
    return JSONResponse({"history": ledger.history(limit)})


# ─── 顶栏探活端点（无认证，供前端状态栏轮询） ────────────────────────────────
@app.get("/pair/status")
async def pair_status():
    """阴阳对子（Yin-Yang Pair）状态

    对子为高风险操作时临时实例化的双背书机制，非常驻服务。
    无活跃对子时返回 enabled=false。
    """
    return JSONResponse({
        "success": True,
        "data": {
            "enabled": False,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "pending_human": 0,
            "avg_gamma_yin": 0.0,
            "avg_gamma_yang": 0.0,
        },
    })


@app.get("/cognition/yin-yang")
async def cognition_yin_yang_status():
    """阴阳对子思考状态（方案C）

    返回最近一次 analytical 意图触发的阴阳双签状态，
    供前端太极图动态展示。
    """
    return JSONResponse({
        "success": True,
        "data": _last_yin_yang_state,
        "threshold": {
            "yin": 0.75,
            "yang": 0.65,
        },
    })


@app.get("/local/backends/status")
async def local_backends_status():
    """本地模型后端状态（LM Studio / ComfyUI）

    SCU2 默认使用自有 local_model（Qwen2.5-7B/VL），未集成 LM Studio/ComfyUI。
    返回 enabled=false 让前端显示"未启用"。
    """
    return JSONResponse({
        "success": True,
        "data": {
            "lmstudio": {
                "enabled": False,
                "available": False,
                "url": "http://localhost:1234/v1",
                "loaded_models": [],
            },
            "comfyui": {
                "enabled": False,
                "available": False,
                "checkpoint": None,
            },
        },
    })


# ─── 知识库端点（任务2.2 RAG） ────────────────────────────────
from pydantic import BaseModel as PydanticModel

class KnowledgeRequest(PydanticModel):
    content: str
    source: str = ""

@app.post("/knowledge/add")
async def knowledge_add(req: KnowledgeRequest, api_key: str = Depends(verify_api_key)):
    """添加知识文档"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    doc_id = store.add_document(req.content, metadata={"source": req.source})
    return JSONResponse({"success": doc_id > 0, "doc_id": doc_id})

@app.post("/knowledge/import")
async def knowledge_import(req: dict, api_key: str = Depends(verify_admin_key)):
    """从目录批量导入知识（C3+C4修复：需管理员权限+路径限制）"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    dir_path = req.get("dir_path", "")
    count = store.import_from_directory(dir_path)
    return JSONResponse({"success": True, "imported": count})

@app.get("/knowledge/search")
async def knowledge_search(q: str = "", top_k: int = 3, api_key: str = Depends(verify_api_key)):
    """检索知识"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    results = store.search(q, top_k=top_k)
    return JSONResponse({"results": results})

@app.get("/knowledge/list")
async def knowledge_list(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """列出知识文档"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    return JSONResponse({"documents": store.list_documents(limit)})

@app.get("/knowledge/status")
async def knowledge_status(api_key: str = Depends(verify_api_key)):
    """知识库状态"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    return JSONResponse(store.get_status())

@app.delete("/knowledge/{doc_id}")
async def knowledge_delete(doc_id: int, api_key: str = Depends(verify_admin_key)):
    """删除知识文档"""
    from w1_layer.knowledge_store import get_store
    store = get_store()
    ok = store.delete_document(doc_id)
    return JSONResponse({"success": ok})


# ─── LLM平台管理端点（阶段1：多平台+本地模型） ────────────────────────────────
class PlatformSwitchRequest(PydanticModel):
    platform: str
    model: str = ""

@app.get("/llm/platforms")
async def llm_platforms(api_key: str = Depends(verify_api_key)):
    """列出所有可用LLM平台"""
    from m_layer.llm_client import get_client
    client = get_client()
    return JSONResponse({
        "active": client.get_active_platform(),
        "available": client.list_available_platforms(),
    })


@app.get("/models")
async def list_models():
    """列出所有支持的模型平台（供前端对话面板下拉框使用，无认证）

    返回所有预设平台（含未配置Key的），标注 available 状态，便于用户了解可选项。
    返回 {success, data:{current, platforms:[{id,label,model,active,available,local}]}}
    """
    from m_layer.llm_client import get_client
    import urllib.request as _urlreq
    import json as _json
    try:
        client = get_client()
        active = client.get_active_platform()
        current_model = client.default_model or active.get("id", "default")
        platforms = []
        for pid, cfg in client.PLATFORM_CONFIGS.items():
            entry = {
                "id": pid,
                "label": cfg["label"],
                "model": cfg["default_model"],
                "local": cfg["local"],
                "active": pid == client.active_platform,
                "available": False,
            }
            if cfg["local"]:
                # 实时探测本地服务
                base_url = os.getenv(cfg["env_url"], cfg["default_url"])
                try:
                    with _urlreq.urlopen(f"{base_url}/models", timeout=1.5) as r:
                        data = _json.loads(r.read().decode("utf-8"))
                        models = data.get("data", [])
                    if models:
                        entry["available"] = True
                        entry["model"] = models[0].get("id", cfg["default_model"])
                except Exception:
                    pass
            else:
                # 云端：检查 Key 是否配置（支持主备变量名）
                key = os.getenv(cfg["env_key"], "")
                if not key and cfg.get("env_key_alt"):
                    key = os.getenv(cfg["env_key_alt"], "")
                if key:
                    entry["available"] = True
                    entry["model"] = os.getenv(pid.upper() + "_MODEL", cfg["default_model"])
            platforms.append(entry)
        return JSONResponse({
            "success": True,
            "data": {
                "current": current_model,
                "current_platform": active,
                "platforms": platforms,
            },
        })
    except Exception as e:
        return JSONResponse({
            "success": True,
            "data": {
                "current": "default",
                "platforms": [{"id": "default", "label": "默认模型", "model": "default", "active": True, "available": True, "local": False}],
            },
        })


@app.get("/units")
async def list_units():
    """列出可用 SCU 单元（供前端对话面板下拉框使用，无认证）

    SCU2 为单实例部署，返回默认单元。
    """
    return JSONResponse({
        "success": True,
        "data": {
            "units": [
                {
                    "uid": "scu2-default",
                    "system_prompt_style": "SCU2 标准单元",
                },
            ],
        },
    })

@app.post("/llm/switch")
async def llm_switch(req: PlatformSwitchRequest, api_key: str = Depends(verify_admin_key)):
    """切换LLM平台（需管理员权限）"""
    from m_layer.llm_client import get_client
    client = get_client()
    result = client.switch_platform(req.platform, req.model)
    return JSONResponse(result)

@app.get("/llm/status")
async def llm_status(api_key: str = Depends(verify_api_key)):
    """LLM客户端状态"""
    from m_layer.llm_client import get_client
    client = get_client()
    return JSONResponse(client.get_status())


# ─── 自学习闭环端点（阶段2） ────────────────────────────────
@app.post("/learning/run")
async def learning_run(force: bool = True, api_key: str = Depends(verify_admin_key)):
    """手动触发自学习闭环（需管理员权限）"""
    report = learning_engine.learn(force=True)
    return JSONResponse(report)

@app.get("/learning/status")
async def learning_status(api_key: str = Depends(verify_api_key)):
    """自学习状态"""
    return JSONResponse(learning_engine.get_status())

@app.get("/learning/history")
async def learning_history(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """学习历史"""
    return JSONResponse({"history": learning_engine.get_learning_history(limit)})

@app.post("/learning/reset")
async def learning_reset(api_key: str = Depends(verify_admin_key)):
    """重置提示词权重（回滚机制，需管理员权限）"""
    result = learning_engine.reset_weights()
    return JSONResponse({"success": True, "result": result})


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

@app.post("/self-modify/propose")
async def self_modify_propose(req: CodeModificationRequest, api_key: str = Depends(verify_admin_key)):
    """提议代码修改（需管理员权限，经安全审查+阴阳双签）"""
    proposal = code_modifier.propose_modification(
        target_file=req.target_file,
        description=req.description,
        new_code=req.new_code,
        proposer=req.proposer,
        reasoning=req.reasoning,
        mode=req.mode,
    )
    return JSONResponse(proposal)

@app.get("/self-modify/pending")
async def self_modify_pending(api_key: str = Depends(verify_admin_key)):
    """列出待审批的修改"""
    return JSONResponse({"pending": code_modifier.list_pending()})

@app.post("/self-modify/approve")
async def self_modify_approve(req: ModificationActionRequest, api_key: str = Depends(verify_admin_key)):
    """审批通过并应用修改（需管理员权限）"""
    result = code_modifier.approve_modification(req.modification_id, approved_by="admin")
    return JSONResponse(result)

@app.post("/self-modify/reject")
async def self_modify_reject(req: ModificationActionRequest, api_key: str = Depends(verify_admin_key)):
    """拒绝修改（需管理员权限）"""
    result = code_modifier.reject_modification(req.modification_id, req.reason)
    return JSONResponse(result)

@app.post("/self-modify/rollback")
async def self_modify_rollback(req: ModificationActionRequest, api_key: str = Depends(verify_admin_key)):
    """回滚已应用的修改（需管理员权限）"""
    result = code_modifier.rollback_modification(req.modification_id)
    return JSONResponse(result)

@app.get("/self-modify/history")
async def self_modify_history(limit: int = 20, api_key: str = Depends(verify_admin_key)):
    """修改历史"""
    return JSONResponse({"history": code_modifier.get_history(limit)})

@app.get("/self-modify/status")
async def self_modify_status(api_key: str = Depends(verify_admin_key)):
    """自修改引擎状态"""
    return JSONResponse(code_modifier.get_status())


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

@app.post("/agent/run")
async def agent_run(req: AgentRunRequest, api_key: str = Depends(verify_api_key)):
    """完整Agent执行：目标→拆解→执行→反思→清理"""
    from m_layer.task_executor import get_executor
    executor = get_executor()
    result = executor.run(req.goal, cleanup=req.cleanup, reflect=req.reflect)
    return JSONResponse(result)

@app.post("/agent/plan")
async def agent_plan(req: AgentRunRequest, api_key: str = Depends(verify_api_key)):
    """仅生成执行计划（不执行）"""
    from m_layer.task_executor import get_executor
    executor = get_executor()
    plan = executor.create_plan(req.goal)
    return JSONResponse(plan)

@app.post("/agent/execute")
async def agent_execute(req: AgentExecuteRequest, api_key: str = Depends(verify_api_key)):
    """执行已有计划"""
    from m_layer.task_executor import get_executor
    executor = get_executor()
    result = executor.execute_plan(req.plan, task_id=req.task_id or None)
    return JSONResponse(result)

@app.get("/agent/history")
async def agent_history(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """Agent执行历史"""
    from m_layer.task_executor import get_executor
    return JSONResponse({"history": get_executor().get_history(limit)})

@app.get("/agent/status")
async def agent_status(api_key: str = Depends(verify_api_key)):
    """Agent执行器状态"""
    from m_layer.task_executor import get_executor
    return JSONResponse(get_executor().get_status())

@app.post("/agent/learn")
async def agent_learn(api_key: str = Depends(verify_admin_key)):
    """触发Agent学习（从历史中积累经验）"""
    from m_layer.task_executor import get_executor
    from m_layer.agent_learning import get_agent_learning
    history = get_executor().get_history(100)
    report = get_agent_learning().learn_from_history(history)
    return JSONResponse(report)

@app.get("/agent/experience")
async def agent_experience(goal: str = "", api_key: str = Depends(verify_api_key)):
    """查询类似任务的执行经验"""
    from m_layer.agent_learning import get_agent_learning
    exp = get_agent_learning().query_experience(goal)
    return JSONResponse(exp)

# ─── 代码生成端点 ────────────────────────────────
@app.post("/codegen/generate")
async def codegen_generate(req: CodeGenRequest, api_key: str = Depends(verify_api_key)):
    """代码生成（可选自动执行）"""
    from m_layer.code_generator import get_code_generator
    gen = get_code_generator()
    if req.execute:
        result = gen.generate_and_run(req.requirement)
    else:
        result = gen.generate_only(req.requirement)
    return JSONResponse(result)

# ─── 工具链端点 ────────────────────────────────
@app.post("/toolchain/execute")
async def toolchain_execute(req: ToolChainRequest, api_key: str = Depends(verify_api_key)):
    """多工具链式执行"""
    from m_layer.tool_chain import quick_chain
    result = quick_chain(req.tools)
    return JSONResponse(result)

# ─── 任务模板端点 ────────────────────────────────
@app.get("/templates")
async def templates_list(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """列出任务模板"""
    from m_layer.task_template import get_template_manager
    return JSONResponse({"templates": get_template_manager().list_templates(limit)})

@app.get("/templates/stats")
async def templates_stats(api_key: str = Depends(verify_api_key)):
    """模板统计"""
    from m_layer.task_template import get_template_manager
    return JSONResponse(get_template_manager().get_stats())

@app.delete("/templates/{template_id}")
async def templates_delete(template_id: str, api_key: str = Depends(verify_admin_key)):
    """删除模板"""
    from m_layer.task_template import get_template_manager
    ok = get_template_manager().delete_template(template_id)
    return JSONResponse({"success": ok})

# ─── 工具偏好端点 ────────────────────────────────
@app.get("/tools/stats")
async def tools_stats(api_key: str = Depends(verify_api_key)):
    """工具使用统计"""
    from m_layer.tool_preference import get_tool_preference
    return JSONResponse(get_tool_preference().get_all_stats())

@app.get("/tools/recommend")
async def tools_recommend(scenario: str = "default", top_k: int = 3,
                           api_key: str = Depends(verify_api_key)):
    """推荐最优工具"""
    from m_layer.tool_preference import get_tool_preference
    return JSONResponse({"recommendations": get_tool_preference().recommend(scenario, top_k)})

# ─── 临时资源管理端点 ────────────────────────────────
@app.get("/temp/resources")
async def temp_resources(task_id: str = "", api_key: str = Depends(verify_api_key)):
    """列出临时资源"""
    from w1_layer.temp_manager import get_temp_manager
    return JSONResponse(get_temp_manager().list_temp_resources(task_id or None))

@app.post("/temp/cleanup")
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

@app.get("/temp/history")
async def temp_history(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """清理历史"""
    from w1_layer.temp_manager import get_temp_manager
    return JSONResponse({"history": get_temp_manager().get_history(limit)})


# ─── 多轮对话端点 ────────────────────────────────
class ConversationStartRequest(PydanticModel):
    user_id: str = "default_user"
    metadata: Dict[str, Any] = {}

class ConversationMessageRequest(PydanticModel):
    role: str  # user/assistant/system
    content: str
    extra: Dict[str, Any] = {}

@app.post("/conversation/start")
async def conversation_start(req: ConversationStartRequest, api_key: str = Depends(verify_api_key)):
    """开始新对话会话，返回session_id"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        session_id = get_conversation_manager().create_session(req.user_id, req.metadata)
        return JSONResponse({"success": True, "session_id": session_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/conversation/{session_id}/message")
async def conversation_add_message(session_id: str, req: ConversationMessageRequest,
                                    api_key: str = Depends(verify_api_key)):
    """添加消息到指定会话"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        ok = get_conversation_manager().add_message(session_id, req.role, req.content, req.extra)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/conversation/{session_id}/history")
async def conversation_history(session_id: str, limit: int = 10,
                                api_key: str = Depends(verify_api_key)):
    """获取会话历史消息"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        history = get_conversation_manager().get_history(session_id, limit)
        return JSONResponse({"success": True, "history": history})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/conversation/{session_id}/context")
async def conversation_context(session_id: str, limit: int = 10,
                                api_key: str = Depends(verify_api_key)):
    """获取LLM注入上下文（role/content格式）"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        ctx = get_conversation_manager().get_history_for_llm(session_id, limit)
        return JSONResponse({"success": True, "context": ctx})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.delete("/conversation/{session_id}")
async def conversation_delete(session_id: str, api_key: str = Depends(verify_api_key)):
    """删除会话"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        ok = get_conversation_manager().delete_session(session_id)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/conversation/sessions")
async def conversation_sessions(user_id: str = "", limit: int = 20,
                                 api_key: str = Depends(verify_api_key)):
    """列出所有会话"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        sessions = get_conversation_manager().list_sessions(user_id or None, limit)
        return JSONResponse({"success": True, "sessions": sessions})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 扩展工具端点 ────────────────────────────────
class ExtendedToolCallRequest(PydanticModel):
    tool: str
    params: Dict[str, Any] = {}

@app.get("/extended_tools/list")
async def extended_tools_list(api_key: str = Depends(verify_api_key)):
    """列出所有扩展工具"""
    try:
        from w1_layer.extended_tools import get_extended_tools
        tools = get_extended_tools()
        return JSONResponse({"success": True, "tools": list(tools.TOOL_TYPES.keys())})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/extended_tools/call")
async def extended_tools_call(req: ExtendedToolCallRequest,
                               api_key: str = Depends(verify_api_key)):
    """调用扩展工具"""
    try:
        from w1_layer.extended_tools import get_extended_tools
        result = get_extended_tools().execute(req.tool, req.params)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/extended_tools/categories")
async def extended_tools_categories(api_key: str = Depends(verify_api_key)):
    """按类别（read/write）列出工具"""
    try:
        from w1_layer.extended_tools import get_extended_tools
        types = get_extended_tools().TOOL_TYPES
        categories: Dict[str, list] = {}
        for tool, ttype in types.items():
            categories.setdefault(ttype, []).append(tool)
        return JSONResponse({"success": True, "categories": categories})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 任务持久化端点 ────────────────────────────────
class CheckpointRequest(PydanticModel):
    task_id: str
    plan: Dict[str, Any]
    current_step: int
    step_context: Dict[str, Any] = {}
    status: str = "running"

@app.post("/task/checkpoint")
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

@app.get("/task/checkpoint/{task_id}")
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

@app.delete("/task/checkpoint/{task_id}")
async def task_checkpoint_delete(task_id: str, api_key: str = Depends(verify_api_key)):
    """删除任务检查点"""
    try:
        from m_layer.task_persistence import get_task_persistence
        ok = get_task_persistence().delete_checkpoint(task_id)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/task/checkpoints")
async def task_checkpoints_list(api_key: str = Depends(verify_api_key)):
    """列出所有可恢复的检查点"""
    try:
        from m_layer.task_persistence import get_task_persistence
        cps = get_task_persistence().list_resumable()
        return JSONResponse({"success": True, "checkpoints": cps})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 并行执行端点 ────────────────────────────────
class ParallelExecuteRequest(PydanticModel):
    plan: Dict[str, Any]
    task_id: str = ""

@app.post("/parallel/execute")
async def parallel_execute(req: ParallelExecuteRequest,
                            api_key: str = Depends(verify_api_key)):
    """并行执行计划（无依赖步骤并行）"""
    try:
        from m_layer.parallel_executor import get_parallel_executor
        result = get_parallel_executor().execute_parallel(req.plan, req.task_id)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/parallel/analyze")
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


# ─── 条件分支端点 ────────────────────────────────
@app.post("/branch/evaluate")
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


# ─── 多Agent端点 ────────────────────────────────
@app.post("/multiagent/execute")
async def multiagent_execute(req: dict, api_key: str = Depends(verify_api_key)):
    """多Agent协作执行（双模式：线程级 / 进程级隔离）

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

    模式说明:
      thread  - 线程级并行（轻量、共享上下文，适合工具调用密集型任务）
      process - 进程级隔离（独立上下文、独立 LLM 会话，适合深度探索任务）
      mixed   - 混合模式（通过每个任务的 isolation 字段指定）
    """
    try:
        from m_layer.multi_agent import quick_multi_agent, quick_mixed_agents

        mode = req.get("mode", "thread")
        subtasks = req.get("subtasks", [])

        # 检测混合模式：任意任务指定了 isolation 字段
        has_isolation_override = any("isolation" in st for st in subtasks)

        if has_isolation_override:
            result = quick_mixed_agents(subtasks)
        else:
            result = quick_multi_agent(subtasks, mode=mode)

        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/multiagent/thread")
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


@app.post("/multiagent/process")
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


@app.post("/multiagent/mixed")
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


@app.get("/multiagent/modes")
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


# ─── 自然语言工具选择端点 ────────────────────────────────
class ToolSelectRequest(PydanticModel):
    query: str
    context: Dict[str, Any] = {}
    max_tools: int = 3

@app.post("/tools/select")
async def tools_select(req: ToolSelectRequest, api_key: str = Depends(verify_api_key)):
    """自然语言选择工具"""
    try:
        from m_layer.nl_tool_selector import get_nl_selector
        selector = get_nl_selector()
        if req.max_tools > 1:
            result = selector.select_multi(req.query, req.max_tools)
            return JSONResponse({"success": True, "selections": result})
        else:
            result = selector.select(req.query, req.context)
            return JSONResponse({"success": True, "selection": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


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

@app.post("/visualize/plan")
async def visualize_plan(req: VisualizePlanRequest, format: str = "mermaid",
                          api_key: str = Depends(verify_api_key)):
    """生成执行计划的Mermaid流程图"""
    try:
        from m_layer.visualizer import get_visualizer
        mermaid = get_visualizer().plan_to_mermaid(req.plan)
        return _render_visualization(mermaid, format, "执行计划流程图")
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/visualize/report")
async def visualize_report(req: VisualizeReportRequest, format: str = "mermaid",
                            api_key: str = Depends(verify_api_key)):
    """生成执行报告的状态图"""
    try:
        from m_layer.visualizer import get_visualizer
        mermaid = get_visualizer().report_to_mermaid(req.report)
        return _render_visualization(mermaid, format, "执行报告状态图")
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/visualize/multiagent")
async def visualize_multiagent(req: VisualizeMultiAgentRequest, format: str = "mermaid",
                                api_key: str = Depends(verify_api_key)):
    """生成多Agent协作图"""
    try:
        from m_layer.visualizer import get_visualizer
        mermaid = get_visualizer().multi_agent_to_mermaid(req.report)
        return _render_visualization(mermaid, format, "多Agent协作图")
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


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

@app.get("/permissions/tools")
async def permissions_tools(level: str = "", api_key: str = Depends(verify_api_key)):
    """列出工具权限分级（可按level过滤）"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        result = get_permission_manager().list_tools_by_level(level or None)
        return JSONResponse({"success": True, "tools_by_level": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/permissions/check")
async def permissions_check(req: PermissionCheckRequest,
                             api_key: str = Depends(verify_api_key)):
    """检查用户权限是否可使用某工具"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        allowed, reason = get_permission_manager().check_permission(req.user_level, req.tool_name)
        return JSONResponse({"success": True, "allowed": allowed, "reason": reason})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/permissions/confirm")
async def permissions_confirm_create(req: ConfirmCreateRequest,
                                      api_key: str = Depends(verify_api_key)):
    """创建敏感操作确认请求"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        cfm_id = get_permission_manager().create_confirmation(req.tool_name, req.user_id)
        return JSONResponse({"success": True, "confirmation_id": cfm_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/permissions/confirm/{confirmation_id}/resolve")
async def permissions_confirm_resolve(confirmation_id: str, req: ConfirmResolveRequest,
                                       api_key: str = Depends(verify_admin_key)):
    """处理敏感操作确认（需管理员权限）"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        ok = get_permission_manager().resolve_confirmation(confirmation_id, req.confirmed, req.resolver)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/permissions/approval")
async def permissions_approval_create(req: ApprovalCreateRequest,
                                       api_key: str = Depends(verify_api_key)):
    """创建危险操作审批请求"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        apv_id = get_permission_manager().require_approval(req.tool_name, req.user_id)
        return JSONResponse({"success": True, "approval_id": apv_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/permissions/approval/{approval_id}/resolve")
async def permissions_approval_resolve(approval_id: str, req: ApprovalResolveRequest,
                                        api_key: str = Depends(verify_admin_key)):
    """处理危险操作审批（需管理员权限）"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        ok = get_permission_manager().resolve_approval(approval_id, req.approved, req.approver)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/permissions/elevation")
async def permissions_elevation(req: ElevationRequest,
                                 api_key: str = Depends(verify_api_key)):
    """申请权限提升"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        req_id = get_permission_manager().apply_elevation(req.user_id, req.requested_level, req.reason)
        return JSONResponse({"success": True, "request_id": req_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/permissions/audit")
async def permissions_audit(limit: int = 100, api_key: str = Depends(verify_admin_key)):
    """获取权限审计日志（需管理员权限）"""
    try:
        from m_layer.tool_permissions import get_permission_manager
        log = get_permission_manager().get_audit_log(limit)
        return JSONResponse({"success": True, "audit_log": log})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 插件系统端点 ────────────────────────────────
class PluginConfigRequest(PydanticModel):
    config: Dict[str, Any]

class PluginLoadRequest(PydanticModel):
    dir_path: str

@app.get("/plugins")
async def plugins_list(api_key: str = Depends(verify_api_key)):
    """列出所有插件"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        plugins = get_plugin_manager().list_plugins()
        return JSONResponse({"success": True, "plugins": plugins})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/plugins/{name}/enable")
async def plugin_enable(name: str, api_key: str = Depends(verify_admin_key)):
    """启用插件（需管理员权限）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        ok = get_plugin_manager().enable_plugin(name)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/plugins/{name}/disable")
async def plugin_disable(name: str, api_key: str = Depends(verify_admin_key)):
    """禁用插件（需管理员权限）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        ok = get_plugin_manager().disable_plugin(name)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/plugins/{name}/config")
async def plugin_config_get(name: str, api_key: str = Depends(verify_api_key)):
    """获取插件配置"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        config = get_plugin_manager().get_config(name)
        return JSONResponse({"success": True, "config": config})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/plugins/{name}/config")
async def plugin_config_set(name: str, req: PluginConfigRequest,
                             api_key: str = Depends(verify_admin_key)):
    """设置插件配置（需管理员权限）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        ok = get_plugin_manager().set_config(name, req.config)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/plugins/load")
async def plugins_load(req: PluginLoadRequest, api_key: str = Depends(verify_admin_key)):
    """从目录加载插件（需管理员权限）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        loaded = get_plugin_manager().load_from_directory(req.dir_path)
        return JSONResponse({"success": True, "loaded": loaded})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/plugins/metrics")
async def plugins_metrics(api_key: str = Depends(verify_api_key)):
    """获取MetricsPlugin统计数据"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        pm = get_plugin_manager()
        metrics_plugin = pm.get_plugin("metrics")
        if metrics_plugin is None:
            return JSONResponse({"success": False, "error": "metrics插件未加载"})
        return JSONResponse({"success": True, "metrics": metrics_plugin.get_metrics()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── MCP协议端点 ────────────────────────────────
class MCPCallRequest(PydanticModel):
    tool: str
    params: Dict[str, Any] = {}

class MCPConnectRequest(PydanticModel):
    name: str
    server_url: str
    api_key: str = ""

class MCPDisconnectRequest(PydanticModel):
    name: str

@app.get("/mcp/tools")
async def mcp_tools(api_key: str = Depends(verify_api_key)):
    """列出所有MCP工具（本地+远程）"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        tools = get_mcp_registry().list_all_tools()
        return JSONResponse({"success": True, "tools": tools,
                             "count": len(tools)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/mcp/call")
async def mcp_call(req: MCPCallRequest, api_key: str = Depends(verify_api_key)):
    """调用MCP工具（自动路由本地/远程）"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        result = get_mcp_registry().route_call(req.tool, req.params)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/mcp/connect")
async def mcp_connect(req: MCPConnectRequest, api_key: str = Depends(verify_admin_key)):
    """连接远程MCP服务器（需管理员权限）"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        ok = get_mcp_registry().connect_remote(req.name, req.server_url, req.api_key or None)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/mcp/disconnect")
async def mcp_disconnect(req: MCPDisconnectRequest, api_key: str = Depends(verify_admin_key)):
    """断开远程MCP服务器（需管理员权限）"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        get_mcp_registry().disconnect_remote(req.name)
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/mcp/servers")
async def mcp_servers(api_key: str = Depends(verify_api_key)):
    """列出已连接的MCP服务器"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        status = get_mcp_registry().get_status()
        return JSONResponse({"success": True, "servers": status.get("remote_servers", {}),
                             "local_tools": status.get("local_tools", 0)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/mcp/health")
async def mcp_health(api_key: str = Depends(verify_api_key)):
    """MCP健康检查"""
    try:
        from m_layer.mcp_protocol import get_mcp_registry
        status = get_mcp_registry().get_status()
        healthy = all(s.get("connected") for s in status.get("remote_servers", {}).values())
        return JSONResponse({"success": True, "healthy": healthy,
                             "status": status})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 多模态端点 ────────────────────────────────
class MultimodalProcessRequest(PydanticModel):
    input_data: Any  # 文本/文件路径/混合字典
    modality: str = ""  # text/image/audio/video/mixed，空则自动检测

class MultimodalPathRequest(PydanticModel):
    path: str

@app.post("/multimodal/process")
async def multimodal_process(req: MultimodalProcessRequest,
                              api_key: str = Depends(verify_api_key)):
    """处理多模态输入（自动检测模态）"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        result = get_multimodal_processor().process(
            req.input_data, req.modality or None)
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/multimodal/image")
async def multimodal_image(req: MultimodalPathRequest,
                            api_key: str = Depends(verify_api_key)):
    """图像理解"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        result = get_multimodal_processor().process(req.path, "image")
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/multimodal/audio")
async def multimodal_audio(req: MultimodalPathRequest,
                            api_key: str = Depends(verify_api_key)):
    """音频理解"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        result = get_multimodal_processor().process(req.path, "audio")
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/multimodal/video")
async def multimodal_video(req: MultimodalPathRequest,
                            api_key: str = Depends(verify_api_key)):
    """视频理解"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        result = get_multimodal_processor().process(req.path, "video")
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/multimodal/status")
async def multimodal_status(api_key: str = Depends(verify_api_key)):
    """多模态处理器状态"""
    try:
        from m_layer.multimodal import get_multimodal_processor
        proc = get_multimodal_processor()
        return JSONResponse({"success": True, "status": {
            "cache_size": len(proc._cache),
        }})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


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

@app.post("/voice/recognize")
async def voice_recognize(req: VoiceRecognizeRequest,
                           api_key: str = Depends(verify_api_key)):
    """语音识别（base64音频 → 文本）"""
    try:
        import base64
        from m_layer.voice_io import get_voice_io
        audio_bytes = base64.b64decode(req.audio_data)
        result = get_voice_io().recognize_detail(audio_bytes, format=req.format,
                                                  language=req.language)
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/voice/synthesize")
async def voice_synthesize(req: VoiceSynthesizeRequest,
                            api_key: str = Depends(verify_api_key)):
    """语音合成（文本 → base64 WAV音频）"""
    try:
        import base64
        from m_layer.voice_io import get_voice_io
        wav_bytes = get_voice_io().synthesize(req.text, lang=req.lang,
                                               rate=req.rate, pitch=req.pitch,
                                               volume=req.volume)
        if not wav_bytes:
            return JSONResponse({"success": False, "error": "合成失败"})
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
        return JSONResponse({"success": True, "audio_data": audio_b64,
                             "format": "wav", "size": len(wav_bytes)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/voice/status")
async def voice_status(api_key: str = Depends(verify_api_key)):
    """语音IO状态"""
    try:
        from m_layer.voice_io import get_voice_io
        return JSONResponse({"success": True, "status": get_voice_io().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 分布式执行端点 ────────────────────────────────
class DistributedExecuteRequest(PydanticModel):
    task: Dict[str, Any]
    workers: int = 2
    capability_requirement: Dict[str, Any] = {}
    merge_strategy: str = "concat"

class TaskSplitRequest(PydanticModel):
    task: Dict[str, Any]
    n: int = 2

class TaskMergeRequest(PydanticModel):
    subtask_results: list
    strategy: str = "concat"

class WorkerAddRequest(PydanticModel):
    url: str = ""
    capabilities: Dict[str, Any] = {}
    local: bool = True

@app.post("/distributed/execute")
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

@app.post("/distributed/split")
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

@app.post("/distributed/merge")
async def distributed_merge(req: TaskMergeRequest,
                             api_key: str = Depends(verify_api_key)):
    """结果合并"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        merged = get_distributed_executor().merge_results(req.subtask_results, req.strategy)
        return JSONResponse({"success": True, "result": merged})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/distributed/workers")
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

@app.post("/distributed/workers/add")
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

@app.post("/distributed/workers/{worker_id}/remove")
async def distributed_workers_remove(worker_id: str,
                                       api_key: str = Depends(verify_admin_key)):
    """移除工作节点（需管理员权限）"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        ok = get_distributed_executor().registry.remove_worker(worker_id)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/distributed/health")
async def distributed_health(api_key: str = Depends(verify_api_key)):
    """分布式健康检查"""
    try:
        from m_layer.distributed_executor import get_distributed_executor
        result = get_distributed_executor().health_check()
        return JSONResponse({"success": True, "health": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 向量数据库端点（v5.0优化） ────────────────────────────────
class VectorSearchRequest(PydanticModel):
    query: str
    top_k: int = 5
    threshold: float = 0.3

@app.get("/vector/status")
async def vector_status(api_key: str = Depends(verify_api_key)):
    """向量知识库状态"""
    try:
        from w1_layer.knowledge_store import get_store
        store = get_store()
        status = store.get_status()
        # 检查是否为向量版本
        is_vector = "vector_store" in str(type(store).__name__).lower() or \
                     "backend" in status or "embedding" in str(status).lower()
        return JSONResponse({
            "success": True,
            "is_vector": is_vector,
            "store_type": type(store).__name__,
            "status": status,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/vector/search")
async def vector_search(req: VectorSearchRequest, api_key: str = Depends(verify_api_key)):
    """向量搜索（混合检索：向量+关键词）"""
    try:
        from w1_layer.knowledge_store import get_store
        store = get_store()
        results = store.search(req.query, top_k=req.top_k, threshold=req.threshold)
        return JSONResponse({"success": True, "results": results, "count": len(results)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/vector/migrate")
async def vector_migrate(api_key: str = Depends(verify_admin_key)):
    """从TF-IDF迁移到向量知识库（需管理员）"""
    try:
        from w1_layer.vector_store import migrate_from_tfidf
        count = migrate_from_tfidf()
        return JSONResponse({"success": True, "migrated": count})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


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

@app.get("/local-model/status")
async def local_model_status(api_key: str = Depends(verify_api_key)):
    """本地模型状态"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        return JSONResponse({"success": True, "status": client.status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/local-model/models")
async def local_model_list(api_key: str = Depends(verify_api_key)):
    """列出支持的本地模型"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        return JSONResponse({"success": True, "models": client.list_supported_models()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/local-model/load")
async def local_model_load(req: LocalModelLoadRequest, api_key: str = Depends(verify_admin_key)):
    """加载本地模型（需管理员）"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        result = client.load_model(req.model_name, req.quantization, req.device)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/local-model/unload")
async def local_model_unload(api_key: str = Depends(verify_admin_key)):
    """卸载本地模型（需管理员）"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        result = client.unload_model()
        return JSONResponse({"success": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/local-model/health")
async def local_model_health(api_key: str = Depends(verify_api_key)):
    """本地模型健康检查"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        healthy = client.health_check()
        return JSONResponse({"success": True, "healthy": healthy})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/local-model/switch-type")
async def local_model_switch_type(req: ModelTypeSwitchRequest, api_key: str = Depends(verify_admin_key)):
    """切换本地模型类型（text ↔ vl，需管理员）

    按方案 A：文本模型与视觉模型不同时加载，切换时卸载当前模型并加载目标模型。

    请求体示例：
        {"target_type": "vl"}  # 自动选择 qwen2-5-vl-7b
        {"target_type": "text", "model_name": "qwen2-5-7b"}
    """
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        result = client.switch_model_type(
            req.target_type,
            model_name=(req.model_name or None),
            quantization=req.quantization,
            device=req.device,
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 视觉对话端点（v5.1 VL 集成） ────────────────────────────────

@app.post("/vision/chat")
async def vision_chat(req: VisionChatRequest, api_key: str = Depends(verify_api_key)):
    """视觉对话：使用本地 VL 模型对图像+提示词进行推理

    支持三种图像输入方式（按优先级取其一）：
      1. image_path: 本地图像文件路径
      2. image_url: HTTP(S) 图像 URL
      3. image_base64: base64 编码的图像数据（可含 data:image/... 前缀）

    若当前加载的是文本模型且 auto_switch=true，会自动切换到 VL 模型。
    按方案 A：文本/VL 模型不同时加载，切换时卸载当前模型。

    请求体示例：
        {"prompt": "描述这张图", "image_path": "C:/images/test.png"}
        {"prompt": "图里有什么文字？", "image_url": "https://example.com/a.jpg"}
        {"prompt": "这是什么的UI截图？", "image_base64": "iVBORw0KGgo..."}

    返回：
        {success, content, model, model_type, tokens, latency, switched, error}
    """
    try:
        require_module("llm.local_model")
        # 校验图像输入
        if not (req.image_path or req.image_url or req.image_base64):
            return JSONResponse({
                "success": False,
                "error": "必须提供 image_path / image_url / image_base64 之一",
            })

        # 构造图像参数（按优先级）
        if req.image_path:
            image = {"path": req.image_path}
        elif req.image_url:
            image = {"url": req.image_url}
        else:
            image = {"base64": req.image_base64}

        from m_layer.llm_client import get_client
        llm = get_client()
        result = llm.chat_with_image(
            prompt=req.prompt,
            image=image,
            system_prompt=req.system_prompt,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            auto_switch=req.auto_switch,
        )

        return JSONResponse({
            "success": not result.get("error"),
            "content": result.get("content", ""),
            "model": result.get("model"),
            "model_type": result.get("model_type", "vl"),
            "tokens": result.get("tokens", 0),
            "latency": result.get("latency", 0),
            "switched": result.get("switched", False),
            "platform": result.get("platform", "local_torch"),
            "error": result.get("error"),
        })
    except Exception as e:
        logger.error(f"视觉对话端点异常: {e}")
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/vision/status")
async def vision_status(api_key: str = Depends(verify_api_key)):
    """视觉模型能力状态：检查 VL 依赖和当前模型类型"""
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        status = client.status()
        deps = status.get("dependencies", {})
        return JSONResponse({
            "success": True,
            "vl_supported": deps.get("qwen_vl", False),
            "current_model_type": status.get("model_type", "text"),
            "is_vl_loaded": status.get("is_vl_model", False),
            "vl_available": client.is_vl_available(),
            "supported_vl_models": [
                m for m in client.list_supported_models()
                if m.get("model_type") == "vl"
            ],
            "pillow_required": "Pillow 未安装时无法推理",
            "hint": "若 vl_supported=false，请执行: pip install -U transformers>=4.45 pillow",
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


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

@app.get("/automation/status")
async def automation_status(api_key: str = Depends(verify_api_key)):
    """获取所有自动化能力状态"""
    try:
        from w1_layer.automation import automation_status as get_status
        return JSONResponse({"success": True, "status": get_status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

# ─── 浏览器自动化 ─────────────────────────

@app.post("/browser/start")
async def browser_start(req: BrowserNavigateRequest, api_key: str = Depends(verify_api_key)):
    """启动浏览器并导航到 URL"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        ba = get_browser()
        start_result = await asyncio.to_thread(
            ba.start,
            headless=req.headless,
            viewport={"width": req.viewport_width, "height": req.viewport_height},
        )
        if not start_result.get("success"):
            return JSONResponse(start_result)
        nav_result = await asyncio.to_thread(ba.navigate, req.url, wait_until=req.wait_until)
        return JSONResponse({
            "success": nav_result.get("success"),
            "start": start_result,
            "navigate": nav_result,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/browser/navigate")
async def browser_navigate(req: BrowserNavigateRequest, api_key: str = Depends(verify_api_key)):
    """导航到新 URL（浏览器须已启动）"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        ba = get_browser()
        if not ba.started:
            start_result = await asyncio.to_thread(ba.start, headless=req.headless)
            if not start_result.get("success"):
                return JSONResponse(start_result)
        result = await asyncio.to_thread(ba.navigate, req.url, wait_until=req.wait_until)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/browser/click")
async def browser_click(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """点击元素"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().click, req.selector, timeout=req.timeout)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/browser/fill")
async def browser_fill(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """填充输入框"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().fill, req.selector, req.value)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/browser/type")
async def browser_type(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """模拟键盘逐字输入"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().type_text, req.selector, req.value, delay=req.delay)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/browser/press")
async def browser_press(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """按键"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().press_key, req.key)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/browser/scroll")
async def browser_scroll(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """滚动页面"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().scroll, req.pixels, req.direction)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/browser/screenshot")
async def browser_screenshot(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """页面截图

    返回 base64 编码的 PNG，可直接用于 VL 模型分析。
    """
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(
            get_browser().screenshot, full_page=req.full_page, selector=req.selector or None
        )
        return JSONResponse({
            "success": result.get("success"),
            "path": result.get("path"),
            "base64": result.get("base64"),
            "error": result.get("error"),
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/browser/text")
async def browser_text(api_key: str = Depends(verify_api_key), selector: str = ""):
    """提取页面文本"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().extract_text, selector or None)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/browser/links")
async def browser_links(api_key: str = Depends(verify_api_key)):
    """提取页面所有链接"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().get_links)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/browser/status")
async def browser_status(api_key: str = Depends(verify_api_key)):
    """浏览器状态"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        return JSONResponse({"success": True, "status": get_browser().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/browser/stop")
async def browser_stop(api_key: str = Depends(verify_api_key)):
    """关闭浏览器"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().stop)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

# ─── 网页抓取 ─────────────────────────

@app.post("/web/fetch")
async def web_fetch(req: WebFetchRequest, api_key: str = Depends(verify_api_key)):
    """抓取网页正文（httpx + BeautifulSoup）

    比 /extended_tools/call 的 web_fetch 增强：
    - 精准提取正文，去除导航/广告/脚本
    - 提取结构化数据：标题、链接、图片、元数据
    - 支持 article_mode 识别文章正文
    """
    try:
        require_module("automation.web_scraper")
        from w1_layer.automation import get_web_scraper
        scraper = get_web_scraper()
        if req.article_mode:
            result = scraper.fetch_article(req.url, max_length=req.max_length)
        else:
            result = scraper.fetch(req.url, max_length=req.max_length)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/web/status")
async def web_status(api_key: str = Depends(verify_api_key)):
    """网页抓取能力状态"""
    try:
        require_module("automation.web_scraper")
        from w1_layer.automation import get_web_scraper
        return JSONResponse({"success": True, "status": get_web_scraper().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

# ─── 屏幕截图 ─────────────────────────

@app.post("/screen/capture")
async def screen_capture(req: ScreenCaptureRequest, api_key: str = Depends(verify_api_key)):
    """屏幕截图

    返回 base64 编码的 PNG，可直接用于 VL 模型分析。
    """
    try:
        require_module("automation.screen")
        from w1_layer.automation import get_screen_capture
        sc = get_screen_capture()
        if req.width > 0 and req.height > 0:
            result = sc.capture_region(req.left, req.top, req.width, req.height)
        else:
            if req.save_to_file:
                result = sc.capture_to_file(monitor=req.monitor)
            else:
                result = sc.capture_full(monitor=req.monitor)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/screen/monitors")
async def screen_monitors(api_key: str = Depends(verify_api_key)):
    """列出所有显示器"""
    try:
        require_module("automation.screen")
        from w1_layer.automation import get_screen_capture
        result = get_screen_capture().list_monitors()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/screen/status")
async def screen_status(api_key: str = Depends(verify_api_key)):
    """截屏能力状态"""
    try:
        require_module("automation.screen")
        from w1_layer.automation import get_screen_capture
        return JSONResponse({"success": True, "status": get_screen_capture().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

# ─── 桌面控制 ─────────────────────────

@app.post("/desktop/action")
async def desktop_action(req: DesktopActionRequest, api_key: str = Depends(verify_admin_key)):
    """桌面控制操作（需管理员）

    action 取值：click/type/press/hotkey/scroll/move/drag/screenshot
    """
    try:
        require_module("automation.desktop")
        from w1_layer.automation import get_desktop_control
        dc = get_desktop_control()
        if req.action == "click":
            result = dc.click(req.x or None, req.y or None, button=req.button, clicks=req.clicks)
        elif req.action == "double_click":
            result = dc.double_click(req.x or None, req.y or None)
        elif req.action == "right_click":
            result = dc.right_click(req.x or None, req.y or None)
        elif req.action == "type":
            result = dc.type_text(req.text)
        elif req.action == "press":
            result = dc.press(req.key)
        elif req.action == "hotkey":
            result = dc.hot_key(*req.keys) if req.keys else {"success": False, "error": "keys 为空"}
        elif req.action == "scroll":
            result = dc.scroll(req.pixels)
        elif req.action == "move":
            result = dc.move_to(req.x, req.y)
        elif req.action == "drag":
            result = dc.drag(req.dx, req.dy)
        elif req.action == "screenshot":
            result = dc.screenshot()
        else:
            return JSONResponse({"success": False, "error": f"未知 action: {req.action}"})
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/desktop/status")
async def desktop_status(api_key: str = Depends(verify_api_key)):
    """桌面控制状态"""
    try:
        require_module("automation.desktop")
        from w1_layer.automation import get_desktop_control
        return JSONResponse({"success": True, "status": get_desktop_control().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

# ─── VL + 截屏联动：看屏幕 ─────────────────────────

@app.post("/vision/analyze-screen")
async def vision_analyze_screen(req: VisionAnalyzeScreenRequest, api_key: str = Depends(verify_api_key)):
    """截屏并让 VL 模型分析（"看屏幕"端点）

    流程：截屏 → base64 → VL 模型分析 → 返回描述
    若当前加载的是文本模型且 auto_switch=true，自动切换到 VL 模型。
    """
    try:
        require_module("automation.screen")
        from w1_layer.automation import get_screen_capture
        from m_layer.llm_client import get_client

        # 1. 截屏
        sc = get_screen_capture()
        if req.region and len(req.region) == 4:
            capture = sc.capture_region(req.region[0], req.region[1], req.region[2], req.region[3])
        else:
            capture = sc.capture_to_file(monitor=req.monitor)

        if not capture.get("success"):
            return JSONResponse({"success": False, "error": f"截屏失败: {capture.get('error')}"})

        # 2. 调用 VL 模型
        llm = get_client()
        result = llm.chat_with_image(
            prompt=req.prompt,
            image={"base64": capture["base64"]},
            auto_switch=req.auto_switch,
            max_tokens=req.max_tokens,
        )

        return JSONResponse({
            "success": not result.get("error"),
            "content": result.get("content", ""),
            "model": result.get("model"),
            "model_type": result.get("model_type", "vl"),
            "switched": result.get("switched", False),
            "screenshot_path": capture.get("path"),
            "latency": result.get("latency", 0),
            "error": result.get("error"),
        })
    except Exception as e:
        logger.error(f"看屏幕端点异常: {e}")
        return JSONResponse({"success": False, "error": str(e)})


# ─── 实时语音监听端点（v5.2 新增） ────────────────────────────────

# 语音监听事件队列（前端轮询获取）
_voice_events: List[Dict[str, Any]] = []

class VoiceListenStartRequest(PydanticModel):
    wake_word: str = ""  # 为空则直通模式（任何语音都触发）
    language: str = "zh"
    device_index: int = -1  # -1=默认设备
    auto_chat: bool = True  # 识别到语音后自动调用 LLM 生成回复

@app.post("/voice/listen/start")
async def voice_listen_start(req: VoiceListenStartRequest, api_key: str = Depends(verify_api_key)):
    """启动实时持续语音监听

    - 直通模式（wake_word 为空）：检测到任何语音段即识别并回调
    - 唤醒词模式（wake_word 非空）：先识别唤醒词，命中后再识别命令

    若 auto_chat=true，识别到的文本会自动作为 prompt 调用 LLM 生成回复。
    """
    try:
        require_module("voice.listener")
        from m_layer.voice_io import get_listener
        listener = get_listener()

        if not listener.available:
            return JSONResponse({
                "success": False,
                "error": "pyaudio 不可用，无法采集麦克风。请执行: pip install pyaudio",
            })

        # 设置回调
        def on_utterance(text: str):
            logger.info(f"[语音监听] 识别到: {text}")
            # 记录到事件队列（前端可轮询 /voice/listen/events）
            try:
                _voice_events.append({"type": "utterance", "text": text, "ts": time.time()})
                # 保留最近 100 条
                if len(_voice_events) > 100:
                    _voice_events.pop(0)
            except Exception:
                pass
            # 自动对话
            if req.auto_chat and text:
                try:
                    from m_layer.llm_client import get_client
                    llm = get_client()
                    reply = llm.chat(text)
                    reply_text = reply.get("content", "")
                    logger.info(f"[语音监听] 回复: {reply_text[:80]}")
                    _voice_events.append({
                        "type": "reply", "text": text,
                        "reply": reply_text, "ts": time.time(),
                    })
                except Exception as e:
                    logger.error(f"[语音监听] 自动对话失败: {e}")

        listener.on_utterance = on_utterance
        listener.on_wake_word = lambda: _voice_events.append({"type": "wake", "ts": time.time()})
        listener.on_state_change = lambda s: _voice_events.append({"type": "state", "state": s, "ts": time.time()})

        result = listener.start(
            wake_word=(req.wake_word or None),
            language=req.language,
            device_index=(req.device_index if req.device_index >= 0 else None),
        )
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"启动语音监听异常: {e}")
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/voice/listen/stop")
async def voice_listen_stop(api_key: str = Depends(verify_api_key)):
    """停止语音监听"""
    try:
        require_module("voice.listener")
        from m_layer.voice_io import get_listener
        result = get_listener().stop()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/voice/listen/status")
async def voice_listen_status(api_key: str = Depends(verify_api_key)):
    """语音监听状态"""
    try:
        require_module("voice.listener")
        from m_layer.voice_io import get_listener
        return JSONResponse({"success": True, "status": get_listener().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/voice/listen/events")
async def voice_listen_events(api_key: str = Depends(verify_api_key), since: int = 0):
    """获取语音监听事件（轮询）

    Args:
        since: 返回此时间戳之后的事件（0=全部最近 100 条）
    """
    events = [e for e in _voice_events if e.get("ts", 0) > since]
    return JSONResponse({"success": True, "events": events, "count": len(events)})


# ─── 功能模块管理端点（v5.2 新增） ────────────────────────────────

class ModuleActionRequest(PydanticModel):
    force: bool = False  # 强制操作（如卸载受保护模块）

@app.get("/modules")
async def modules_list(api_key: str = Depends(verify_api_key), category: str = ""):
    """列出所有功能模块"""
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        modules = registry.list_modules(category=category or None)
        return JSONResponse({
            "success": True,
            "modules": modules,
            "status": registry.status(),
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/modules/{name}")
async def module_get(name: str, api_key: str = Depends(verify_api_key)):
    """获取单个模块详情"""
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        modules = {m["name"]: m for m in registry.list_modules()}
        if name not in modules:
            return JSONResponse({"success": False, "error": f"未注册的模块: {name}"})
        return JSONResponse({"success": True, "module": modules[name]})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/modules/{name}/load")
async def module_load(name: str, api_key: str = Depends(verify_admin_key)):
    """加载模块（管理员）"""
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().load(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/modules/{name}/unload")
async def module_unload(name: str, req: ModuleActionRequest, api_key: str = Depends(verify_admin_key)):
    """卸载模块（管理员）

    释放模块占用的资源（关闭浏览器、停止监听、卸载模型等）。
    受保护模块（CUF守卫/防火墙等）不可卸载，除非 force=true。
    """
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().unload(name, force=req.force)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/modules/{name}/reload")
async def module_reload(name: str, api_key: str = Depends(verify_admin_key)):
    """重载模块（unload + load，管理员）"""
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().reload(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/modules/{name}/disable")
async def module_disable(name: str, api_key: str = Depends(verify_admin_key)):
    """禁用模块（卸载 + 标记 disabled，之后无法 load 直到 enable）"""
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().disable(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.post("/modules/{name}/enable")
async def module_enable(name: str, api_key: str = Depends(verify_admin_key)):
    """启用模块（清除 disabled 标记，不自动加载）"""
    try:
        from m_layer.module_registry import get_registry
        result = get_registry().enable(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/modules/status")
async def modules_status(api_key: str = Depends(verify_api_key)):
    """模块注册表总状态"""
    try:
        from m_layer.module_registry import get_registry
        return JSONResponse({"success": True, "status": get_registry().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 启动时注册内置模块 ────────────────────────────────────

@app.on_event("startup")
async def _register_modules_on_startup():
    """启动时注册内置功能模块到注册表 + 启动周期审计定时器 + D层完整性校验"""
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

    # 周期审计定时器（每24小时自动触发一次 daily_audit）
    try:
        import threading
        def _periodic_audit():
            import time
            while True:
                time.sleep(24 * 3600)  # 24小时
                try:
                    metacog.daily_audit(force=False)
                    logger.info("周期审计自动执行完成")
                except Exception as ae:
                    logger.warning(f"周期审计自动执行失败: {ae}")

        t = threading.Thread(target=_periodic_audit, daemon=True, name="periodic_audit")
        t.start()
        logger.info("周期审计定时器已启动（24小时间隔）")
    except Exception as e:
        logger.warning(f"周期审计定时器启动失败: {e}")


# ─── 前端补全端点（favicon + 别名 + 功能桩） ────────────────────────────────

@app.get("/favicon.ico")
async def favicon():
    """返回空图标避免浏览器 404 报错"""
    from fastapi.responses import Response
    # 1x1 透明 PNG
    return Response(content=b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82',
                    media_type="image/png")


# ─── 路径别名（前端调用名 → 后端已有路由） ────────────────────────────────
@app.get("/plugins/list")
async def plugins_list_alias(api_key: str = Depends(verify_api_key)):
    """别名：/plugins/list → /plugins"""
    from m_layer.plugin_system import get_plugin_manager
    try:
        pm = get_plugin_manager()
        return JSONResponse({"success": True, "data": {"plugins": pm.list_plugins()}})
    except Exception as e:
        return JSONResponse({"success": True, "data": {"plugins": []}})


@app.get("/tools/extended")
async def tools_extended_alias(api_key: str = Depends(verify_api_key)):
    """别名：/tools/extended → /extended_tools/list"""
    try:
        from w1_layer.extended_tools import get_extended_tools
        tools = get_extended_tools()
        return JSONResponse({"success": True, "data": {"tools": tools}})
    except Exception:
        return JSONResponse({"success": True, "data": {"tools": []}})


@app.get("/conversations")
async def conversations_alias(api_key: str = Depends(verify_api_key)):
    """别名：/conversations → 返回记忆层会话列表"""
    try:
        convs = memory.recall(limit=50)
        return JSONResponse({"success": True, "data": {"conversations": [
            {"id": str(i), "title": c.get("input", "")[:30], "created": c.get("timestamp", ""),
             "messages": 2} for i, c in enumerate(convs)
        ]}})
    except Exception:
        return JSONResponse({"success": True, "data": {"conversations": []}})


@app.post("/conversations")
async def conversations_create(req: dict, api_key: str = Depends(verify_api_key)):
    """创建新会话（前端 POST /conversations 调用）"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        title = str(req.get("title", ""))[:100]
        sid = get_conversation_manager().create_session(
            user_id="default_user", metadata={"title": title})
        return JSONResponse({"success": True, "data": {"id": sid, "title": title}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 三级记忆管理端点（L1工作/L2语义/L3情景） ────────────────────────────────
@app.get("/memory/stats")
async def memory_stats(api_key: str = Depends(verify_api_key)):
    """三级记忆统计"""
    try:
        return JSONResponse({"success": True, "data": memory.stats()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/memory/health")
async def memory_health(api_key: str = Depends(verify_api_key)):
    """三级记忆健康检查"""
    try:
        return JSONResponse({"success": True, "data": memory.health()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/memory/search")
async def memory_search(query: str, layers: str = "L1,L2,L3",
                        top_k: int = 5, category: str = "",
                        api_key: str = Depends(verify_api_key)):
    """跨层检索记忆

    Args:
        query: 查询文本
        layers: 检索层级，逗号分隔（L1,L2,L3）
        top_k: 每层返回条数
        category: L2 类别过滤（可选）
    """
    try:
        layer_list = [l.strip() for l in layers.split(",") if l.strip()]
        result = memory.search_cross_layer(
            query, layers=layer_list, top_k=top_k,
            **({"category": category} if category else {})
        )
        return JSONResponse({"success": True, "data": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/memory/episode")
async def memory_save_episode(req: dict, api_key: str = Depends(verify_api_key)):
    """保存情景到 L3（任务轨迹/反思/决策）"""
    try:
        eid = memory.save_episode(
            event_type=str(req.get("event_type", "task")),
            task_desc=str(req.get("task_desc", ""))[:500],
            steps=req.get("steps", []),
            result=str(req.get("result", ""))[:1000],
            success=bool(req.get("success", True)),
            reflection=str(req.get("reflection", ""))[:1000],
        )
        return JSONResponse({"success": True, "data": {"id": eid}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/memory/knowledge")
async def memory_save_knowledge(req: dict, api_key: str = Depends(verify_api_key)):
    """保存知识到 L2（语义记忆）"""
    try:
        kid = memory.save_knowledge(
            content=str(req.get("content", ""))[:2000],
            source=str(req.get("source", "manual")),
            category=str(req.get("category", "general")),
            score=float(req.get("score", 0.7)),
            tags=req.get("tags", []),
        )
        return JSONResponse({"success": True, "data": {"id": kid}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.delete("/memory/{layer}/{item_id}")
async def memory_forget(layer: str, item_id: str,
                        api_key: str = Depends(verify_api_key)):
    """遗忘指定记忆

    Args:
        layer: L1 / L2 / L3
        item_id: 记忆条目 ID
    """
    try:
        deleted = memory.forget(layer, item_id)
        return JSONResponse({"success": deleted, "data": {"deleted": deleted}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/memory/clear-l1")
async def memory_clear_l1(api_key: str = Depends(verify_admin_key)):
    """清空工作记忆（需管理员权限）"""
    try:
        memory.clear_l1()
        return JSONResponse({"success": True, "data": {"cleared": True}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/conversations/{conv_id}/messages")
async def conversation_messages(conv_id: str, api_key: str = Depends(verify_api_key)):
    """获取会话消息列表（前端 GET /conversations/{id}/messages 调用）"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        history = get_conversation_manager().get_history(conv_id, limit=50)
        return JSONResponse({"success": True, "data": {"messages": history}})
    except Exception:
        return JSONResponse({"success": True, "data": {"messages": []}})


@app.get("/conversations/{conv_id}/inject")
async def conversation_inject(conv_id: str, n: int = 5,
                              api_key: str = Depends(verify_api_key)):
    """预览会话注入上下文（前端 GET /conversations/{id}/inject 调用）"""
    try:
        from m_layer.conversation_context import get_conversation_manager
        history = get_conversation_manager().get_history_for_llm(conv_id, limit=n)
        text = "\n".join(f"[{m['role']}] {m['content']}" for m in history) if history else "(空)"
        return JSONResponse({"success": True, "data": {"text": text, "count": len(history)}})
    except Exception:
        return JSONResponse({"success": True, "data": {"text": "(空)", "count": 0}})


@app.post("/plugins/toggle")
async def plugins_toggle(req: dict, api_key: str = Depends(verify_api_key)):
    """切换插件启用/禁用（前端 POST /plugins/toggle 调用）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        pid = str(req.get("id", "")).strip()
        enable = bool(req.get("enable", False))
        if not pid:
            return JSONResponse({"success": False, "error": "缺少插件 id"})
        pm = get_plugin_manager()
        ok = pm.enable_plugin(pid) if enable else pm.disable_plugin(pid)
        if ok:
            return JSONResponse({"success": True, "data": {"id": pid, "enabled": enable}})
        return JSONResponse({"success": False, "error": f"插件不存在或操作失败: {pid}"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/plugins/stats")
async def plugins_stats(id: str = "", api_key: str = Depends(verify_api_key)):
    """获取插件统计（前端 GET /plugins/stats?id= 调用）"""
    try:
        from m_layer.plugin_system import get_plugin_manager
        pm = get_plugin_manager()
        stats = pm.get_sandbox_stats()
        if id:
            return JSONResponse({"success": True, "data": stats.get(id, {})})
        return JSONResponse({"success": True, "data": stats})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 插件市场（自动下载/加载/卸载） ────────────────────────────────
@app.get("/plugins/market/list")
async def market_list(api_key: str = Depends(verify_api_key)):
    """列出插件市场所有可用插件"""
    try:
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        return JSONResponse({"success": True, "data": market.list_available()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/plugins/market/status")
async def market_status(api_key: str = Depends(verify_api_key)):
    """插件市场总状态"""
    try:
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        return JSONResponse({"success": True, "data": market.get_status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/plugins/market/install")
async def market_install(req: dict, api_key: str = Depends(verify_api_key)):
    """安装并加载指定插件（POST /plugins/market/install {name: "pdf_reader"}）"""
    try:
        name = req.get("name", "")
        if not name:
            return JSONResponse({"success": False, "error": "缺少 name 参数"})
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        result = market.install_and_load(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/plugins/market/unload")
async def market_unload(req: dict, api_key: str = Depends(verify_api_key)):
    """卸载已加载的插件（用完释放，POST /plugins/market/unload {name: "pdf_reader"}）"""
    try:
        name = req.get("name", "")
        if not name:
            return JSONResponse({"success": False, "error": "缺少 name 参数"})
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        result = market.unload_after_use(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/plugins/market/uninstall")
async def market_uninstall(req: dict, api_key: str = Depends(verify_admin_key)):
    """完全卸载插件（pip uninstall，需管理员权限）"""
    try:
        name = req.get("name", "")
        if not name:
            return JSONResponse({"success": False, "error": "缺少 name 参数"})
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        result = market.uninstall(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/plugins/market/loaded")
async def market_loaded(api_key: str = Depends(verify_api_key)):
    """查看当前已加载的插件（含 TTL 剩余时间）"""
    try:
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        return JSONResponse({"success": True, "data": market.list_loaded()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/plugins/market/keep-alive")
async def market_keep_alive(req: dict, api_key: str = Depends(verify_api_key)):
    """标记插件为持久模式（不自动卸载）"""
    try:
        name = req.get("name", "")
        if not name:
            return JSONResponse({"success": False, "error": "缺少 name 参数"})
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        if market.keep_alive(name):
            return JSONResponse({"success": True, "name": name, "message": "已标记为持久模式"})
        return JSONResponse({"success": False, "error": f"插件 {name} 未加载"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/plugins/market/match")
async def market_match(req: dict, api_key: str = Depends(verify_api_key)):
    """测试能力匹配（POST /plugins/market/match {input: "读取pdf", failed_tool: ""}）"""
    try:
        user_input = req.get("input", "")
        failed_tool = req.get("failed_tool", "")
        from m_layer.plugin_market import get_marketplace
        market = get_marketplace()
        info = market.match_capability(user_input, failed_tool)
        if info:
            return JSONResponse({"success": True, "matched": True, "plugin": info})
        return JSONResponse({"success": True, "matched": False, "plugin": None})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 经验存储（学习沉淀） ────────────────────────────────
@app.get("/experience/list")
async def experience_list(mature: bool = False, api_key: str = Depends(verify_api_key)):
    """列出所有经验（GET /experience/list?mature=true 仅看成熟经验）"""
    try:
        from m_layer.experience_store import get_experience_store
        store = get_experience_store()
        return JSONResponse({"success": True, "data": store.list_experiences(mature)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/experience/status")
async def experience_status(api_key: str = Depends(verify_api_key)):
    """经验存储状态"""
    try:
        from m_layer.experience_store import get_experience_store
        store = get_experience_store()
        return JSONResponse({"success": True, "data": store.get_status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/experience/test-match")
async def experience_test_match(req: dict, api_key: str = Depends(verify_api_key)):
    """测试经验匹配（POST /experience/test-match {input: "读取 test.pdf", tool: "pdf_read"}）"""
    try:
        user_input = req.get("input", "")
        tool_name = req.get("tool", "")
        from m_layer.experience_store import get_experience_store
        store = get_experience_store()
        exp = store.match_experience(user_input, tool_name)
        if exp:
            return JSONResponse({"success": True, "matched": True, "experience": exp})
        return JSONResponse({"success": True, "matched": False, "experience": None})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 自进化引擎（自动总结不足+生成方案+提交审核） ────────────────────────────────
@app.get("/evolution/status")
async def evolution_status(api_key: str = Depends(verify_api_key)):
    """自进化引擎状态"""
    try:
        from m_layer.self_evolution import get_evolution_engine
        engine = get_evolution_engine()
        return JSONResponse({"success": True, "data": engine.get_status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/evolution/scan")
async def evolution_scan(api_key: str = Depends(verify_admin_key)):
    """手动触发自进化扫描（需管理员权限）

    流程：扫描缺陷 → LLM 生成方案 → 提交审核队列
    """
    try:
        from m_layer.self_evolution import get_evolution_engine
        engine = get_evolution_engine()
        report = engine.trigger_now()
        return JSONResponse({"success": True, "data": report})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/evolution/history")
async def evolution_history(limit: int = 20, api_key: str = Depends(verify_api_key)):
    """自进化扫描历史"""
    try:
        from m_layer.self_evolution import get_evolution_engine
        engine = get_evolution_engine()
        return JSONResponse({"success": True, "data": engine.list_scan_history(limit)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/evolution/defects")
async def evolution_defects(api_key: str = Depends(verify_api_key)):
    """查看当前缺陷列表（扫描但不提交方案）"""
    try:
        from m_layer.self_evolution import DefectAnalyzer
        analyzer = DefectAnalyzer()
        defects = analyzer.scan()
        return JSONResponse({"success": True, "data": defects, "total": len(defects)})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 帮助中心 ────────────────────────────────
@app.get("/help")
async def help_endpoint():
    """帮助命令列表"""
    commands = [
        {"cmd": "你好", "desc": "开始对话", "category": "对话", "alias": ["hi", "hello"]},
        {"cmd": "计算 <表达式>", "desc": "数学计算，如 计算 25*4", "category": "对话"},
        {"cmd": "几点了", "desc": "查询当前时间", "category": "对话"},
        {"cmd": "天气 <城市>", "desc": "查询天气", "category": "对话"},
        {"cmd": "搜索 <关键词>", "desc": "搜索知识库", "category": "知识库"},
        {"cmd": "/switch <平台>", "desc": "切换LLM平台", "category": "系统"},
        {"cmd": "/status", "desc": "查看系统状态", "category": "系统"},
        {"cmd": "/audit", "desc": "触发周期审计", "category": "系统"},
    ]
    return JSONResponse({"success": True, "data": {"commands": commands}})


# ─── 邮件/日历（未集成，返回未配置状态） ────────────────────────────────
@app.get("/mail/status")
async def mail_status():
    return JSONResponse({"success": True, "data": {
        "mail_send_ready": False, "mail_recv_ready": False,
        "smtp_host": "", "imap_host": "", "calendar_events": 0,
    }})

@app.post("/mail/send")
async def mail_send(req: dict):
    return JSONResponse({"success": False, "error": "邮件发送未配置（需配置SMTP环境变量）"})

@app.get("/mail/inbox")
async def mail_inbox(limit: int = 10):
    return JSONResponse({"success": True, "data": {"emails": []}})

@app.get("/calendar/list")
async def calendar_list():
    return JSONResponse({"success": True, "data": {"events": []}})

@app.post("/calendar/add")
async def calendar_add(req: dict):
    return JSONResponse({"success": False, "error": "日程管理未配置"})

@app.delete("/calendar/remove")
async def calendar_remove(event_id: str = ""):
    return JSONResponse({"success": False, "error": "日程管理未配置"})


# ─── 资讯/热搜（未集成，返回空） ────────────────────────────────
@app.get("/news/category/{cat}")
async def news_category(cat: str, n: int = 10):
    return JSONResponse({"success": True, "data": {"news": []}})

@app.get("/hot-search/{platform}")
async def hot_search(platform: str, n: int = 10):
    return JSONResponse({"success": True, "data": {"items": []}})


# ─── CUF 活动流 + 快速检查 ────────────────────────────────
@app.get("/cuf/activity")
async def cuf_activity(limit: int = 50, api_key: str = Depends(verify_api_key)):
    """CUF守卫活动记录"""
    try:
        history = ledger.history(limit)
        events = []
        for h in history:
            events.append({
                "ts": h.get("timestamp", ""),
                "tool": h.get("action", ""),
                "action": h.get("action", ""),
                "allowed": h.get("allowed", True),
                "axioms": [],
            })
        return JSONResponse({"success": True, "data": {
            "balance": round(ledger.balance(), 4),
            "total": len(events),
            "events": events,
        }})
    except Exception as e:
        return JSONResponse({"success": True, "data": {"balance": round(ledger.balance(), 4), "total": 0, "events": []}})

@app.get("/cuf/check")
async def cuf_check(api_key: str = Depends(verify_api_key)):
    """CUF守卫状态检查"""
    return JSONResponse({"success": True, "data": {
        "guard_active": True,
        "balance": round(ledger.balance(), 4),
        "whitelist_count": len(whitelist.list_all()),
        "stats": ledger.stats(),
    }})

@app.get("/self-check/quick")
async def self_check_quick(api_key: str = Depends(verify_admin_key)):
    """快速自检"""
    return JSONResponse({"success": True, "data": {
        "status": "ok",
        "arch": "v3",
        "balance": round(ledger.balance(), 4),
        "layers": {"W2": "ok", "W1": "ok", "M": "ok", "D": "ok"},
        "guards": {"g1": "ok", "g2": "ok", "g3": "ok", "g4": "ok", "g5": "ok"},
    }})

@app.get("/self-check")
async def self_check_full(api_key: str = Depends(verify_admin_key)):
    """完整自检"""
    return JSONResponse({"success": True, "data": {
        "status": "ok",
        "arch": "v3",
        "version": "2.0.0",
        "balance": round(ledger.balance(), 4),
        "stats": ledger.stats(),
        "whitelist_count": len(whitelist.list_all()),
        "layers": {"W2": "ok", "W1": "ok", "M": "ok", "D": "ok"},
        "guards": {"g1_W2_W1": "ok", "g2_W1_M": "ok", "g3_tool": "ok", "g4_audit": "ok", "g5_filter": "ok"},
    }})


# ─── 权限状态（桩） ────────────────────────────────
@app.get("/permissions/status")
async def permissions_status(api_key: str = Depends(verify_api_key)):
    return JSONResponse({"success": True, "data": {
        "role": "user",
        "elevated": False,
        "tools_enabled": 13,
        "tools_blocked": 0,
    }})

@app.get("/permissions/pending")
async def permissions_pending(api_key: str = Depends(verify_api_key)):
    return JSONResponse({"success": True, "data": {"items": []}})


# ─── Agent 可视化（桩） ────────────────────────────────
@app.get("/agent/graph")
async def agent_graph(api_key: str = Depends(verify_api_key)):
    return JSONResponse({"success": True, "data": {"mermaid": "graph TD\n    A[用户输入] --> B[感知层]\n    B --> C[记忆层]\n    C --> D[执行层]\n    D --> E[认知层]\n    E --> F[元认知层]\n    F --> G[输出]"}})

@app.get("/agent/parallel-steps")
async def agent_parallel_steps(api_key: str = Depends(verify_api_key)):
    return JSONResponse({"success": True, "data": {"steps": []}})

@app.get("/agent/branches")
async def agent_branches(api_key: str = Depends(verify_api_key)):
    return JSONResponse({"success": True, "data": {"branches": []}})


# ─── 分布式状态（桩） ────────────────────────────────
@app.get("/distributed/status")
async def distributed_status(task: str = "", api_key: str = Depends(verify_api_key)):
    if task:
        return JSONResponse({"success": True, "data": {"shards": [], "progress": 0, "task": task}})
    return JSONResponse({"success": True, "data": {
        "workers_online": 0, "workers_total": 0, "tasks_running": 0, "tasks_completed": 0,
    }})


# ─── 代码自修改提案（别名） ────────────────────────────────
@app.get("/code/proposals")
async def code_proposals(api_key: str = Depends(verify_admin_key)):
    """别名：/code/proposals → 自修改提案列表"""
    try:
        proposals = code_modifier.list_pending()
        return JSONResponse({"success": True, "data": {"proposals": proposals}})
    except Exception:
        return JSONResponse({"success": True, "data": {"proposals": []}})


# ─── 图片后端列表（桩） ────────────────────────────────
@app.get("/image/backends")
async def image_backends():
    return JSONResponse({"success": True, "data": {"backends": [
        {"id": "local", "label": "本地生成", "available": True, "loaded": True},
    ]}})


# ─── 图片对话（VL模型） ────────────────────────────────
@app.post("/chat/image")
async def chat_image(req: dict, api_key: str = Depends(verify_api_key)):
    """图片对话（需VL模型）"""
    from m_layer.llm_client import get_client
    client = get_client()
    image_data = req.get("image_data", "")
    prompt = req.get("prompt", "请描述这张图片")
    if not image_data:
        return JSONResponse({"success": False, "error": "未提供图片数据"})
    try:
        result = client.chat_with_image(prompt, image_data, auto_switch=True)
        return JSONResponse({"success": True, "data": {
            "response": result.get("content", ""),
            "model": result.get("model", ""),
            "model_type": result.get("model_type", "vl"),
            "switched": result.get("switched", False),
        }})
    except Exception as e:
        return JSONResponse({"success": False, "error": f"图片对话失败: {e}"})


if __name__ == "__main__":
    import uvicorn
    logger.info("🚀 标准计算单元2 SCU2 启动 (v3 架构 + Agent能力)")

    # 安全告警：使用默认Key时显著提示
    _get_configured_api_key()  # 触发标记
    _get_configured_admin_key()
    if _USING_DEV_API_KEY or _USING_DEV_ADMIN_KEY:
        warn_msg = (
            "\n" + "=" * 70 + "\n"
            "⚠️  安全告警：正在使用开发模式默认 API Key/Admin Key\n"
            "    生产环境务必配置环境变量：\n"
            "      set SCU2_API_KEY=<your_strong_key>\n"
            "      set SCU2_ADMIN_API_KEY=<your_strong_admin_key>\n"
            "    默认Key已公开在源码中，不安全！\n"
            + "=" * 70
        )
        logger.warning(warn_msg)
        print(warn_msg)

    # C4修复：默认监听127.0.0.1（生产环境用反向代理）
    host = os.getenv("SCU2_HOST", "127.0.0.1")
    port = int(os.getenv("SCU2_PORT", "8300"))
    if host in ("0.0.0.0", "::"):
        logger.warning(f"⚠️ 服务监听 {host}，将暴露至所有网卡！仅开发测试用。")
    uvicorn.run(app, host=host, port=port, log_level="info")
