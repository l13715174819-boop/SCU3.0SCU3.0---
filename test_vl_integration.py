# -*- coding: utf-8 -*-
"""
test_vl_integration.py — Qwen2.5-VL-7B 视觉模型集成测试
============================================================
方案 A：文本模型（Qwen2.5-7B）与视觉模型（Qwen2.5-VL-7B）按需切换，不同时加载。

测试范围：
  1. 模块导入与依赖检查
  2. SUPPORTED_MODELS 中 VL 模型配置正确性
  3. switch_model_type / ensure_model_type 接口可用性（不实际加载）
  4. LLMClient.chat_with_image 接口可用性（不实际加载）
  5. server.py 视觉端点注册检查
  6. chat_with_image 在无模型时的降级返回

注意：本测试不实际加载 VL 模型（需要 12GB 显存），仅验证接口完整性。
实际加载测试请通过 server.py API 手动进行。

运行：
    python test_vl_integration.py
"""
import os
import sys
import json
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scu2.test.vl")

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def test_imports():
    """测试1：模块导入与依赖检查"""
    print("\n" + "=" * 60)
    print("测试1：模块导入与依赖检查")
    print("=" * 60)

    try:
        from m_layer import local_model
        print(f"  [OK] m_layer.local_model 导入成功")
    except Exception as e:
        print(f"  [FAIL] m_layer.local_model 导入失败: {e}")
        return False

    try:
        from m_layer import llm_client
        print(f"  [OK] m_layer.llm_client 导入成功")
    except Exception as e:
        print(f"  [FAIL] m_layer.llm_client 导入失败: {e}")
        return False

    # 依赖可用性
    print(f"\n  依赖状态：")
    print(f"    torch:           {local_model._TORCH_AVAILABLE}")
    print(f"    transformers:    {local_model._TRANSFORMERS_AVAILABLE}")
    print(f"    bitsandbytes:    {local_model._BITSANDBYTES_AVAILABLE}")
    print(f"    accelerate:      {local_model._ACCELERATE_AVAILABLE}")
    print(f"    qwen_vl:         {local_model._QWEN_VL_AVAILABLE}")

    # Pillow 检查（VL 推理必需）
    try:
        import PIL
        print(f"    PIL (Pillow):    True (v{PIL.__version__})")
    except ImportError:
        print(f"    PIL (Pillow):    False [警告：VL 推理时需要]")

    return True


def test_supported_models_config():
    """测试2：SUPPORTED_MODELS 中 VL 模型配置"""
    print("\n" + "=" * 60)
    print("测试2：SUPPORTED_MODELS VL 模型配置")
    print("=" * 60)

    from m_layer.local_model import SUPPORTED_MODELS

    vl_models = {k: v for k, v in SUPPORTED_MODELS.items() if v.get("model_type") == "vl"}
    text_models = {k: v for k, v in SUPPORTED_MODELS.items() if v.get("model_type") == "text"}

    print(f"\n  文本模型 ({len(text_models)} 个):")
    for name, cfg in text_models.items():
        print(f"    - {name}: {cfg.get('label')} (family={cfg.get('family')})")

    print(f"\n  视觉模型 ({len(vl_models)} 个):")
    for name, cfg in vl_models.items():
        print(f"    - {name}: {cfg.get('label')} (family={cfg.get('family')}, "
              f"ctx={cfg.get('context_length')}, mem={cfg.get('min_memory_gb')}GB)")

    # 断言：必须包含 qwen2-5-vl-7b 和 qwen2-5-vl-3b
    assert "qwen2-5-vl-7b" in vl_models, "缺少 qwen2-5-vl-7b"
    assert "qwen2-5-vl-3b" in vl_models, "缺少 qwen2-5-vl-3b"
    assert "qwen2-5-7b" in text_models, "缺少 qwen2-5-7b（文本基线）"

    # 校验 VL 模型配置字段
    for name in ("qwen2-5-vl-7b", "qwen2-5-vl-3b"):
        cfg = vl_models[name]
        assert cfg.get("model_type") == "vl", f"{name} model_type 应为 vl"
        assert cfg.get("family") == "qwen-vl", f"{name} family 应为 qwen-vl"
        assert cfg.get("model_id", "").startswith("Qwen/Qwen2.5-VL"), f"{name} model_id 异常"
        assert cfg.get("context_length", 0) >= 8192, f"{name} context_length 过小"

    print(f"\n  [OK] VL 模型配置验证通过")
    return True


