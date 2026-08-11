# -*- coding: utf-8 -*-
"""
test_automation.py — 自动化能力集成测试
========================================
测试 v5.1 新增的四项能力：
  1. 浏览器自动化（Playwright）
  2. 屏幕截图（mss）
  3. 网页抓取（httpx + BeautifulSoup）
  4. 桌面控制（pyautogui）
  5. VL + 截屏联动端点

注意：
  - 浏览器测试会实际启动 chromium 并访问 example.com
  - 截屏测试会实际截取屏幕
  - 网页抓取测试会实际访问 httpbin.org
  - 桌面控制仅做状态查询，不做实际操作（避免干扰）
"""
import os
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scu2.test.automation")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def test_dependencies():
    """测试1：依赖检查"""
    print("\n" + "=" * 60)
    print("测试1：依赖检查")
    print("=" * 60)

    from w1_layer import automation

    deps = {
        "playwright": automation._PLAYWRIGHT_AVAILABLE,
        "mss": automation._MSS_AVAILABLE,
        "httpx": automation._HTTPX_AVAILABLE,
        "bs4": automation._BS4_AVAILABLE,
        "pyautogui": automation._PYAUTOGUI_AVAILABLE,
        "PIL": automation._PIL_AVAILABLE,
    }
    for k, v in deps.items():
        status = "[OK]" if v else "[FAIL]"
        print(f"  {status} {k}: {v}")

    expected = ["mss", "httpx", "bs4", "PIL"]
    missing = [k for k in expected if not deps.get(k)]
    if missing:
        print(f"\n  [FAIL] 缺少必需依赖: {missing}")
        return False

    print(f"\n  [OK] 核心依赖齐全（playwright/pyautogui 可选）")
    return True


def test_screen_capture():
    """测试2：屏幕截图"""
    print("\n" + "=" * 60)
    print("测试2：屏幕截图（mss）")
    print("=" * 60)

    from w1_layer.automation import get_screen_capture
    sc = get_screen_capture()

    if not sc.available:
        print("  [FAIL] 截屏能力不可用")
        return False

    print(f"  后端: {sc.status()['backend']}")

    # 列出显示器
    monitors = sc.list_monitors()
    print(f"  显示器: {monitors.get('count', 0)} 个")
    if monitors.get("success"):
        for m in monitors["monitors"][:3]:
            print(f"    - #{m['index']}: {m['width']}x{m['height']} @ ({m['left']},{m['top']})")

    # 实际截屏
    print(f"\n  执行截屏...")
    start = time.time()
    result = sc.capture_to_file()
    latency = time.time() - start

    if not result.get("success"):
        print(f"  [FAIL] 截屏失败: {result.get('error')}")
        return False

    path = result.get("path")
    b64_len = len(result.get("base64", ""))
    file_size = os.path.getsize(path) if path and os.path.exists(path) else 0

    print(f"  [OK] 截屏成功")
    print(f"    路径: {path}")
    print(f"    文件大小: {file_size/1024:.1f} KB")
    print(f"    base64 长度: {b64_len}")
    print(f"    耗时: {latency:.3f}s")

    assert file_size > 1000, "截图文件过小"
    assert b64_len > 1000, "base64 数据过小"
    return True


def test_web_scraper():
    """测试3：网页抓取"""
    print("\n" + "=" * 60)
    print("测试3：网页抓取（httpx + BeautifulSoup）")
    print("=" * 60)

    from w1_layer.automation import get_web_scraper
    ws = get_web_scraper()

    if not ws.available:
        print("  [FAIL] 网页抓取不可用")
        return False

    print(f"  后端: httpx + {ws.status()['parser']}")

    # 抓取 example.com（极简页面，适合测试）
    print(f"\n  抓取 https://example.com ...")
    start = time.time()
    result = ws.fetch("https://example.com", max_length=2000)
    latency = time.time() - start

    if not result.get("success"):
        print(f"  [FAIL] 抓取失败: {result.get('error')}")
        return False

    print(f"  [OK] 抓取成功")
    print(f"    URL: {result.get('url')}")
    print(f"    标题: {result.get('title', '')[:80]}")
    print(f"    状态码: {result.get('status_code')}")
    print(f"    内容长度: {result.get('content_length', 0)} 字符")
    print(f"    链接数: {result.get('links_count', 0)}")
    print(f"    图片数: {result.get('images_count', 0)}")
    print(f"    耗时: {latency:.3f}s")
    print(f"    内容预览: {result.get('content', '')[:150]}...")

    assert result.get("content_length", 0) > 0, "内容为空"
    assert result.get("status_code") == 200, f"状态码异常: {result.get('status_code')}"
    return True


