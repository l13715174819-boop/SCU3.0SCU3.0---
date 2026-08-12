# -*- coding: utf-8 -*-
"""
w1_layer/automation.py — 自动化能力集（W1层）
================================================
v5.1 新增：补齐 SCU3 "像 Agent 一样看屏幕/开浏览器/抓网页/控桌面" 的能力。

包含四个独立子模块（互不依赖，可单独使用）：

  1. BrowserAutomation    — 浏览器自动化（Playwright）
     - 打开网页、点击、输入、滚动、截图、提取文本、执行 JS
     - 支持 headless 与有头模式
     - 单例管理浏览器进程，避免重复启动

  2. ScreenCapture       — 屏幕截图（mss）
     - 全屏 / 区域 / 指定显示器截图
     - 返回 PIL.Image 或保存到文件
     - 高性能（mss 比 PIL.ImageGrab 快 5-10x）

  3. WebScraper          — 网页正文抓取（httpx + BeautifulSoup）
     - 比 extended_tools.web_fetch 更强：识别正文、去广告、保留结构
     - 自动处理编码、重定向、gzip
     - 支持提取标题、正文、链接、图片、元数据

  4. DesktopControl      — 桌面 GUI 控制（pyautogui）
     - 鼠标移动/点击/拖拽、键盘输入、快捷键
     - 屏幕分辨率、鼠标位置查询
     - 安全限制：FAIL-SAFE（鼠标移到角落立即终止）

架构归属：W1层（执行层扩展）
依赖方向：W1层→D层（只读axioms），W2层（感知调用）
依赖：可选 playwright / mss / httpx / beautifulsoup4 / pyautogui / Pillow
"""
import os
import time
import base64
import logging
import threading
from typing import Optional, List, Dict, Any, Tuple, Union

logger = logging.getLogger("SCU3.w1.automation")

