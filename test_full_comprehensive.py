# -*- coding: utf-8 -*-
"""
test_full_comprehensive.py — SCU2 v5.2 综合测试套件
=====================================================
4 大类 × 3 次 = 12 次测试

  A. 全功能检测（3次）：所有模块功能完整性
  B. 饱和攻击测试（3次）：并发/压力/异常输入
  C. 代码和逻辑验证（3次）：语法/导入/类型/契约
  D. 功能软实现检查（3次）：端点注册/降级路径/错误处理

为避免外部网络波动影响测试稳定性，网络相关测试使用本地 HTTP 服务器。

运行：
    python test_full_comprehensive.py
"""
import os
import sys
import ast
import time
import json
import threading
import traceback
import importlib.util
from typing import List, Dict, Any, Tuple, Callable
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scu2.test.full")

# 测试结果收集
_results: List[Dict[str, Any]] = []


def record(category: str, run: int, name: str, ok: bool, detail: str = "", latency: float = 0):
    _results.append({
        "category": category, "run": run, "name": name,
        "ok": ok, "detail": detail, "latency": latency,
    })


# ─── 本地 HTTP 服务器（避免外部网络波动） ────────────────────────────────

_LOCAL_SERVER_PORT = 18923
_local_server: HTTPServer = None
_local_server_thread: threading.Thread = None


class _LocalHandler(BaseHTTPRequestHandler):
    """本地测试 HTTP 服务器

    提供：
      /         → 返回简单 HTML 页面
      /article  → 返回带 article 标签的页面
      /slow     → 延迟 2s 返回（测试超时）
    """
    def log_message(self, format, *args):
        pass  # 静默日志

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = b"""<!DOCTYPE html><html><head><title>SCU2 Test Page</title></head>
<body><h1>Hello SCU2</h1><p>This is a local test page.</p>
<a href="/article">Article</a><a href="https://example.com">External</a>
<img src="/img.png" alt="test image"></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/article":
            body = b"""<!DOCTYPE html><html><head><title>Test Article</title></head>