def test_browser_automation():
    """测试4：浏览器自动化"""
    print("\n" + "=" * 60)
    print("测试4：浏览器自动化（Playwright）")
    print("=" * 60)

    from w1_layer.automation import get_browser
    ba = get_browser()

    if not ba.available:
        print("  [FAIL] playwright 不可用")
        return False

    # 启动浏览器
    print(f"\n  启动 chromium (headless)...")
    start = time.time()
    result = ba.start(headless=True)
    latency = time.time() - start

    if not result.get("success"):
        print(f"  [FAIL] 启动失败: {result.get('error')}")
        return False

    print(f"  [OK] 浏览器已启动 (耗时 {latency:.2f}s)")

    # 导航
    print(f"\n  导航到 https://example.com ...")
    start = time.time()
    result = ba.navigate("https://example.com")
    latency = time.time() - start

    if not result.get("success"):
        print(f"  [FAIL] 导航失败: {result.get('error')}")
        ba.stop()
        return False

    print(f"  [OK] 导航成功 (耗时 {latency:.2f}s)")
    print(f"    标题: {result.get('title')}")
    print(f"    状态: {result.get('status')}")

    # 提取文本
    text_result = ba.extract_text()
    if text_result.get("success"):
        text = text_result.get("text", "")
        print(f"  [OK] 文本提取: {len(text)} 字符")
        print(f"    预览: {text[:150]}...")

    # 截图
    print(f"\n  页面截图...")
    screenshot = ba.screenshot()
    if screenshot.get("success"):
        print(f"  [OK] 截图成功: {screenshot.get('path')}")

    # 关闭
    print(f"\n  关闭浏览器...")
    ba.stop()
    print(f"  [OK] 浏览器已关闭")

    return True


def test_desktop_control_status():
    """测试5：桌面控制状态（不实际操作）"""
    print("\n" + "=" * 60)
    print("测试5：桌面控制（仅状态查询）")
    print("=" * 60)

    from w1_layer.automation import get_desktop_control
    dc = get_desktop_control()

    if not dc.available:
        print("  [SKIP] pyautogui 不可用，跳过")
        return True

    status = dc.status()
    print(f"  available: {status.get('available')}")
    print(f"  failsafe: {status.get('failsafe')}")
    print(f"  pause: {status.get('pause')}")

    size = dc.screen_size()
    if size.get("success"):
        print(f"  屏幕分辨率: {size.get('width')}x{size.get('height')}")

    pos = dc.mouse_position()
    if pos.get("success"):
        print(f"  鼠标位置: ({pos.get('x')}, {pos.get('y')})")

    print(f"  [OK] 桌面控制可用")
    return True


def test_server_endpoints():
    """测试6：server.py 端点注册"""
    print("\n" + "=" * 60)
    print("测试6：server.py 自动化端点注册")
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

    # 期望的新端点
    expected = [
        # 浏览器
        ("/browser/start", "POST"),
        ("/browser/navigate", "POST"),
        ("/browser/click", "POST"),
        ("/browser/fill", "POST"),
        ("/browser/type", "POST"),
        ("/browser/press", "POST"),
        ("/browser/scroll", "POST"),
        ("/browser/screenshot", "POST"),
        ("/browser/text", "GET"),
        ("/browser/links", "GET"),
        ("/browser/status", "GET"),
        ("/browser/stop", "POST"),
        # 网页
        ("/web/fetch", "POST"),
        ("/web/status", "GET"),
        # 屏幕截图
        ("/screen/capture", "POST"),
        ("/screen/monitors", "GET"),
        ("/screen/status", "GET"),
        # 桌面
        ("/desktop/action", "POST"),
        ("/desktop/status", "GET"),
        # VL+截屏联动
        ("/vision/analyze-screen", "POST"),
        # 总状态
        ("/automation/status", "GET"),
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


def test_automation_status():
    """测试7：自动化能力总状态"""
    print("\n" + "=" * 60)
    print("测试7：自动化能力总状态")
    print("=" * 60)

    from w1_layer.automation import automation_status
    status = automation_status()

    for category, info in status.items():
        print(f"\n  {category}:")
        if isinstance(info, dict):
            for k, v in info.items():
                print(f"    {k}: {v}")

    # 至少 screen_capture 和 web_scraper 应该可用
    assert status["screen_capture"]["available"], "屏幕截图不可用"
    assert status["web_scraper"]["available"], "网页抓取不可用"
    print(f"\n  [OK] 核心能力（截屏+抓取）可用")
    return True


def main():
    print("=" * 60)
    print("SCU2 v5.1 — 自动化能力集成测试")
    print("浏览器/截屏/网页抓取/桌面控制")
    print("=" * 60)

    tests = [
        ("依赖检查", test_dependencies),
        ("屏幕截图", test_screen_capture),
        ("网页抓取", test_web_scraper),
        ("浏览器自动化", test_browser_automation),
        ("桌面控制状态", test_desktop_control_status),
        ("server 端点注册", test_server_endpoints),
        ("自动化总状态", test_automation_status),
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
