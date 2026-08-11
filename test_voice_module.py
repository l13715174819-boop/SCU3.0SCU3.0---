# -*- coding: utf-8 -*-
"""
test_voice_module.py — 语音监听 + 模块管理 集成测试
=====================================================
测试 v5.2 新增能力：
  1. ContinuousListener 接口与状态（不实际开启麦克风）
  2. ModuleRegistry 注册/卸载/重载/禁用/启用
  3. server.py 端点注册检查
  4. 受保护模块不可卸载验证
"""
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scu2.test.v5.2")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def test_continuous_listener_interface():
    """测试1：ContinuousListener 接口与状态"""
    print("\n" + "=" * 60)
    print("测试1：ContinuousListener 接口")
    print("=" * 60)

    from m_layer.voice_io import get_listener, ContinuousListener, _WEBRTC_VAD_AVAILABLE, _PYAUDIO_AVAILABLE

    listener = get_listener()
    assert isinstance(listener, ContinuousListener), "类型错误"

    # 接口检查
    for method in ["start", "stop", "status", "available", "running", "vad_backend"]:
        assert hasattr(listener, method), f"缺少 {method}"
    print(f"  [OK] 接口齐全: start/stop/status/available/running/vad_backend")

    # 状态检查（未启动）
    status = listener.status()
    print(f"\n  状态:")
    for k, v in status.items():
        print(f"    {k}: {v}")

    assert status["running"] is False, "初始状态应为未运行"
    assert status["available"] == _PYAUDIO_AVAILABLE
    assert status["vad_backend"] in ("webrtcvad", "energy_rms")
    print(f"\n  [OK] 初始状态正确 (running=False, vad={status['vad_backend']})")

    # 在未启动时调用 stop 应安全返回
    result = listener.stop()
    assert result.get("success"), "未运行时 stop 应返回 success"
    print(f"  [OK] 未运行时 stop 安全返回")

    return True


def test_continuous_listener_callbacks():
    """测试2：回调机制（不实际录音）"""
    print("\n" + "=" * 60)
    print("测试2：回调机制")
    print("=" * 60)

    from m_layer.voice_io import ContinuousListener

    listener = ContinuousListener()

    # 设置回调
    received = []
    listener.on_utterance = lambda text: received.append(("utterance", text))
    listener.on_wake_word = lambda: received.append(("wake",))
    listener.on_state_change = lambda s: received.append(("state", s))

    # 模拟触发（直接调用 _process_utterance 内部逻辑会触发识别，这里只测回调可设置）
    assert listener.on_utterance is not None
    assert listener.on_wake_word is not None
    assert listener.on_state_change is not None
    print(f"  [OK] 回调设置成功: on_utterance/on_wake_word/on_state_change")

    # 测试 _notify_state 回调
    listener._notify_state("listening")
    listener._notify_state("speaking")
    assert ("state", "listening") in received
    assert ("state", "speaking") in received
    print(f"  [OK] on_state_change 回调被触发: {received}")

    return True


def test_module_registry_basic():
    """测试3：ModuleRegistry 注册/加载/卸载"""
    print("\n" + "=" * 60)
    print("测试3：ModuleRegistry 基本操作")
    print("=" * 60)

    from m_layer.module_registry import get_registry, ModuleRegistry

    registry = get_registry()
    assert isinstance(registry, ModuleRegistry)

    # 注册测试模块
    load_count = [0]
    unload_count = [0]

    def loader():
        load_count[0] += 1
        return {"name": "test_module", "loaded": True}

    def unloader(instance):
        unload_count[0] += 1
        return {"success": True}

    result = registry.register(
        "test.dummy",
        "测试模块",
        loader=loader,
        unloader=unloader,
        category="test",
    )
    assert result["success"], f"注册失败: {result}"
    print(f"  [OK] 注册成功: {result}")

    # 加载
    result = registry.load("test.dummy")
    assert result["success"], f"加载失败: {result}"
    assert load_count[0] == 1
    print(f"  [OK] 加载成功 (loader 调用次数: {load_count[0]})")

    # 重复加载应直接返回成功
    result = registry.load("test.dummy")
    assert result["success"]
    assert load_count[0] == 1, "重复加载不应调用 loader"
    print(f"  [OK] 重复加载幂等")

    # 获取实例
    instance = registry.get("test.dummy")
    assert instance is not None and instance.get("loaded") is True
    print(f"  [OK] get() 返回实例: {instance}")

    # is_available
    assert registry.is_available("test.dummy")
    print(f"  [OK] is_available=True")

    # 卸载
    result = registry.unload("test.dummy")
    assert result["success"], f"卸载失败: {result}"
    assert unload_count[0] == 1
    print(f"  [OK] 卸载成功 (unloader 调用次数: {unload_count[0]})")

    # 卸载后实例为 None
    assert registry.get("test.dummy") is None
    assert not registry.is_available("test.dummy")
    print(f"  [OK] 卸载后 get()=None, is_available=False")

    return True


def test_module_registry_protected():
    """测试4：受保护模块不可卸载"""
    print("\n" + "=" * 60)
    print("测试4：受保护模块保护")
    print("=" * 60)

    from m_layer.module_registry import get_registry, PROTECTED_MODULES

    print(f"  受保护模块: {PROTECTED_MODULES}")
    assert "cuf.firewall" in PROTECTED_MODULES
    assert "module_registry" in PROTECTED_MODULES

    # 注册一个受保护模块（模拟）
    registry = get_registry()
    registry.register(
        "cuf.firewall",
        "CUF 逻辑防火墙（受保护）",
        loader=lambda: "firewall_instance",
        unloader=lambda m: None,
        category="security",
    )
    registry.load("cuf.firewall")

    # 尝试卸载（应被拒绝）
    result = registry.unload("cuf.firewall", force=False)
    assert not result["success"], "受保护模块不应被卸载"
    print(f"  [OK] 受保护模块拒绝卸载: {result.get('error')}")

    # force=true 应可卸载
    result = registry.unload("cuf.firewall", force=True)
    assert result["success"], "force=true 应可卸载"
    print(f"  [OK] force=true 可强制卸载")

    # 尝试禁用（应被拒绝）
    registry.load("cuf.firewall")
    result = registry.disable("cuf.firewall")
    assert not result["success"], "受保护模块不应被禁用"
    print(f"  [OK] 受保护模块拒绝禁用: {result.get('error')}")

    return True