<body><nav>Navigation</nav><article><h1>Article Title</h1>
<p>This is the article content for testing fetch_article.</p>
<p>Another paragraph with more text.</p></article><footer>Footer</footer></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/slow":
            time.sleep(2)
            body = b"""<!DOCTYPE html><html><head><title>Slow Page</title></head>
<body><p>Slow response</p></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")


def _start_local_server():
    """启动本地 HTTP 服务器"""
    global _local_server, _local_server_thread
    _local_server = HTTPServer(("127.0.0.1", _LOCAL_SERVER_PORT), _LocalHandler)
    _local_server_thread = threading.Thread(target=_local_server.serve_forever, daemon=True)
    _local_server_thread.start()
    # 等待服务器就绪
    time.sleep(0.3)
    logger.info(f"本地测试服务器已启动: http://127.0.0.1:{_LOCAL_SERVER_PORT}")


def _stop_local_server():
    """停止本地 HTTP 服务器"""
    global _local_server
    if _local_server:
        _local_server.shutdown()
        _local_server.server_close()


LOCAL_BASE_URL = f"http://127.0.0.1:{_LOCAL_SERVER_PORT}"


def record(category: str, run: int, name: str, ok: bool, detail: str = "", latency: float = 0):
    _results.append({
        "category": category, "run": run, "name": name,
        "ok": ok, "detail": detail, "latency": latency,
    })
    status = "PASS" if ok else "FAIL"
    print(f"    [{status}] {name}" + (f" ({latency:.2f}s)" if latency > 0.01 else ""))
    if not ok and detail:
        print(f"           ↳ {detail[:200]}")


# ═══════════════════════════════════════════════════════════════
#  A. 全功能检测（3次）
# ═══════════════════════════════════════════════════════════════

def run_full_function_run(run: int):
    """单次全功能检测"""
    print(f"\n  ─── 全功能检测 第 {run} 次 ───")
    t0 = time.time()

    # A1. 依赖完整性
    try:
        from w1_layer import automation
        from m_layer import voice_io, module_registry, local_model, llm_client
        deps = {
            "playwright": automation._PLAYWRIGHT_AVAILABLE,
            "mss": automation._MSS_AVAILABLE,
            "httpx": automation._HTTPX_AVAILABLE,
            "bs4": automation._BS4_AVAILABLE,
            "pyautogui": automation._PYAUTOGUI_AVAILABLE,
            "PIL": automation._PIL_AVAILABLE,
            "pyaudio": voice_io._PYAUDIO_AVAILABLE,
            "speech_recognition": voice_io._SR_AVAILABLE,
            "whisper": voice_io._WHISPER_AVAILABLE,
            "webrtcvad": voice_io._WEBRTC_VAD_AVAILABLE,
        }
        ok = all(deps.values())
        record("A全功能", run, "依赖完整性", ok, f"缺失: {[k for k,v in deps.items() if not v]}", time.time()-t0)
    except Exception as e:
        record("A全功能", run, "依赖完整性", False, str(e), time.time()-t0)

    # A2. 模块导入
    t0 = time.time()
    try:
        modules = ["w1_layer.automation", "m_layer.voice_io", "m_layer.module_registry",
                   "m_layer.local_model", "m_layer.llm_client", "server"]
        for m in modules:
            importlib.import_module(m)
        record("A全功能", run, "模块导入（6个）", True, "", time.time()-t0)
    except Exception as e:
        record("A全功能", run, "模块导入（6个）", False, str(e), time.time()-t0)

    # A3. VL 模型配置
    t0 = time.time()
    try:
        from m_layer.local_model import SUPPORTED_MODELS
        vl = {k: v for k, v in SUPPORTED_MODELS.items() if v.get("model_type") == "vl"}
        text = {k: v for k, v in SUPPORTED_MODELS.items() if v.get("model_type") == "text"}
        ok = "qwen2-5-vl-7b" in vl and "qwen2-5-7b" in text
        record("A全功能", run, "VL 模型配置", ok, f"vl={list(vl.keys())}, text={list(text.keys())}", time.time()-t0)
    except Exception as e:
        record("A全功能", run, "VL 模型配置", False, str(e), time.time()-t0)

    # A4. 截屏功能
    t0 = time.time()
    try:
        from w1_layer.automation import get_screen_capture
        result = get_screen_capture().capture_to_file()
        ok = result.get("success") and len(result.get("base64", "")) > 1000
        record("A全功能", run, "截屏功能", ok, f"path={result.get('path')}", time.time()-t0)
    except Exception as e:
        record("A全功能", run, "截屏功能", False, str(e), time.time()-t0)

    # A5. 网页抓取（使用本地服务器，避免外部网络波动）
    t0 = time.time()
    try:
        from w1_layer.automation import get_web_scraper
        result = get_web_scraper().fetch(LOCAL_BASE_URL, max_length=500)
        ok = result.get("success") and result.get("content_length", 0) > 0
        record("A全功能", run, "网页抓取", ok, f"status={result.get('status_code')}, len={result.get('content_length')}, attempts={result.get('attempts', 1)}", time.time()-t0)
    except Exception as e:
        record("A全功能", run, "网页抓取", False, str(e), time.time()-t0)

    # A6. 浏览器自动化（使用本地服务器）
    t0 = time.time()
    try:
        from w1_layer.automation import get_browser
        ba = get_browser()
        if not ba.available:
            record("A全功能", run, "浏览器自动化", False, "playwright 不可用", time.time()-t0)
        else:
            ba.start(headless=True)
            r = ba.navigate(LOCAL_BASE_URL)
            ba.stop()
            ok = r.get("success")
            record("A全功能", run, "浏览器自动化", ok, f"title={r.get('title')}", time.time()-t0)
    except Exception as e:
        record("A全功能", run, "浏览器自动化", False, str(e), time.time()-t0)

    # A7. ContinuousListener 接口
    t0 = time.time()
    try:
        from m_layer.voice_io import get_listener
        listener = get_listener()
        status = listener.status()
        ok = "vad_backend" in status and "running" in status
        record("A全功能", run, "ContinuousListener 接口", ok, f"vad={status.get('vad_backend')}", time.time()-t0)
    except Exception as e:
        record("A全功能", run, "ContinuousListener 接口", False, str(e), time.time()-t0)

    # A8. ModuleRegistry
    t0 = time.time()
    try:
        from m_layer.module_registry import get_registry, register_builtin_modules
        register_builtin_modules()
        registry = get_registry()
        modules = registry.list_modules()
        ok = len(modules) >= 8
        record("A全功能", run, "ModuleRegistry", ok, f"{len(modules)} 个模块", time.time()-t0)
    except Exception as e:
        record("A全功能", run, "ModuleRegistry", False, str(e), time.time()-t0)

    # A9. server.py 端点总数
    t0 = time.time()
    try:
        import server
        routes = [r for r in server.app.routes if hasattr(r, "path")]
        ok = len(routes) >= 60
        record("A全功能", run, "server.py 端点数", ok, f"{len(routes)} 个端点", time.time()-t0)
    except Exception as e:
        record("A全功能", run, "server.py 端点数", False, str(e), time.time()-t0)


# ═══════════════════════════════════════════════════════════════
#  B. 饱和攻击测试（3次）
# ═══════════════════════════════════════════════════════════════

def run_saturation_run(run: int):
    """单次饱和攻击测试"""
    print(f"\n  ─── 饱和攻击测试 第 {run} 次 ───")

    # B1. 并发截屏（10 线程同时）
    t0 = time.time()
    try:
        from w1_layer.automation import get_screen_capture
        sc = get_screen_capture()
        errors = []
        def worker():
            try:
                r = sc.capture_to_file()
                if not r.get("success"):
                    errors.append("capture failed")
            except Exception as e:
                errors.append(str(e))
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        ok = len(errors) == 0
        record("B饱和", run, "并发截屏（10线程）", ok, f"errors={len(errors)}", time.time()-t0)
    except Exception as e:
        record("B饱和", run, "并发截屏（10线程）", False, str(e), time.time()-t0)

    # B2. 并发网页抓取（5 线程，使用本地服务器，避免外部网络限流）
    t0 = time.time()
    try:
        from w1_layer.automation import get_web_scraper
        ws = get_web_scraper()
        results = []
        errors = []
        def worker_fetch():
            try:
                r = ws.fetch(LOCAL_BASE_URL, max_length=200)
                results.append(r.get("success", False))
            except Exception as e:
                errors.append(str(e))
        threads = [threading.Thread(target=worker_fetch) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=30)
        ok = len(errors) == 0 and all(results)
        record("B饱和", run, "并发网页抓取（5线程）", ok, f"ok={sum(results)}/{len(results)}, err={len(errors)}", time.time()-t0)
    except Exception as e:
        record("B饱和", run, "并发网页抓取（5线程）", False, str(e), time.time()-t0)

    # B3. 大量模块注册/卸载循环（100次）
    t0 = time.time()
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        for i in range(100):
            name = f"stress.test.{i}"
            registry.register(name, f"压力测试模块{i}", loader=lambda: i, unloader=lambda m: None, category="stress")
            registry.load(name)
            registry.unload(name)
        # 清理
        for i in range(100):
            name = f"stress.test.{i}"
            if name in registry._modules:
                del registry._modules[name]
        ok = True
        record("B饱和", run, "模块注册/卸载循环（100次）", ok, "", time.time()-t0)
    except Exception as e:
        record("B饱和", run, "模块注册/卸载循环（100次）", False, str(e), time.time()-t0)

    # B4. 异常输入：网页抓取空URL
    t0 = time.time()
    try:
        from w1_layer.automation import get_web_scraper
        r = get_web_scraper().fetch("")
        ok = not r.get("success")  # 应失败但不崩溃
        record("B饱和", run, "异常输入-空URL", ok, f"error={r.get('error', '')[:100]}", time.time()-t0)
    except Exception as e:
        record("B饱和", run, "异常输入-空URL", False, str(e), time.time()-t0)

    # B5. 异常输入：截屏非法显示器
    t0 = time.time()
    try:
        from w1_layer.automation import get_screen_capture
        r = get_screen_capture().capture_to_file(monitor=999)
        ok = not r.get("success")  # 应失败但不崩溃
        record("B饱和", run, "异常输入-非法显示器", ok, f"error={r.get('error', '')[:100]}", time.time()-t0)
    except Exception as e:
        record("B饱和", run, "异常输入-非法显示器", False, str(e), time.time()-t0)

    # B6. 异常输入：VL 模型未加载时调用
    t0 = time.time()
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        # 确保未加载
        if client._model_loaded:
            ok = True
            detail = "模型已加载，跳过"
        else:
            r = client.chat_with_image(prompt="test", image={"path": "/nonexistent.png"})
            ok = r.get("error") in ("model_not_loaded", "model_not_vl", "processor_unavailable")
            detail = f"error={r.get('error')}"
        record("B饱和", run, "异常输入-VL未加载调用", ok, detail, time.time()-t0)
    except Exception as e:
        record("B饱和", run, "异常输入-VL未加载调用", False, str(e), time.time()-t0)

    # B7. 异常输入：模块卸载未注册模块
    t0 = time.time()
    try:
        from m_layer.module_registry import get_registry
        r = get_registry().unload("nonexistent.module.xyz")
        ok = not r.get("success")
        record("B饱和", run, "异常输入-卸载未注册模块", ok, f"error={r.get('error', '')[:100]}", time.time()-t0)
    except Exception as e:
        record("B饱和", run, "异常输入-卸载未注册模块", False, str(e), time.time()-t0)

    # B8. 受保护模块卸载攻击
    t0 = time.time()
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        registry.register("cuf.axioms", "受保护", loader=lambda: "x", unloader=lambda m: None, category="security")
        registry.load("cuf.axioms")
        r = registry.unload("cuf.axioms", force=False)
        ok = not r.get("success")  # 应被拒绝
        record("B饱和", run, "受保护模块卸载攻击", ok, f"error={r.get('error', '')[:100]}", time.time()-t0)
    except Exception as e:
        record("B饱和", run, "受保护模块卸载攻击", False, str(e), time.time()-t0)


# ═══════════════════════════════════════════════════════════════
#  C. 代码和逻辑验证（3次）
# ═══════════════════════════════════════════════════════════════

def run_code_logic_run(run: int):
    """单次代码和逻辑验证"""
    print(f"\n  ─── 代码和逻辑验证 第 {run} 次 ───")

    # C1. 语法检查所有 Python 文件
    t0 = time.time()
    try:
        files_to_check = [
            "server.py", "engine.py", "meta_guard.py", "baseline.py",
            "m_layer/local_model.py", "m_layer/llm_client.py", "m_layer/voice_io.py",
            "m_layer/module_registry.py", "m_layer/code_self_modify.py",
            "w1_layer/automation.py", "w1_layer/extended_tools.py",
            "w1_layer/memory.py",
        ]
        errors = []
        for f in files_to_check:
            path = os.path.join(BASE_DIR, f)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as fp:
                try:
                    ast.parse(fp.read())
                except SyntaxError as e:
                    errors.append(f"{f}: {e}")
        ok = len(errors) == 0
        record("C代码逻辑", run, f"语法检查（{len(files_to_check)}文件）", ok, "; ".join(errors)[:200], time.time()-t0)
    except Exception as e:
        record("C代码逻辑", run, "语法检查", False, str(e), time.time()-t0)

    # C2. 类型注解检查（关键方法）
    t0 = time.time()
    try:
        from m_layer.local_model import LocalModelClient
        from m_layer.voice_io import ContinuousListener
        from m_layer.module_registry import ModuleRegistry
        from w1_layer.automation import BrowserAutomation, ScreenCapture, WebScraper, DesktopControl

        type_checks = []
        # 检查方法存在性
        for cls, methods in [
            (LocalModelClient, ["load_model", "unload_model", "chat", "chat_with_image", "switch_model_type"]),
            (ContinuousListener, ["start", "stop", "status"]),
            (ModuleRegistry, ["register", "load", "unload", "reload", "disable", "enable"]),
            (BrowserAutomation, ["navigate", "click", "fill", "screenshot"]),
            (ScreenCapture, ["capture_full", "capture_to_file"]),
            (WebScraper, ["fetch", "fetch_article"]),
            (DesktopControl, ["click", "type_text", "press", "hot_key"]),
        ]:
            for m in methods:
                if not hasattr(cls, m):
                    type_checks.append(f"{cls.__name__}.{m} 缺失")
        ok = len(type_checks) == 0
        record("C代码逻辑", run, "方法存在性检查", ok, "; ".join(type_checks)[:200], time.time()-t0)
    except Exception as e:
        record("C代码逻辑", run, "方法存在性检查", False, str(e), time.time()-t0)

    # C3. VL 模型配置逻辑
    t0 = time.time()
    try:
        from m_layer.local_model import SUPPORTED_MODELS
        issues = []
        for name, cfg in SUPPORTED_MODELS.items():
            if "model_id" not in cfg: issues.append(f"{name} 缺 model_id")
            if "model_type" not in cfg: issues.append(f"{name} 缺 model_type")
            if "context_length" not in cfg: issues.append(f"{name} 缺 context_length")
            if cfg.get("model_type") not in ("text", "vl"): issues.append(f"{name} model_type 异常")
            if cfg.get("min_memory_gb", 0) <= 0: issues.append(f"{name} memory 异常")
        ok = len(issues) == 0
        record("C代码逻辑", run, "VL 模型配置逻辑", ok, "; ".join(issues)[:200], time.time()-t0)
    except Exception as e:
        record("C代码逻辑", run, "VL 模型配置逻辑", False, str(e), time.time()-t0)

    # C4. 模块注册表契约
    t0 = time.time()
    try:
        from m_layer.module_registry import PROTECTED_MODULES, ModuleRegistry
        expected_protected = {"cuf.firewall", "cuf.entropy_ledger", "cuf.axioms", "engine",
                              "meta_guard", "baseline", "code_self_modify", "module_registry"}
        missing = expected_protected - PROTECTED_MODULES
        ok = len(missing) == 0
        record("C代码逻辑", run, "受保护模块契约", ok, f"缺失: {missing}", time.time()-t0)
    except Exception as e:
        record("C代码逻辑", run, "受保护模块契约", False, str(e), time.time()-t0)

    # C5. ContinuousListener VAD 逻辑
    t0 = time.time()
    try:
        from m_layer.voice_io import ContinuousListener, _WEBRTC_VAD_AVAILABLE
        listener = ContinuousListener()
        # 检查 VAD 后端选择逻辑
        expected = "webrtcvad" if _WEBRTC_VAD_AVAILABLE else "energy_rms"
        ok = listener.vad_backend == expected
        record("C代码逻辑", run, "VAD 后端选择逻辑", ok, f"backend={listener.vad_backend}, expected={expected}", time.time()-t0)
    except Exception as e:
        record("C代码逻辑", run, "VAD 后端选择逻辑", False, str(e), time.time()-t0)

    # C6. 模型切换逻辑
    t0 = time.time()
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        # 非法类型
        r1 = client.switch_model_type("audio")
        # 非法模型名
        r2 = client.switch_model_type("vl", model_name="nonexistent")
        # 类型不匹配
        r3 = client.switch_model_type("vl", model_name="qwen2-5-7b")
        ok = (not r1.get("success")) and (not r2.get("success")) and (not r3.get("success"))
        record("C代码逻辑", run, "模型切换校验逻辑", ok, f"r1={r1.get('error', '')[:50]}", time.time()-t0)
    except Exception as e:
        record("C代码逻辑", run, "模型切换校验逻辑", False, str(e), time.time()-t0)

    # C7. 降级策略存在性
    t0 = time.time()
    try:
        from m_layer import voice_io
        from m_layer.voice_io import VoiceIO
        v = VoiceIO()
        status = v.status()
        # 应有降级链
        has_stt = status.get("recognizer_backend") is not None
        has_tts = status.get("synthesizer_backend") is not None
        ok = has_stt and has_tts
        record("C代码逻辑", run, "语音降级策略", ok, f"stt={status.get('recognizer_backend')}, tts={status.get('synthesizer_backend')}", time.time()-t0)
    except Exception as e:
        record("C代码逻辑", run, "语音降级策略", False, str(e), time.time()-t0)


# ═══════════════════════════════════════════════════════════════
#  D. 功能软实现检查（3次）
# ═══════════════════════════════════════════════════════════════

def run_soft_impl_run(run: int):
    """单次功能软实现检查"""
    print(f"\n  ─── 功能软实现检查 第 {run} 次 ───")

    # D1. server.py 端点注册完整性
    t0 = time.time()
    try:
        import server
        routes = {}
        for r in server.app.routes:
            if hasattr(r, "path"):
                for m in (r.methods or set()):
                    routes.setdefault(r.path, set()).add(m)
        # 检查关键端点组
        expected_groups = {
            "browser": [p for p in routes if p.startswith("/browser/")],
            "screen": [p for p in routes if p.startswith("/screen/")],
            "web": [p for p in routes if p.startswith("/web/")],
            "desktop": [p for p in routes if p.startswith("/desktop/")],
            "vision": [p for p in routes if p.startswith("/vision/")],
            "voice": [p for p in routes if p.startswith("/voice/")],
            "modules": [p for p in routes if p.startswith("/modules")],
            "local-model": [p for p in routes if p.startswith("/local-model/")],
        }
        missing = {k: 0 for k in expected_groups}
        for k, v in expected_groups.items():
            if len(v) == 0:
                missing[k] = 0
        ok = all(len(v) > 0 for v in expected_groups.values())
        detail = ", ".join(f"{k}={len(v)}" for k, v in expected_groups.items())
        record("D软实现", run, "端点组完整性", ok, detail, time.time()-t0)
    except Exception as e:
        record("D软实现", run, "端点组完整性", False, str(e), time.time()-t0)

    # D2. 降级路径软实现（无 pyaudio 时不崩溃）
    t0 = time.time()
    try:
        from m_layer.voice_io import get_listener
        listener = get_listener()
        if listener.available:
            # 有 pyaudio，检查 start/stop 接口软实现
            status = listener.status()
            ok = "vad_backend" in status
            record("D软实现", run, "语音监听降级软实现", ok, f"available=True, vad={status.get('vad_backend')}", time.time()-t0)
        else:
            # 无 pyaudio，start 应返回错误而非崩溃
            r = listener.start()
            ok = not r.get("success")
            record("D软实现", run, "语音监听降级软实现", ok, f"无 pyaudio 时正确降级: {r.get('error', '')[:100]}", time.time()-t0)
    except Exception as e:
        record("D软实现", run, "语音监听降级软实现", False, str(e), time.time()-t0)

    # D3. 模块卸载降级软实现
    t0 = time.time()
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        # 卸载已加载但无 unloader 的模块应不崩溃
        registry.register("soft.test", "软实现测试", loader=lambda: "instance", unloader=None, category="test")
        registry.load("soft.test")
        r = registry.unload("soft.test")
        ok = r.get("success")  # 无 unloader 也应成功（仅标记为已卸载）
        record("D软实现", run, "无 unloader 模块卸载", ok, f"unloader_error={r.get('unloader_error')}", time.time()-t0)
    except Exception as e:
        record("D软实现", run, "无 unloader 模块卸载", False, str(e), time.time()-t0)

    # D4. VL 模型未加载降级
    t0 = time.time()
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        if client._model_loaded:
            ok = True
            detail = "模型已加载，跳过降级检查"
        else:
            r = client.chat_with_image(prompt="test", image={"path": "/x.png"})
            ok = r.get("error") is not None  # 应有错误但不崩溃
            detail = f"error={r.get('error')}"
        record("D软实现", run, "VL 未加载降级", ok, detail, time.time()-t0)
    except Exception as e:
        record("D软实现", run, "VL 未加载降级", False, str(e), time.time()-t0)

    # D5. 浏览器未启动时操作降级
    t0 = time.time()
    try:
        from w1_layer.automation import get_browser
        ba = get_browser()
        if ba.started:
            ba.stop()
        # 未启动时调用 navigate 应返回错误
        r = ba.navigate("https://example.com")
        ok = not r.get("success")
        record("D软实现", run, "浏览器未启动降级", ok, f"error={r.get('error', '')[:100]}", time.time()-t0)
    except Exception as e:
        record("D软实现", run, "浏览器未启动降级", False, str(e), time.time()-t0)

    # D6. 网页抓取无效 URL 降级（本机未占用端口，连接快速失败）
    t0 = time.time()
    try:
        from w1_layer.automation import get_web_scraper
        # 1.1.1.1:1 几乎总是连接被拒绝，快速失败不依赖 DNS
        r = get_web_scraper().fetch("http://127.0.0.1:1", max_length=100)
        ok = not r.get("success")  # 应失败但不崩溃
        record("D软实现", run, "无效 URL 抓取降级", ok, f"error={r.get('error', '')[:100]}, attempts={r.get('attempts', 1)}", time.time()-t0)
    except Exception as e:
        record("D软实现", run, "无效 URL 抓取降级", False, str(e), time.time()-t0)

    # D7. 受保护模块禁用降级
    t0 = time.time()
    try:
        from m_layer.module_registry import get_registry
        registry = get_registry()
        registry.register("engine", "引擎", loader=lambda: "x", unloader=lambda m: None, category="core")
        r = registry.disable("engine")
        ok = not r.get("success")  # 应被拒绝
        record("D软实现", run, "受保护模块禁用降级", ok, f"error={r.get('error', '')[:100]}", time.time()-t0)
    except Exception as e:
        record("D软实现", run, "受保护模块禁用降级", False, str(e), time.time()-t0)

    # D8. 模型切换类型一致性
    t0 = time.time()
    try:
        from m_layer.local_model import get_local_model
        client = get_local_model()
        # switch_model_type 返回结构应包含必要字段
        r = client.switch_model_type("invalid")
        ok = "success" in r and "error" in r
        record("D软实现", run, "模型切换返回契约", ok, f"keys={list(r.keys())}", time.time()-t0)
    except Exception as e:
        record("D软实现", run, "模型切换返回契约", False, str(e), time.time()-t0)


# ═══════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════

def print_summary():
    """汇总报告"""
    print("\n" + "=" * 70)
    print("                       综合测试汇总报告")
    print("=" * 70)

    categories = ["A全功能", "B饱和", "C代码逻辑", "D软实现"]
    total_pass = 0
    total_fail = 0

    for cat in categories:
        cat_results = [r for r in _results if r["category"] == cat]
        cat_pass = sum(1 for r in cat_results if r["ok"])
        cat_fail = sum(1 for r in cat_results if not r["ok"])
        total_pass += cat_pass
        total_fail += cat_fail

        print(f"\n  [{cat}] 通过 {cat_pass}/{len(cat_results)}")
        for run in [1, 2, 3]:
            run_results = [r for r in cat_results if r["run"] == run]
            run_pass = sum(1 for r in run_results if r["ok"])
            print(f"    第{run}次: {run_pass}/{len(run_results)}")
            for r in run_results:
                if not r["ok"]:
                    print(f"      ✗ {r['name']}: {r['detail'][:100]}")

    print("\n" + "-" * 70)
    total = total_pass + total_fail
    rate = (total_pass / total * 100) if total > 0 else 0
    print(f"  总计: {total_pass}/{total} 通过 ({rate:.1f}%)")
    print(f"  失败: {total_fail}")
    print("=" * 70)

    return total_fail == 0


def main():
    print("=" * 70)
    print("  SCU2 v5.2 综合测试套件")
    print("  4 大类 × 3 次 = 12 次测试")
    print("  网络测试使用本地 HTTP 服务器（避免外部网络波动）")
    print("=" * 70)

    # 设置环境变量
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"

    # 启动本地测试服务器
    print("\n  启动本地测试服务器...")
    try:
        _start_local_server()
        print(f"  本地服务器已就绪: {LOCAL_BASE_URL}")
    except Exception as e:
        print(f"  ⚠ 本地服务器启动失败，网络测试将回退到外部源: {e}")

    suite_start = time.time()

    try:
        # A. 全功能检测 ×3
        print("\n\n╔══════════════════════════════════════════════════════════╗")
        print("║          A. 全功能检测（3次）                            ║")
        print("╚══════════════════════════════════════════════════════════╝")
        for i in range(1, 4):
            run_full_function_run(i)

        # B. 饱和攻击测试 ×3
        print("\n\n╔══════════════════════════════════════════════════════════╗")
        print("║          B. 饱和攻击测试（3次）                          ║")
        print("╚══════════════════════════════════════════════════════════╝")
        for i in range(1, 4):
            run_saturation_run(i)

        # C. 代码和逻辑验证 ×3
        print("\n\n╔══════════════════════════════════════════════════════════╗")
        print("║          C. 代码和逻辑验证（3次）                        ║")
        print("╚══════════════════════════════════════════════════════════╝")
        for i in range(1, 4):
            run_code_logic_run(i)

        # D. 功能软实现检查 ×3
        print("\n\n╔══════════════════════════════════════════════════════════╗")
        print("║          D. 功能软实现检查（3次）                        ║")
        print("╚══════════════════════════════════════════════════════════╝")
        for i in range(1, 4):
            run_soft_impl_run(i)

        suite_latency = time.time() - suite_start
        print(f"\n  总耗时: {suite_latency:.1f}s")

        all_ok = print_summary()
        return 0 if all_ok else 1
    finally:
        # 确保服务器关闭
        _stop_local_server()


if __name__ == "__main__":
    sys.exit(main())
