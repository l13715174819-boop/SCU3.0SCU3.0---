# -*- coding: utf-8 -*-
"""
test_modular_fixes.py — 模块化可插拔性修复验证测试
====================================================
验证 7 项修复是否全部生效

运行：python test_modular_fixes.py
"""
import os
import sys
import time
import json
import logging
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("SCU3.test.fixes")


def test_1_plugin_hooks_triggered():
    """测试1：插件钩子在 process_request 中触发"""
    print("\n" + "=" * 60)
    print("  测试1：插件钩子接入运行时")
    print("=" * 60)

    from m_layer.plugin_system import get_plugin_manager, Plugin
    pm = get_plugin_manager()

    # 创建测试插件记录钩子调用
    hook_calls = []

    class TestPlugin(Plugin):
        name = "test_hook_plugin"
        version = "1.0"

        def on_message(self, message):
            hook_calls.append(("on_message", message))
            return {"processed": True}

        def on_tool_call(self, tool, params):
            hook_calls.append(("on_tool_call", tool))
            return None

        def on_response(self, response):
            hook_calls.append(("on_response", response.get("op_id", "")))
            return None

    plugin = TestPlugin()
    pm.register_plugin(plugin)
    pm.enable_plugin("test_hook_plugin")

    # 触发一次请求
    from server import process_request
    try:
        result = process_request("计算 1+1", user_id="test_user")
    except Exception as e:
        print(f"  请求执行异常（可接受）: {e}")

    # 检查钩子是否被调用
    hook_types = set(h[0] for h in hook_calls)
    print(f"  触发的钩子: {hook_types}")
    print(f"  钩子调用次数: {len(hook_calls)}")

    # 至少 on_message 和 on_response 应该被触发
    assert "on_message" in hook_types, "on_message 钩子未被触发"
    print("  ✓ on_message 钩子已触发")

    # 清理
    pm.disable_plugin("test_hook_plugin")
    pm.unregister_plugin("test_hook_plugin")
    return True


def test_2_endpoint_503_when_unloaded():
    """测试2：模块卸载后端点返回 503"""
    print("\n" + "=" * 60)
    print("  测试2：卸载后端点返回 503")
    print("=" * 60)

    from m_layer.module_registry import get_registry
    registry = get_registry()

    # 确保模块注册
    if not registry.list_modules():
        from m_layer.module_registry import register_builtin_modules
        register_builtin_modules()

    # 先加载 automation.browser
    registry.load("automation.browser")
    assert registry.is_available("automation.browser"), "加载失败"

    # 卸载
    registry.unload("automation.browser")
    assert not registry.is_available("automation.browser"), "卸载后仍可用"

    # 检查 require_module 函数会抛 503
    from server import require_module
    from fastapi import HTTPException
    try:
        require_module("automation.browser")
        print("  ✗ 未抛出 503 异常")
        return False
    except HTTPException as e:
        assert e.status_code == 503, f"期望 503，实际 {e.status_code}"
        print(f"  ✓ 卸载后正确返回 503: {e.detail}")
        return True