def test_switch_model_type_interface():
    """测试3：switch_model_type / ensure_model_type 接口"""
    print("\n" + "=" * 60)
    print("测试3：模型类型切换接口")
    print("=" * 60)

    from m_layer.local_model import get_local_model
    client = get_local_model()

    # 3.1 非法类型
    result = client.switch_model_type("audio")
    assert not result.get("success"), "非法类型应失败"
    print(f"  [OK] 非法类型 'audio' 正确拒绝: {result.get('error')}")

    # 3.2 非法模型名
    result = client.switch_model_type("vl", model_name="nonexistent-model")
    assert not result.get("success"), "非法模型名应失败"
    print(f"  [OK] 非法模型名正确拒绝: {result.get('error')}")

    # 3.3 类型不匹配（用 text 模型名请求 vl 类型）
    result = client.switch_model_type("vl", model_name="qwen2-5-7b")
    assert not result.get("success"), "类型不匹配应失败"
    print(f"  [OK] 类型不匹配正确拒绝: {result.get('error')}")

    # 3.4 ensure_model_type 在未加载时的行为
    #     （未加载时切换需要实际加载，这里只检查返回结构）
    result = client.ensure_model_type("invalid_type")
    assert not result.get("switched"), "非法类型不应切换"
    print(f"  [OK] ensure_model_type('invalid_type') 正确拒绝")

    # 3.5 接口签名检查
    assert callable(getattr(client, "switch_model_type", None)), "switch_model_type 不可调用"
    assert callable(getattr(client, "ensure_model_type", None)), "ensure_model_type 不可调用"
    assert callable(getattr(client, "chat_with_image", None)), "chat_with_image 不可调用"
    assert callable(getattr(client, "is_vl_available", None)), "is_vl_available 不可调用"
    print(f"  [OK] 所有切换接口签名检查通过")

    return True


def test_llm_client_vl_interface():
    """测试4：LLMClient.chat_with_image / switch_model_type 接口"""
    print("\n" + "=" * 60)
    print("测试4：LLMClient 视觉接口")
    print("=" * 60)

    from m_layer.llm_client import get_client
    llm = get_client()

    # 接口存在性
    assert callable(getattr(llm, "chat_with_image", None)), "chat_with_image 不可调用"
    assert callable(getattr(llm, "switch_model_type", None)), "switch_model_type 不可调用"
    print(f"  [OK] LLMClient.chat_with_image 接口存在")
    print(f"  [OK] LLMClient.switch_model_type 接口存在")

    # 在未加载模型时调用，应返回错误（而非崩溃）
    result = llm.chat_with_image(
        prompt="测试",
        image={"path": "/nonexistent.png"},
        auto_switch=False,
    )
    print(f"\n  未加载模型时调用 chat_with_image 返回：")
    print(f"    success: {not result.get('error')}")
    print(f"    error:   {result.get('error')}")
    print(f"    mode:    {result.get('mode')}")
    assert result.get("error"), "未加载模型时应返回错误"
    print(f"  [OK] 未加载模型时降级返回正确")

    return True