def test_module_registry_disable_enable():
    """测试5：禁用/启用"""
    print("\n" + "=" * 60)
    print("测试5：禁用/启用")
    print("=" * 60)

    from m_layer.module_registry import get_registry

    registry = get_registry()

    # 先加载 test.dummy
    registry.load("test.dummy")

    # 禁用
    result = registry.disable("test.dummy")
    assert result["success"]
    assert result["disabled"] is True
    print(f"  [OK] 禁用成功")

    # 禁用后 load 应失败
    result = registry.load("test.dummy")
    assert not result["success"], "禁用后不应能 load"
    print(f"  [OK] 禁用后 load 被拒绝: {result.get('error')}")

    # 启用
    result = registry.enable("test.dummy")
    assert result["success"]
    print(f"  [OK] 启用成功")

    # 启用后可 load
    result = registry.load("test.dummy")
    assert result["success"], "启用后应可 load"
    print(f"  [OK] 启用后可加载")

    return True


def test_module_registry_reload():
    """测试6：重载"""
    print("\n" + "=" * 60)
    print("测试6：重载（unload+load）")
    print("=" * 60)

    from m_layer.module_registry import get_registry

    registry = get_registry()
    registry.load("test.dummy")

    result = registry.reload("test.dummy")
    assert result["success"], f"重载失败: {result}"
    assert result["loaded"] is True
    print(f"  [OK] 重载成功: loaded={result['loaded']}")
    print(f"    unload: {result['unload'].get('success')}")
    print(f"    load:   {result['load'].get('success')}")

    return True


def test_server_endpoints():
    """测试7：server.py 端点注册"""
    print("\n" + "=" * 60)
    print("测试7：server.py 端点注册")
    print("=" * 60)

    try:
        import server
    except Exception as e:
        print(f"  [FAIL] server 导入失败: {e}")
        return False

    routes = []
    for route in server.app.routes:
        if hasattr(route, "path"):
            methods = getattr(route, "methods", set()) or set()
            routes.append((route.path, sorted(methods)))

    expected = [
        # 语音监听
        ("/voice/listen/start", "POST"),
        ("/voice/listen/stop", "POST"),
        ("/voice/listen/status", "GET"),
        ("/voice/listen/events", "GET"),
        # 模块管理
        ("/modules", "GET"),
        ("/modules/{name}", "GET"),
        ("/modules/{name}/load", "POST"),
        ("/modules/{name}/unload", "POST"),
        ("/modules/{name}/reload", "POST"),
        ("/modules/{name}/disable", "POST"),
        ("/modules/{name}/enable", "POST"),
        ("/modules/status", "GET"),
    ]

    all_ok = True
    for path, method in expected:
        found = any(path == r_path and method in r_methods for r_path, r_methods in routes)
        status = "[OK]" if found else "[FAIL]"
        if not found:
            all_ok = False
        print(f"  {status} {method:4s} {path}")

    print(f"\n  新增端点: {len(expected)} 个，全部注册: {'是' if all_ok else '否'}")
    return all_ok


def test_builtin_modules_registration():
    """测试8：内置模块注册"""
    print("\n" + "=" * 60)
    print("测试8：内置模块注册")
    print("=" * 60)

    from m_layer.module_registry import register_builtin_modules, get_registry

    register_builtin_modules()
    registry = get_registry()
    modules = registry.list_modules()

    print(f"\n  已注册模块 ({len(modules)} 个):")
    for m in modules:
        prot = "[P]" if m["protected"] else "   "
        dis = "[D]" if m["disabled"] else "   "
        print(f"    {prot}{dis} {m['name']:25s} ({m['category']:10s}) - {m['description']}")

    # 应包含自动化、语音、LLM、知识库等核心模块
    names = [m["name"] for m in modules]
    expected = [
        "automation.browser", "automation.screen", "automation.web_scraper", "automation.desktop",
        "voice.io", "voice.listener",
        "llm.local_model",
        "knowledge.base",
    ]
    for name in expected:
        assert name in names, f"缺少内置模块: {name}"
    print(f"\n  [OK] 所有 {len(expected)} 个核心内置模块已注册")

    # 总状态
    status = registry.status()
    print(f"\n  总状态: {status}")

    return True


def main():
    print("=" * 60)
    print("SCU2 v5.2 — 语音监听 + 模块管理 集成测试")
    print("=" * 60)

    tests = [
        ("ContinuousListener 接口", test_continuous_listener_interface),
        ("回调机制", test_continuous_listener_callbacks),
        ("ModuleRegistry 基本操作", test_module_registry_basic),
        ("受保护模块", test_module_registry_protected),
        ("禁用/启用", test_module_registry_disable_enable),
        ("重载", test_module_registry_reload),
        ("server 端点注册", test_server_endpoints),
        ("内置模块注册", test_builtin_modules_registration),
    ]

    results = []
    for name, fn in tests:
        try:
            ok = fn()
            results.append((name, ok))
        except Exception as e:
            import traceback
            print(f"\n  [FAIL] 测试异常: {e}")
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 60)
    print("测试汇总")
    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  通过: {passed}/{total}")
    print("=" * 60)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