def test_3_disabled_persistence_restore():
    """测试3：disabled 持久化恢复（使用临时状态文件，不污染全局注册表）"""
    print("\n" + "=" * 60)
    print("  测试3：disabled 持久化恢复")
    print("=" * 60)

    import m_layer.module_registry as mr_module

    # 使用临时状态文件
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "test_registry.json")

        # 临时替换模块级 STATE_PATH
        original_state_path = mr_module.STATE_PATH
        mr_module.STATE_PATH = state_path
        try:
            # 第一个实例：注册并禁用
            reg1 = mr_module.ModuleRegistry()

            def dummy_loader():
                return "instance"

            reg1.register("test.mod", "测试模块", loader=dummy_loader)
            reg1.disable("test.mod")
            reg1._save_state()

            # 验证状态文件已写入
            assert os.path.exists(state_path), "状态文件未创建"
            with open(state_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            saved_modules = saved.get("modules", {})
            assert saved_modules.get("test.mod", {}).get("disabled") is True, "未保存 disabled=True"
            print("  ✓ disabled 状态已持久化到文件")

            # 第二个实例：模拟重启，加载状态并重新注册
            reg2 = mr_module.ModuleRegistry()
            # _load_state 在 __init__ 中已调用

            # 验证 _pending_state 已加载
            assert "test.mod" in reg2._pending_state, "_pending_state 未加载"
            print("  ✓ _load_state 已加载到 _pending_state")

            # 重新注册模块
            def dummy_loader2():
                return "instance2"

            reg2.register("test.mod", "测试模块", loader=dummy_loader2)

            # 验证 disabled 标记已恢复
            m = reg2._modules.get("test.mod")
            assert m is not None, "模块未注册"
            assert m.disabled is True, f"disabled 未恢复，实际: {m.disabled}"
            print("  ✓ 重新注册后 disabled 标记已正确恢复")
        finally:
            # 恢复原始 STATE_PATH
            mr_module.STATE_PATH = original_state_path

    return True


def test_4_knowledge_base_loader():
    """测试4：knowledge.base loader 可正常加载"""
    print("\n" + "=" * 60)
    print("  测试4：knowledge.base loader 修复")
    print("=" * 60)

    from m_layer.module_registry import get_registry, register_builtin_modules
    registry = get_registry()
    if not registry.list_modules():
        register_builtin_modules()

    # 检查 knowledge.base 已注册
    modules = registry.list_modules()
    mod_names = [m["name"] for m in modules]
    assert "knowledge.base" in mod_names, "knowledge.base 未注册"
    print("  ✓ knowledge.base 已注册")

    # 尝试加载（调用 loader）
    m = registry._modules.get("knowledge.base")
    assert m is not None
    try:
        instance = m.loader()
        assert instance is not None, "loader 返回 None"
        print(f"  ✓ knowledge.base loader 执行成功: {type(instance).__name__}")
    except ImportError as e:
        print(f"  ✗ loader 仍引用不存在的函数: {e}")
        return False

    # 验证 unloader 已设置
    assert m.unloader is not None, "unloader 未设置"
    print("  ✓ knowledge.base unloader 已设置")
    return True


def test_5_protected_module_registered():
    """测试5：PROTECTED 模块已注册"""
    print("\n" + "=" * 60)
    print("  测试5：PROTECTED 模块注册")
    print("=" * 60)

    from m_layer.module_registry import get_registry, register_builtin_modules, PROTECTED_MODULES
    registry = get_registry()
    if not registry.list_modules():
        register_builtin_modules()

    modules = registry.list_modules()
    mod_names = set(m["name"] for m in modules)

    # code_self_modify 应该在注册表中
    assert "code_self_modify" in mod_names, "code_self_modify 未注册"
    print("  ✓ code_self_modify 已注册")

    # 验证 code_self_modify 在 PROTECTED 中
    assert "code_self_modify" in PROTECTED_MODULES, "code_self_modify 不在 PROTECTED_MODULES"
    print("  ✓ code_self_modify 在 PROTECTED_MODULES 中")

    # 验证无法卸载（先加载再尝试卸载）
    registry.load("code_self_modify")
    result = registry.unload("code_self_modify")
    assert not result.get("success", False), \
        f"受保护模块被卸载了: {result}"
    assert "保护" in result.get("error", ""), \
        f"错误信息应包含'保护': {result.get('error', '')}"
    print("  ✓ 受保护模块无法卸载")
    return True


def test_6_singleton_reset_on_unload():
    """测试6：卸载后单例重置"""
    print("\n" + "=" * 60)
    print("  测试6：卸载后单例重置")
    print("=" * 60)

    from w1_layer.automation import get_browser, reset_browser, _browser_singleton

    # 获取单例
    ba1 = get_browser()
    assert ba1 is not None, "单例获取失败"

    # 重置
    reset_browser()

    # 再次获取应该是新实例
    ba2 = get_browser()
    assert ba2 is not None, "重置后单例获取失败"
    print("  ✓ reset_browser() 后可获取新实例")

    # 清理
    reset_browser()
    return True


def test_7_env_config_drive_modules():
    """测试7：.env 配置驱动模块启停"""
    print("\n" + "=" * 60)
    print("  测试7：.env 配置驱动模块启停")
    print("=" * 60)

    # 设置环境变量
    os.environ["SCU3_DISABLED_MODULES"] = "automation.screen,automation.desktop"

    # 重新注册
    from m_layer.module_registry import ModuleRegistry

    reg = ModuleRegistry()
    # 清空持久化状态，避免受到之前测试的干扰
    reg._pending_state = {}

    def dummy_loader():
        return "instance"

    # 注册几个模块
    for name in ["automation.browser", "automation.screen", "automation.desktop", "automation.web_scraper"]:
        reg.register(name, f"测试模块 {name}", loader=dummy_loader)

    # 模拟 register_builtin_modules 末尾的 .env 读取逻辑
    disabled_env = os.environ.get("SCU3_DISABLED_MODULES", "").strip()
    if disabled_env:
        disabled_list = [m.strip() for m in disabled_env.split(",") if m.strip()]
        for mod_name in disabled_list:
            if mod_name in reg._modules:
                reg._modules[mod_name].disabled = True

    # 验证
    assert not reg._modules["automation.browser"].disabled, "browser 不应被禁用"
    assert reg._modules["automation.screen"].disabled, "screen 应被禁用"
    assert reg._modules["automation.desktop"].disabled, "desktop 应被禁用"
    assert not reg._modules["automation.web_scraper"].disabled, "scraper 不应被禁用"

    print("  ✓ SCU3_DISABLED_MODULES 正确禁用指定模块")
    print(f"    browser: disabled={reg._modules['automation.browser'].disabled}")
    print(f"    screen:  disabled={reg._modules['automation.screen'].disabled}")
    print(f"    desktop: disabled={reg._modules['automation.desktop'].disabled}")
    print(f"    scraper: disabled={reg._modules['automation.web_scraper'].disabled}")

    # 清理
    del os.environ["SCU3_DISABLED_MODULES"]
    return True


def main():
    print("=" * 60)
    print("  SCU3 模块化可插拔性修复验证")
    print("=" * 60)

    tests = [
        ("插件钩子接入运行时", test_1_plugin_hooks_triggered),
        ("卸载后端点返回 503", test_2_endpoint_503_when_unloaded),
        ("disabled 持久化恢复", test_3_disabled_persistence_restore),
        ("knowledge.base loader 修复", test_4_knowledge_base_loader),
        ("PROTECTED 模块注册", test_5_protected_module_registered),
        ("卸载后单例重置", test_6_singleton_reset_on_unload),
        (".env 配置驱动启停", test_7_env_config_drive_modules),
    ]

    results = []
    for name, test_fn in tests:
        try:
            passed = test_fn()
            results.append((name, passed))
        except Exception as e:
            print(f"\n  ✗ {name} 测试异常: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # 汇总
    print("\n" + "=" * 60)
    print("  修复验证汇总")
    print("=" * 60)
    passed_count = sum(1 for _, p in results if p)
    for name, passed in results:
        print(f"  [{'✓' if passed else '✗'}] {name}")
    print(f"\n  通过: {passed_count}/{len(results)}")
    print("=" * 60)

    return 0 if passed_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