def test_server_endpoints():
    """测试5：server.py 视觉端点注册"""
    print("\n" + "=" * 60)
    print("测试5：server.py 视觉端点注册")
    print("=" * 60)

    try:
        # 仅导入模块，不启动服务
        import server
    except Exception as e:
        print(f"  [FAIL] server 导入失败: {e}")
        return False

    # 获取 FastAPI 路由
    routes = []
    for route in server.app.routes:
        if hasattr(route, "path"):
            methods = getattr(route, "methods", set()) or set()
            routes.append((route.path, sorted(methods)))

    # 期望的新端点
    expected_endpoints = [
        ("/local-model/switch-type", "POST"),
        ("/vision/chat", "POST"),
        ("/vision/status", "GET"),
    ]

    print(f"\n  已注册路由数: {len(routes)}")
    print(f"\n  检查新增端点：")
    all_ok = True
    for path, method in expected_endpoints:
        found = any(path == r_path and method in r_methods for r_path, r_methods in routes)
        status = "[OK]" if found else "[FAIL]"
        if not found:
            all_ok = False
        print(f"    {status} {method:4s} {path}")

    # 列出所有 vision/local-model 相关端点
    print(f"\n  视觉/本地模型相关端点：")
    for path, methods in sorted(routes):
        if "vision" in path or "local-model" in path:
            print(f"    {','.join(methods):10s} {path}")

    return all_ok


def test_chat_with_image_no_model():
    """测试6：chat_with_image 在无 VL 模型时的降级返回"""
    print("\n" + "=" * 60)
    print("测试6：chat_with_image 降级行为")
    print("=" * 60)

    from m_layer.local_model import get_local_model
    client = get_local_model()

    # 确保未加载
    if client._model_loaded:
        print(f"  [SKIP] 当前已加载模型，跳过降级测试")
        return True

    # 调用 chat_with_image（无模型）
    result = client.chat_with_image(prompt="描述这张图", image={"path": "/nonexistent.png"})

    print(f"\n  返回结构：")
    for k, v in result.items():
        print(f"    {k}: {v}")

    assert result.get("error") in ("model_not_loaded", "model_not_vl", "processor_unavailable"), \
        f"未加载模型时 error 应为相关错误码，实际: {result.get('error')}"
    print(f"\n  [OK] 降级行为正确（error={result.get('error')}）")
    return True


def test_to_llm_compatible_vl():
    """测试7：to_llm_compatible 暴露 VL 能力"""
    print("\n" + "=" * 60)
    print("测试7：to_llm_compatible VL 能力暴露")
    print("=" * 60)

    from m_layer.local_model import get_local_model
    client = get_local_model()
    compat = client.to_llm_compatible()

    print(f"\n  to_llm_compatible 返回：")
    print(f"    type:          {compat.get('type')}")
    print(f"    available:     {compat.get('available')}")
    print(f"    vl_available:  {compat.get('vl_available')}")
    print(f"    model_name:    {compat.get('model_name')}")
    print(f"    model_type:    {compat.get('model_type')}")
    print(f"    methods:       {list(compat.get('methods', {}).keys())}")

    assert "vl_available" in compat, "缺少 vl_available 字段"
    assert "model_type" in compat, "缺少 model_type 字段"
    assert "chat_with_image" in compat.get("methods", {}), "methods 缺少 chat_with_image"
    assert "switch_model_type" in compat.get("methods", {}), "methods 缺少 switch_model_type"
    assert "ensure_model_type" in compat.get("methods", {}), "methods 缺少 ensure_model_type"
    print(f"\n  [OK] to_llm_compatible VL 能力暴露完整")
    return True


def main():
    print("=" * 60)
    print("SCU2 v5.1 — Qwen2.5-VL-7B 视觉模型集成测试")
    print("方案 A：文本/VL 模型按需切换，不同时加载")
    print("=" * 60)

    tests = [
        ("模块导入", test_imports),
        ("VL 模型配置", test_supported_models_config),
        ("切换接口", test_switch_model_type_interface),
        ("LLMClient 视觉接口", test_llm_client_vl_interface),
        ("server 端点注册", test_server_endpoints),
        ("降级行为", test_chat_with_image_no_model),
        ("LLM 兼容接口", test_to_llm_compatible_vl),
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

    # 汇总
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