# 项目路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "SCU3_data")
SCREENSHOT_DIR = os.path.join(DATA_DIR, "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


# ─── 依赖可选导入 ────────────────────────────────────
_PLAYWRIGHT_AVAILABLE = False
_MSS_AVAILABLE = False
_HTTPX_AVAILABLE = False
_BS4_AVAILABLE = False
_PYAUTOGUI_AVAILABLE = False
_PIL_AVAILABLE = False

try:
    from PIL import Image
    from PIL import ImageGrab
    _PIL_AVAILABLE = True
except ImportError:
    logger.debug("PIL 不可用，截图仅能保存到文件")

try:
    import mss
    _MSS_AVAILABLE = True
except ImportError:
    logger.debug("mss 不可用，截屏功能将降级到 PIL.ImageGrab")

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    logger.debug("httpx 不可用，网页抓取将降级到 urllib")

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    logger.debug("BeautifulSoup 不可用，网页解析将降级到正则")

try:
    import pyautogui
    _PYAUTOGUI_AVAILABLE = True
    # 启用 FAIL-SAFE：鼠标移到屏幕角落(0,0)立即终止
    pyautogui.FAILSAFE = True
    # 缓慢动画关闭（更快响应）
    pyautogui.PAUSE = 0.1
except ImportError:
    logger.debug("pyautogui 不可用，桌面控制功能不可用")

try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    logger.debug("playwright 不可用，浏览器自动化功能不可用")


# ═══════════════════════════════════════════════════════════════
#  1. 浏览器自动化（Playwright）
# ═══════════════════════════════════════════════════════════════

class BrowserAutomation:
    """浏览器自动化客户端（Playwright）

    用法：
        ba = BrowserAutomation()
        ba.start(headless=True)
        page = ba.navigate("https://example.com")
        text = ba.extract_text()
        ba.screenshot("out.png")
        ba.click("button#submit")
        ba.fill("input[name=q]", "搜索词")
        ba.stop()
    """

    def __init__(self, browser_type: str = "chromium", default_timeout: int = 30000):
        """初始化

        Args:
            browser_type: chromium / firefox / webkit
            default_timeout: 默认操作超时（毫秒）
        """
        self.browser_type = browser_type
        self.default_timeout = default_timeout
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = threading.RLock()
        self._started = False

    @property
    def available(self) -> bool:
        return _PLAYWRIGHT_AVAILABLE

    @property
    def started(self) -> bool:
        return self._started

    def start(self, headless: bool = True, viewport: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        """启动浏览器

        Args:
            headless: 是否无头模式
            viewport: 视口尺寸 {"width": 1280, "height": 720}

        Returns:
            {success, browser_type, headless, error}
        """
        if not _PLAYWRIGHT_AVAILABLE:
            return {"success": False, "error": "playwright 未安装，请执行: pip install playwright && python -m playwright install chromium"}
        with self._lock:
            if self._started:
                return {"success": True, "browser_type": self.browser_type, "headless": headless, "message": "浏览器已启动"}
            try:
                self._playwright = sync_playwright().start()
                browser_launcher = getattr(self._playwright, self.browser_type)
                self._browser = browser_launcher.launch(headless=headless)
                self._context = self._browser.new_context(
                    viewport=viewport or {"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                )
                self._context.set_default_timeout(self.default_timeout)
                self._page = self._context.new_page()
                self._started = True
                logger.info(f"浏览器已启动: {self.browser_type} (headless={headless})")
                return {"success": True, "browser_type": self.browser_type, "headless": headless, "error": None}
            except Exception as e:
                err = str(e)
                logger.error(f"启动浏览器失败: {err}")
                if "Executable doesn't exist" in err or "playwright install" in err:
                    err += " | 请执行: python -m playwright install chromium"
                return {"success": False, "error": err}

    def stop(self) -> Dict[str, Any]:
        """关闭浏览器，释放资源"""
        with self._lock:
            closed = []
            try:
                if self._page:
                    self._page.close()
                    closed.append("page")
                if self._context:
                    self._context.close()
                    closed.append("context")
                if self._browser:
                    self._browser.close()
                    closed.append("browser")
                if self._playwright:
                    self._playwright.stop()
                    closed.append("playwright")
            except Exception as e:
                logger.debug(f"关闭浏览器组件异常: {e}")
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            self._started = False
        logger.info(f"浏览器已关闭: {closed}")
        return {"success": True, "closed": closed}

    def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Dict[str, Any]:
        """导航到 URL

        Args:
            url: 目标网址
            wait_until: 等待事件 (load/domcontentloaded/networkidle)

        Returns:
            {success, url, title, status, error}
        """
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            response = self._page.goto(url, wait_until=wait_until)
            title = self._page.title()
            status = response.status if response else None
            return {"success": True, "url": url, "title": title, "status": status, "error": None}
        except Exception as e:
            return {"success": False, "url": url, "error": str(e)}

    def click(self, selector: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """点击元素

        Args:
            selector: CSS 选择器（如 "button#submit" / "text=登录"）
            timeout: 超时毫秒
        """
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            self._page.click(selector, timeout=timeout or self.default_timeout)
            return {"success": True, "selector": selector, "error": None}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    def fill(self, selector: str, value: str) -> Dict[str, Any]:
        """在输入框填入文本（覆盖原有内容）"""
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            self._page.fill(selector, value)
            return {"success": True, "selector": selector, "value_len": len(value), "error": None}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    def type_text(self, selector: str, text: str, delay: int = 50) -> Dict[str, Any]:
        """模拟键盘逐字输入（更像真人）

        Args:
            selector: 目标输入框
            text: 要输入的文本
            delay: 每个字符间隔毫秒
        """
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            self._page.type(selector, text, delay=delay)
            return {"success": True, "selector": selector, "error": None}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    def press_key(self, key: str) -> Dict[str, Any]:
        """按键（如 "Enter" / "Escape" / "Control+a"）"""
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            self._page.keyboard.press(key)
            return {"success": True, "key": key, "error": None}
        except Exception as e:
            return {"success": False, "key": key, "error": str(e)}

    def scroll(self, pixels: int = 500, direction: str = "down") -> Dict[str, Any]:
        """滚动页面

        Args:
            pixels: 滚动像素数
            direction: down/up
        """
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            delta = -pixels if direction == "up" else pixels
            self._page.mouse.wheel(0, delta)
            time.sleep(0.3)  # 等待渲染
            return {"success": True, "pixels": pixels, "direction": direction, "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def screenshot(self, path: Optional[str] = None, full_page: bool = False,
                   selector: Optional[str] = None) -> Dict[str, Any]:
        """截图

        Args:
            path: 保存路径（为空则保存到 screenshots/ 目录，文件名时间戳）
            full_page: 是否截整页
            selector: 仅截取指定元素（与 full_page 互斥）

        Returns:
            {success, path, base64, error}
        """
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        if not path:
            ts = int(time.time() * 1000)
            path = os.path.join(SCREENSHOT_DIR, f"browser_{ts}.png")
        try:
            if selector:
                self._page.locator(selector).screenshot(path=path)
            else:
                self._page.screenshot(path=path, full_page=full_page)
            # 读取为 base64（供 VL 模型使用）
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return {"success": True, "path": path, "base64": b64, "error": None}
        except Exception as e:
            return {"success": False, "path": path, "error": str(e)}

    def extract_text(self, selector: Optional[str] = None) -> Dict[str, Any]:
        """提取页面文本

        Args:
            selector: 仅提取指定元素文本（为空提取整页）

        Returns:
            {success, text, length, error}
        """
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            if selector:
                text = self._page.locator(selector).inner_text()
            else:
                text = self._page.inner_text("body")
            return {"success": True, "text": text, "length": len(text), "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def extract_html(self, selector: Optional[str] = None) -> Dict[str, Any]:
        """提取页面 HTML"""
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            if selector:
                html = self._page.locator(selector).inner_html()
            else:
                html = self._page.content()
            return {"success": True, "html": html, "length": len(html), "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute_js(self, script: str, arg: Optional[Any] = None) -> Dict[str, Any]:
        """执行 JavaScript 并返回结果

        Args:
            script: JS 代码（最后一行表达式的值作为返回值）
            arg: 传入脚本的参数
        """
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            result = self._page.evaluate(script, arg)
            return {"success": True, "result": result, "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_links(self) -> Dict[str, Any]:
        """提取页面所有链接"""
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            links = self._page.eval_on_selector_all(
                "a",
                "els => els.map(e => ({text: e.innerText.trim(), href: e.href})).filter(e => e.href)"
            )
            return {"success": True, "links": links, "count": len(links), "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_title(self) -> Dict[str, Any]:
        """获取当前页面标题"""
        if not self._require_started():
            return {"success": False, "error": "浏览器未启动"}
        try:
            return {"success": True, "title": self._page.title(), "url": self._page.url, "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def status(self) -> Dict[str, Any]:
        """浏览器状态"""
        return {
            "available": _PLAYWRIGHT_AVAILABLE,
            "started": self._started,
            "browser_type": self.browser_type if self._started else None,
            "current_url": self._page.url if self._page else None,
            "title": None,
            "default_timeout": self.default_timeout,
        }

    def _require_started(self) -> bool:
        if not self._started or self._page is None:
            return False
        return True


# ═══════════════════════════════════════════════════════════════
#  2. 屏幕截图（mss）
# ═══════════════════════════════════════════════════════════════

class ScreenCapture:
    """屏幕截图客户端（mss，性能优于 PIL.ImageGrab）

    用法：
        sc = ScreenCapture()
        img = sc.capture_full()              # 返回 PIL.Image
        path = sc.capture_to_file("out.png") # 保存到文件
        img = sc.capture_region(100, 100, 500, 400)  # 区域截图
    """

    def __init__(self):
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return _MSS_AVAILABLE or _PIL_AVAILABLE

    def capture_full(self, monitor: int = 1) -> Dict[str, Any]:
        """全屏截图

        Args:
            monitor: 显示器编号（1=主屏，2=副屏）

        Returns:
            {success, image, width, height, base64, error}
        """
        with self._lock:
            try:
                if _MSS_AVAILABLE:
                    with mss.mss() as sct:
                        sct_img = sct.grab(sct.monitors[monitor])
                        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                elif _PIL_AVAILABLE:
                    img = ImageGrab.grab()  # type: ignore
                else:
                    return {"success": False, "error": "mss 和 PIL 均不可用"}

                # 转 base64
                import io
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")

                return {
                    "success": True,
                    "width": img.width,
                    "height": img.height,
                    "base64": b64,
                    "error": None,
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

    def capture_to_file(self, path: Optional[str] = None, monitor: int = 1,
                        region: Optional[Tuple[int, int, int, int]] = None) -> Dict[str, Any]:
        """截图并保存到文件

        Args:
            path: 保存路径（为空自动生成）
            monitor: 显示器编号
            region: 区域 (left, top, width, height)

        Returns:
            {success, path, base64, error}
        """
        if not path:
            ts = int(time.time() * 1000)
            suffix = f"region_{ts}" if region else f"full_{ts}"
            path = os.path.join(SCREENSHOT_DIR, f"screen_{suffix}.png")

        with self._lock:
            try:
                if _MSS_AVAILABLE:
                    with mss.mss() as sct:
                        if region:
                            left, top, width, height = region
                            sct_img = sct.grab({"left": left, "top": top, "width": width, "height": height})
                        else:
                            sct_img = sct.grab(sct.monitors[monitor])
                        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                        img.save(path, "PNG")
                elif _PIL_AVAILABLE:
                    if region:
                        left, top, width, height = region
                        img = ImageGrab.grab(bbox=(left, top, left + width, top + height))  # type: ignore
                    else:
                        img = ImageGrab.grab()  # type: ignore
                    img.save(path, "PNG")
                else:
                    return {"success": False, "error": "mss 和 PIL 均不可用"}

                # 读取 base64
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")

                return {"success": True, "path": path, "base64": b64, "error": None}
            except Exception as e:
                return {"success": False, "path": path, "error": str(e)}

    def capture_region(self, left: int, top: int, width: int, height: int,
                       path: Optional[str] = None) -> Dict[str, Any]:
        """区域截图

        Args:
            left, top: 区域左上角坐标
            width, height: 区域宽高
            path: 保存路径（为空返回 base64 不保存）
        """
        return self.capture_to_file(path=path, region=(left, top, width, height))

    def list_monitors(self) -> Dict[str, Any]:
        """列出所有显示器"""
        try:
            if _MSS_AVAILABLE:
                with mss.mss() as sct:
                    monitors = []
                    for i, m in enumerate(sct.monitors):
                        monitors.append({
                            "index": i,
                            "left": m["left"], "top": m["top"],
                            "width": m["width"], "height": m["height"],
                        })
                    return {"success": True, "monitors": monitors, "count": len(monitors), "error": None}
            return {"success": False, "error": "mss 不可用，无法列出显示器"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def status(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "backend": "mss" if _MSS_AVAILABLE else ("PIL.ImageGrab" if _PIL_AVAILABLE else "none"),
            "screenshot_dir": SCREENSHOT_DIR,
        }


# ═══════════════════════════════════════════════════════════════
#  3. 网页正文抓取（httpx + BeautifulSoup）
# ═══════════════════════════════════════════════════════════════

class WebScraper:
    """网页正文抓取客户端

    比 extended_tools.web_fetch 增强：
      - httpx 性能更好，支持 HTTP/2、连接池
      - BeautifulSoup 精准提取正文，去除导航/广告/脚本
      - 自动识别编码
      - 提取结构化数据：标题、正文、链接、图片、元数据

    用法：
        ws = WebScraper()
        result = ws.fetch("https://example.com")
        result = ws.fetch_article("https://news.example.com/article/123")
    """

    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    def __init__(self, timeout: int = 30, follow_redirects: bool = True, max_retries: int = 3, retry_backoff: float = 1.5):
        """初始化网页抓取器

        Args:
            timeout: 单次请求超时（秒）
            follow_redirects: 是否跟随重定向
            max_retries: 最大重试次数（含首次）
            retry_backoff: 重试退避因子（每次等待 backoff^attempt 秒）
        """
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    @property
    def available(self) -> bool:
        return _HTTPX_AVAILABLE

    def _http_get(self, url: str) -> Dict[str, Any]:
        """带重试的 HTTP GET

        Returns:
            {success, html, status_code, final_url, content_type, attempts, error}
        """
        import time as _time
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if _HTTPX_AVAILABLE:
                    # 分阶段超时：连接 10s，读取 20s，总 30s
                    timeout_cfg = httpx.Timeout(
                        connect=10.0, read=max(20.0, self.timeout * 0.7),
                        write=10.0, pool=5.0,
                    )
                    with httpx.Client(timeout=timeout_cfg, follow_redirects=self.follow_redirects) as client:
                        resp = client.get(url, headers=self.DEFAULT_HEADERS)
                        return {
                            "success": True,
                            "html": resp.text,
                            "status_code": resp.status_code,
                            "final_url": str(resp.url),
                            "content_type": resp.headers.get("content-type", ""),
                            "attempts": attempt,
                            "error": None,
                        }
                else:
                    import urllib.request
                    req = urllib.request.Request(url, headers=self.DEFAULT_HEADERS)
                    with urllib.request.urlopen(req, timeout=self.timeout) as r:
                        html = r.read().decode("utf-8", errors="ignore")
                        return {
                            "success": True,
                            "html": html,
                            "status_code": r.status,
                            "final_url": r.url,
                            "content_type": r.headers.get("content-type", ""),
                            "attempts": attempt,
                            "error": None,
                        }
            except Exception as e:
                last_error = str(e)
                logger.debug(f"HTTP GET 失败 (attempt {attempt}/{self.max_retries}): {e}")
                # 最后一次不再等待
                if attempt < self.max_retries:
                    wait = self.retry_backoff ** attempt
                    _time.sleep(wait)
        return {"success": False, "attempts": self.max_retries, "error": last_error}

    def fetch(self, url: str, max_length: int = 10000) -> Dict[str, Any]:
        """抓取网页（基础版：返回清理后的纯文本）

        Args:
            url: 目标 URL
            max_length: 最大返回字符数

        Returns:
            {success, url, title, content, links, images, status_code, error}
        """
        if not url:
            return {"success": False, "url": url, "error": "URL 为空"}
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # 带重试的 HTTP GET
        http_result = self._http_get(url)
        if not http_result.get("success"):
            return {"success": False, "url": url, "error": http_result.get("error"),
                    "attempts": http_result.get("attempts", 1)}
        html = http_result["html"]
        status_code = http_result["status_code"]
        final_url = http_result["final_url"]
        content_type = http_result["content_type"]
        attempts = http_result.get("attempts", 1)

        try:
            # 解析 HTML
            if _BS4_AVAILABLE and "html" in content_type.lower():
                soup = BeautifulSoup(html, "lxml" if _BS4_AVAILABLE else "html.parser")

                # 移除 script/style/nav/footer
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()

                title = soup.title.string.strip() if soup.title and soup.title.string else ""
                content = soup.get_text(separator="\n", strip=True)
                # 压缩多余空行
                content = "\n".join(line.strip() for line in content.split("\n") if line.strip())

                # 提取链接
                links = []
                for a in soup.find_all("a", href=True):
                    text = a.get_text(strip=True)
                    href = a["href"]
                    if href and not href.startswith(("javascript:", "#", "mailto:")):
                        links.append({"text": text[:100], "href": href})
                    if len(links) >= 100:
                        break

                # 提取图片
                images = []
                for img in soup.find_all("img", src=True):
                    alt = img.get("alt", "")
                    src = img["src"]
                    if src:
                        images.append({"alt": alt, "src": src})
                    if len(images) >= 50:
                        break

                # 元数据
                meta = {}
                for m in soup.find_all("meta"):
                    name = m.get("name") or m.get("property")
                    content_m = m.get("content")
                    if name and content_m:
                        meta[name] = content_m[:200]

                return {
                    "success": True,
                    "url": final_url,
                    "title": title,
                    "content": content[:max_length],
                    "content_length": len(content),
                    "truncated": len(content) > max_length,
                    "links": links,
                    "links_count": len(links),
                    "images": images,
                    "images_count": len(images),
                    "meta": meta,
                    "status_code": status_code,
                    "content_type": content_type,
                    "attempts": attempts,
                    "error": None,
                }
            else:
                # 非 HTML 或 BeautifulSoup 不可用，正则清理
                import re
                text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S)
                text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return {
                    "success": True,
                    "url": final_url,
                    "title": "",
                    "content": text[:max_length],
                    "content_length": len(text),
                    "truncated": len(text) > max_length,
                    "status_code": status_code,
                    "content_type": content_type,
                    "attempts": attempts,
                    "error": None,
                }
        except Exception as e:
            return {"success": False, "url": url, "error": str(e)}

    def fetch_article(self, url: str, max_length: int = 20000) -> Dict[str, Any]:
        """抓取文章正文（增强版：识别 main/article 标签，去除侧边栏）

        Args:
            url: 文章 URL
            max_length: 最大返回字符数
        """
        if not _BS4_AVAILABLE:
            return self.fetch(url, max_length)

        if not url:
            return {"success": False, "url": url, "error": "URL 为空"}
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # 带重试的 HTTP GET
        http_result = self._http_get(url)
        if not http_result.get("success"):
            return {"success": False, "url": url, "error": http_result.get("error"),
                    "attempts": http_result.get("attempts", 1)}
        html = http_result["html"]
        status_code = http_result["status_code"]
        final_url = http_result["final_url"]
        attempts = http_result.get("attempts", 1)

        try:
            soup = BeautifulSoup(html, "lxml")

            # 优先从 main/article 标签提取
            article = soup.find("article") or soup.find("main") or soup.find("div", class_=lambda c: c and any(
                x in str(c).lower() for x in ["article", "content", "post", "entry"]
            ))

            if article:
                # 移除文章内的广告/相关推荐
                for tag in article.find_all(["script", "style", "aside", "nav"]):
                    tag.decompose()
                content = article.get_text(separator="\n", strip=True)
            else:
                # 退回到 body
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                content = soup.get_text(separator="\n", strip=True)

            content = "\n".join(line.strip() for line in content.split("\n") if line.strip())

            title = soup.title.string.strip() if soup.title and soup.title.string else ""

            # 元数据
            meta = {}
            for m in soup.find_all("meta"):
                name = m.get("name") or m.get("property")
                content_m = m.get("content")
                if name and content_m:
                    meta[name] = content_m[:200]

            return {
                "success": True,
                "url": final_url,
                "title": title,
                "content": content[:max_length],
                "content_length": len(content),
                "truncated": len(content) > max_length,
                "meta": meta,
                "status_code": status_code,
                "attempts": attempts,
                "error": None,
            }
        except Exception as e:
            return {"success": False, "url": url, "error": str(e)}

    def status(self) -> Dict[str, Any]:
        return {
            "available": _HTTPX_AVAILABLE,
            "parser": "BeautifulSoup+lxml" if _BS4_AVAILABLE else ("BeautifulSoup" if _BS4_AVAILABLE else "regex"),
            "timeout": self.timeout,
            "follow_redirects": self.follow_redirects,
            "max_retries": self.max_retries,
            "retry_backoff": self.retry_backoff,
        }


# ═══════════════════════════════════════════════════════════════
#  4. 桌面控制（pyautogui）
# ═══════════════════════════════════════════════════════════════

class DesktopControl:
    """桌面 GUI 控制客户端（pyautogui）

    用法：
        dc = DesktopControl()
        dc.click(100, 200)
        dc.type_text("hello world")
        dc.hot_key("ctrl", "c")
        size = dc.screen_size()
    """

    def __init__(self, move_duration: float = 0.3):
        """初始化

        Args:
            move_duration: 鼠标移动动画时长（秒）
        """
        self.move_duration = move_duration
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return _PYAUTOGUI_AVAILABLE

    def screen_size(self) -> Dict[str, Any]:
        """获取屏幕分辨率"""
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        try:
            size = pyautogui.size()
            return {"success": True, "width": size.width, "height": size.height, "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mouse_position(self) -> Dict[str, Any]:
        """获取当前鼠标位置"""
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        try:
            pos = pyautogui.position()
            return {"success": True, "x": pos.x, "y": pos.y, "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def move_to(self, x: int, y: int) -> Dict[str, Any]:
        """移动鼠标到绝对坐标"""
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        try:
            with self._lock:
                pyautogui.moveTo(x, y, duration=self.move_duration)
            return {"success": True, "x": x, "y": y, "error": None}
        except pyautogui.FailSafeException:
            return {"success": False, "error": "FAIL-SAFE 触发（鼠标移到角落）"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def click(self, x: Optional[int] = None, y: Optional[int] = None,
              button: str = "left", clicks: int = 1, interval: float = 0.1) -> Dict[str, Any]:
        """点击鼠标

        Args:
            x, y: 坐标（为空则点击当前位置）
            button: left/right/middle
            clicks: 点击次数
            interval: 多次点击间隔
        """
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        try:
            with self._lock:
                pyautogui.click(x=x, y=y, button=button, clicks=clicks, interval=interval)
            return {"success": True, "x": x, "y": y, "button": button, "clicks": clicks, "error": None}
        except pyautogui.FailSafeException:
            return {"success": False, "error": "FAIL-SAFE 触发"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        """双击"""
        return self.click(x=x, y=y, clicks=2, interval=0.05)

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        """右键点击"""
        return self.click(x=x, y=y, button="right")

    def drag(self, x: int, y: int, duration: float = 0.5,
             button: str = "left") -> Dict[str, Any]:
        """拖拽（从当前位置拖到相对偏移）

        Args:
            x, y: 相对偏移量
            duration: 拖拽时长
            button: 鼠标按键
        """
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        try:
            with self._lock:
                pyautogui.drag(x, y, duration=duration, button=button)
            return {"success": True, "dx": x, "dy": y, "error": None}
        except pyautogui.FailSafeException:
            return {"success": False, "error": "FAIL-SAFE 触发"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def scroll(self, clicks: int, x: Optional[int] = None,
               y: Optional[int] = None) -> Dict[str, Any]:
        """滚轮滚动

        Args:
            clicks: 滚动格数（正=上，负=下）
            x, y: 在指定位置滚动（为空则在当前位置）
        """
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        try:
            with self._lock:
                pyautogui.scroll(clicks, x=x, y=y)
            return {"success": True, "clicks": clicks, "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def type_text(self, text: str, interval: float = 0.05) -> Dict[str, Any]:
        """键盘输入文本

        Args:
            text: 要输入的文本
            interval: 每个按键间隔
        """
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        try:
            with self._lock:
                pyautogui.typewrite(text, interval=interval)
            return {"success": True, "text_len": len(text), "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def press(self, key: str) -> Dict[str, Any]:
        """按下并释放单个键

        Args:
            key: 键名（如 "enter" / "escape" / "tab" / "space" / "backspace"）
        """
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        try:
            with self._lock:
                pyautogui.press(key)
            return {"success": True, "key": key, "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def hot_key(self, *keys: str) -> Dict[str, Any]:
        """组合快捷键

        Args:
            keys: 键序列（如 hot_key("ctrl", "c")）
        """
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        try:
            with self._lock:
                pyautogui.hotkey(*keys)
            return {"success": True, "keys": list(keys), "error": None}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def screenshot(self, path: Optional[str] = None,
                   region: Optional[Tuple[int, int, int, int]] = None) -> Dict[str, Any]:
        """截图（pyautogui 内置，等价于 ScreenCapture 但接口统一）

        Args:
            path: 保存路径
            region: 区域 (left, top, width, height)
        """
        if not _PYAUTOGUI_AVAILABLE:
            return {"success": False, "error": "pyautogui 不可用"}
        if not path:
            ts = int(time.time() * 1000)
            path = os.path.join(SCREENSHOT_DIR, f"desktop_{ts}.png")
        try:
            img = pyautogui.screenshot(region=region) if region else pyautogui.screenshot()
            img.save(path, "PNG")
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return {"success": True, "path": path, "base64": b64, "error": None}
        except Exception as e:
            return {"success": False, "path": path, "error": str(e)}

    def status(self) -> Dict[str, Any]:
        if not _PYAUTOGUI_AVAILABLE:
            return {"available": False, "failsafe": None, "pause": None}
        return {
            "available": True,
            "failsafe": pyautogui.FAILSAFE,
            "pause": pyautogui.PAUSE,
            "screen_size": dict(zip(["width", "height"], pyautogui.size())) if _PYAUTOGUI_AVAILABLE else None,
        }


# ═══════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════

_browser_singleton: Optional[BrowserAutomation] = None
_browser_lock = threading.Lock()

def get_browser() -> BrowserAutomation:
    """获取 BrowserAutomation 全局单例"""
    global _browser_singleton
    if _browser_singleton is None:
        with _browser_lock:
            if _browser_singleton is None:
                _browser_singleton = BrowserAutomation()
    return _browser_singleton

def reset_browser():
    """重置浏览器单例（供模块注册表 unload 调用，确保状态一致）"""
    global _browser_singleton
    with _browser_lock:
        _browser_singleton = None

_screen_singleton: Optional[ScreenCapture] = None
def get_screen_capture() -> ScreenCapture:
    global _screen_singleton
    if _screen_singleton is None:
        _screen_singleton = ScreenCapture()
    return _screen_singleton

def reset_screen_capture():
    """重置屏幕截图单例（供模块注册表 unload 调用）"""
    global _screen_singleton
    _screen_singleton = None

_scraper_singleton: Optional[WebScraper] = None
def get_web_scraper() -> WebScraper:
    global _scraper_singleton
    if _scraper_singleton is None:
        _scraper_singleton = WebScraper()
    return _scraper_singleton

def reset_web_scraper():
    """重置网页抓取单例（供模块注册表 unload 调用）"""
    global _scraper_singleton
    _scraper_singleton = None

_desktop_singleton: Optional[DesktopControl] = None
def get_desktop_control() -> DesktopControl:
    global _desktop_singleton
    if _desktop_singleton is None:
        _desktop_singleton = DesktopControl()
    return _desktop_singleton

def reset_desktop_control():
    """重置桌面控制单例（供模块注册表 unload 调用）"""
    global _desktop_singleton
    _desktop_singleton = None


def automation_status() -> Dict[str, Any]:
    """获取所有自动化能力的总状态"""
    return {
        "browser": {
            "available": _PLAYWRIGHT_AVAILABLE,
            "started": get_browser().started,
            "browser_type": "chromium",
        },
        "screen_capture": {
            "available": _MSS_AVAILABLE or _PIL_AVAILABLE,
            "backend": "mss" if _MSS_AVAILABLE else ("PIL" if _PIL_AVAILABLE else "none"),
        },
        "web_scraper": {
            "available": _HTTPX_AVAILABLE,
            "parser": "BeautifulSoup+lxml" if _BS4_AVAILABLE else "regex",
        },
        "desktop_control": {
            "available": _PYAUTOGUI_AVAILABLE,
        },
        "screenshot_dir": SCREENSHOT_DIR,
    }


if __name__ == "__main__":
    # 自测
    import json
    logging.basicConfig(level=logging.INFO)
    print("=== 自动化能力状态 ===")
    status = automation_status()
    for k, v in status.items():
        print(f"  {k}: {v}")
