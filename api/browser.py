# -*- coding: utf-8 -*-
"""api/browser.py — 浏览器/桌面自动化路由

从 server.py 抽取的 20 个自动化路由：
  GET  /automation/status  — 获取所有自动化能力状态
  POST /browser/start      — 启动浏览器并导航到 URL
  POST /browser/navigate   — 导航到新 URL
  POST /browser/click      — 点击元素
  POST /browser/fill       — 填充输入框
  POST /browser/type       — 模拟键盘逐字输入
  POST /browser/press      — 按键
  POST /browser/scroll     — 滚动页面
  POST /browser/screenshot — 页面截图
  GET  /browser/text       — 提取页面文本
  GET  /browser/links      — 提取页面所有链接
  GET  /browser/status     — 浏览器状态
  POST /browser/stop       — 关闭浏览器
  POST /web/fetch          — 抓取网页正文
  GET  /web/status         — 网页抓取能力状态
  POST /screen/capture     — 屏幕截图
  GET  /screen/monitors    — 列出所有显示器
  GET  /screen/status      — 截屏能力状态
  POST /desktop/action     — 桌面控制操作（管理员）
  GET  /desktop/status     — 桌面控制状态
"""
import asyncio
import logging
from typing import List
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_api_key, verify_admin_key, require_module

logger = logging.getLogger("SCU3.api.browser")

router = APIRouter(tags=["browser"])


# ─── 请求模型 ────────────────────────────────
class BrowserNavigateRequest(BaseModel):
    url: str
    headless: bool = True
    wait_until: str = "domcontentloaded"  # load/domcontentloaded/networkidle
    viewport_width: int = 1280
    viewport_height: int = 720


class BrowserActionRequest(BaseModel):
    selector: str = ""
    value: str = ""
    key: str = ""
    pixels: int = 500
    direction: str = "down"  # down/up
    full_page: bool = False
    timeout: int = 30000
    delay: int = 50


class WebFetchRequest(BaseModel):
    url: str
    max_length: int = 10000
    article_mode: bool = False  # 文章正文模式


class ScreenCaptureRequest(BaseModel):
    monitor: int = 1
    left: int = 0
    top: int = 0
    width: int = 0  # 0=全屏
    height: int = 0
    save_to_file: bool = True


class DesktopActionRequest(BaseModel):
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


# ─── 自动化能力总状态 ────────────────────────────────
@router.get("/automation/status")
async def automation_status(api_key: str = Depends(verify_api_key)):
    """获取所有自动化能力状态"""
    try:
        from w1_layer.automation import automation_status as get_status
        return JSONResponse({"success": True, "status": get_status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 浏览器自动化 ─────────────────────────
@router.post("/browser/start")
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


@router.post("/browser/navigate")
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


@router.post("/browser/click")
async def browser_click(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """点击元素"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().click, req.selector, timeout=req.timeout)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/browser/fill")
async def browser_fill(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """填充输入框"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().fill, req.selector, req.value)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/browser/type")
async def browser_type(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """模拟键盘逐字输入"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().type_text, req.selector, req.value, delay=req.delay)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/browser/press")
async def browser_press(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """按键"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().press_key, req.key)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/browser/scroll")
async def browser_scroll(req: BrowserActionRequest, api_key: str = Depends(verify_api_key)):
    """滚动页面"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().scroll, req.pixels, req.direction)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/browser/screenshot")
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


@router.get("/browser/text")
async def browser_text(api_key: str = Depends(verify_api_key), selector: str = ""):
    """提取页面文本"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().extract_text, selector or None)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/browser/links")
async def browser_links(api_key: str = Depends(verify_api_key)):
    """提取页面所有链接"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        result = await asyncio.to_thread(get_browser().get_links)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/browser/status")
async def browser_status(api_key: str = Depends(verify_api_key)):
    """浏览器状态"""
    try:
        require_module("automation.browser")
        from w1_layer.automation import get_browser
        return JSONResponse({"success": True, "status": get_browser().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/browser/stop")
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
@router.post("/web/fetch")
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


@router.get("/web/status")
async def web_status(api_key: str = Depends(verify_api_key)):
    """网页抓取能力状态"""
    try:
        require_module("automation.web_scraper")
        from w1_layer.automation import get_web_scraper
        return JSONResponse({"success": True, "status": get_web_scraper().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 屏幕截图 ─────────────────────────
@router.post("/screen/capture")
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


@router.get("/screen/monitors")
async def screen_monitors(api_key: str = Depends(verify_api_key)):
    """列出所有显示器"""
    try:
        require_module("automation.screen")
        from w1_layer.automation import get_screen_capture
        result = get_screen_capture().list_monitors()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/screen/status")
async def screen_status(api_key: str = Depends(verify_api_key)):
    """截屏能力状态"""
    try:
        require_module("automation.screen")
        from w1_layer.automation import get_screen_capture
        return JSONResponse({"success": True, "status": get_screen_capture().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ─── 桌面控制 ─────────────────────────
@router.post("/desktop/action")
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


@router.get("/desktop/status")
async def desktop_status(api_key: str = Depends(verify_api_key)):
    """桌面控制状态"""
    try:
        require_module("automation.desktop")
        from w1_layer.automation import get_desktop_control
        return JSONResponse({"success": True, "status": get_desktop_control().status()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})
