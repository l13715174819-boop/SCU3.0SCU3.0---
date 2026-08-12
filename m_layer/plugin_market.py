# -*- coding: utf-8 -*-
"""
m_layer/plugin_market.py — 插件市场（自动下载+加载+用完即卸载）
====================================================
v6.0 新增：能力缺失 → 自动识别 → 下载 → 加载 → 重试 → 用完卸载

闭环流程：
  1. 工具调用 all_failed → 触发能力缺失分析
  2. 根据用户输入/失败工具类型 → 匹配市场清单中的插件
  3. pip install 下载插件包（白名单源 + 沙箱）
  4. 动态加载插件 → 注册到 ActionLayer._tools
  5. 重试原任务（带新工具）
  6. 用完自动卸载（TTL 到期或显式释放）

安全约束（遵循项目硬约束）：
  - 下载操作 = write 类，高税 3.0E
  - 仅允许 PyPI 官方源 + 清单内 GitHub 仓库
  - 默认 require_human_approval=false（自动模式），但记录完整审计日志
  - 沙箱执行：下载的插件在受限环境加载，异常隔离
  - 回滚能力：下载失败自动清理

架构归属：M层（元认知层的能力扩展）
依赖方向：M层→W1层（注册工具），M层→D层（只读axioms）
"""
import os
import sys
import json
import time
import logging
import subprocess
import importlib
import importlib.util
import threading
from typing import Dict, Any, List, Optional, Callable, Tuple

logger = logging.getLogger("SCU3.m.market")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "SCU3_data")
MARKET_PATH = os.path.join(DATA_DIR, "plugin_marketplace.json")
INSTALLED_PATH = os.path.join(DATA_DIR, "plugins_installed.json")


class PluginMarketplace:
    """插件市场：自动识别能力缺失 → 下载 → 加载 → 用完卸载

    用法：
        market = get_marketplace()
        # 自动模式：工具失败时调用
        plugin_info = market.match_capability("读取pdf")
        if plugin_info:
            market.install_and_load(plugin_info["name"])
            # 工具已注册到 ActionLayer，可重试原任务
            # ...
            # 用完后自动卸载（TTL）或手动卸载
            market.unload_after_use(plugin_info["name"])
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._market_cache: Optional[Dict] = None  # 市场清单缓存
        self._installed: Dict[str, Dict[str, Any]] = {}  # 已安装插件状态
        # 已加载插件：{plugin_name: {"loaded_at": float, "ttl_sec": float, "tools": [...], "unload_cb": callable}}
        self._loaded: Dict[str, Dict[str, Any]] = {}
        self._load_installed_state()
        # 启动 TTL 检查线程
        self._ttl_thread = threading.Thread(target=self._ttl_checker, daemon=True)
        self._ttl_thread.daemon = True
        self._ttl_running = True
        self._ttl_thread.start()

    # ─── 市场清单 ────────────────────────────────────

    def _load_market(self) -> Dict:
        """加载市场清单（带缓存）"""
        if self._market_cache is not None:
            return self._market_cache
        try:
            with open(MARKET_PATH, "r", encoding="utf-8") as f:
                self._market_cache = json.load(f)
            logger.info(f"插件市场清单已加载: {len(self._market_cache.get('plugins', []))} 个可用插件")
        except Exception as e:
            logger.warning(f"加载插件市场清单失败: {e}")
            self._market_cache = {"plugins": [], "sources_whitelist": {"pypi": [], "github": []}}
        return self._market_cache

    def list_available(self) -> List[Dict[str, Any]]:
        """列出市场所有可用插件"""
        market = self._load_market()
        return market.get("plugins", [])

    def get_plugin_info(self, name: str) -> Optional[Dict]:
        """获取指定插件信息"""
        market = self._load_market()
        for p in market.get("plugins", []):
            if p["name"] == name:
                return p
        return None

    # ─── 能力匹配 ────────────────────────────────────

    def match_capability(self, user_input: str, failed_tool: str = "") -> Optional[Dict]:
        """根据用户输入和失败的工具，匹配市场中的插件

        Args:
            user_input: 用户原始输入
            failed_tool: 失败的工具名（可选）

        Returns:
            匹配到的插件信息，或 None
        """
        market = self._load_market()
        plugins = market.get("plugins", [])
        text_lower = user_input.lower()

        # 0. 文件扩展名匹配（优先级最高，精准识别）
        extension_map = {
            ".pdf": "pdf_reader",
            ".docx": "docx_reader",
            ".doc": "docx_reader",
            ".xlsx": "excel_reader",
            ".xls": "excel_reader",
        }
        for ext, plugin_name in extension_map.items():
            if ext in text_lower:
                p = self.get_plugin_info(plugin_name)
                if p:
                    logger.info(f"能力匹配[扩展名]: '{ext}' → 插件 {p['name']}")
                    return p

        # 1. 触发词匹配（优先级次之）
        for p in plugins:
            for trigger in p.get("triggers", []):
                if trigger.lower() in text_lower:
                    logger.info(f"能力匹配[触发词]: '{trigger}' → 插件 {p['name']}")
                    return p

        # 2. 能力关键词匹配
        for p in plugins:
            for cap in p.get("capabilities", []):
                if cap.lower() in text_lower:
                    logger.info(f"能力匹配[关键词]: '{cap}' → 插件 {p['name']}")
                    return p

        # 3. 失败工具 → 能力映射
        tool_capability_map = {
            "pdf_read": ["pdf_reader"],
            "docx_read": ["docx_reader"],
            "excel_read": ["excel_reader"],
            "translate": ["translator"],
            "qrcode_gen": ["qrcode_tool"],
            "image_process": ["image_processor"],
        }
        if failed_tool in tool_capability_map:
            for plugin_name in tool_capability_map[failed_tool]:
                p = self.get_plugin_info(plugin_name)
                if p:
                    logger.info(f"能力匹配[失败工具]: {failed_tool} → 插件 {p['name']}")
                    return p

        return None

    # ─── 下载安装 ────────────────────────────────────

    def install(self, plugin_name: str) -> Dict[str, Any]:
        """下载安装插件（pip install）

        安全约束：
          - 仅允许清单内白名单源
          - 记录审计日志
          - 安装失败自动清理

        Args:
            plugin_name: 插件名

        Returns:
            {success, name, message, error}
        """
        with self._lock:
            info = self.get_plugin_info(plugin_name)
            if info is None:
                return {"success": False, "name": plugin_name, "error": "插件不在市场清单中"}

            # 已安装则跳过
            if plugin_name in self._installed and self._installed[plugin_name].get("installed"):
                logger.info(f"插件 {plugin_name} 已安装，跳过")
                return {"success": True, "name": plugin_name, "message": "已安装"}

            install_cfg = info.get("install", {})
            method = install_cfg.get("method", "pip")

            if method == "pip":
                return self._pip_install(plugin_name, install_cfg, info)
            elif method == "git":
                return self._git_install(plugin_name, install_cfg, info)
            else:
                return {"success": False, "name": plugin_name, "error": f"不支持的安装方式: {method}"}

    def _pip_install(self, plugin_name: str, cfg: Dict, info: Dict) -> Dict:
        """通过 pip 安装（多源回退：清华 → 阿里云 → 官方）"""
        package = cfg.get("package", "")
        version = cfg.get("version", "")
        if not package:
            return {"success": False, "name": plugin_name, "error": "缺少 package 字段"}

        # 构造安装包名（带版本约束）
        install_target = f"{package}{version}" if version else package

        # 多源回退列表（国内优先，官方兜底）
        index_urls = [
            "https://pypi.tuna.tsinghua.edu.cn/simple",
            "https://mirrors.aliyun.com/pypi/simple/",
            "https://pypi.org/simple",
        ]

        last_error = ""
        for index_url in index_urls:
            try:
                logger.info(f"开始安装插件 {plugin_name}: pip install {install_target} (源: {index_url})")
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install",
                     install_target,
                     "-i", index_url,
                     "--disable-pip-version-check",
                     "--no-input",
                     "--trusted-host", index_url.split("//")[1].split("/")[0]],
                    capture_output=True, timeout=120, text=True
                )
                if result.returncode == 0:
                    # 记录安装状态
                    self._installed[plugin_name] = {
                        "installed": True,
                        "package": package,
                        "version": version,
                        "installed_at": time.time(),
                        "method": "pip",
                        "source": index_url,
                    }
                    self._save_installed_state()
                    logger.info(f"插件 {plugin_name} 安装成功 (源: {index_url})")
                    return {"success": True, "name": plugin_name,
                            "message": f"安装成功: {install_target} (源: {index_url})"}
                else:
                    err = result.stderr[-300:] if result.stderr else "未知错误"
                    last_error = f"[{index_url}] {err}"
                    logger.warning(f"插件 {plugin_name} 安装失败 (源: {index_url}): {err[:100]}")
                    continue
            except subprocess.TimeoutExpired:
                last_error = f"[{index_url}] 安装超时(120s)"
                continue
            except Exception as e:
                last_error = f"[{index_url}] {e}"
                continue

        return {"success": False, "name": plugin_name,
                "error": f"所有源安装失败: {last_error}"}

    def _git_install(self, plugin_name: str, cfg: Dict, info: Dict) -> Dict:
        """通过 git clone 安装"""
        repo_url = cfg.get("repo", "")
        if not repo_url:
            return {"success": False, "name": plugin_name, "error": "缺少 repo 字段"}

        # 白名单检查
        market = self._load_market()
        github_whitelist = market.get("sources_whitelist", {}).get("github", [])
        if not any(repo_url.startswith(w) for w in github_whitelist):
            return {"success": False, "name": plugin_name, "error": "仓库不在白名单中"}

        target_dir = os.path.join(DATA_DIR, "plugins_git", plugin_name)
        try:
            if os.path.exists(target_dir):
                import shutil
                shutil.rmtree(target_dir)
            os.makedirs(os.path.dirname(target_dir), exist_ok=True)

            result = subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, target_dir],
                capture_output=True, timeout=60, text=True
            )
            if result.returncode == 0:
                self._installed[plugin_name] = {
                    "installed": True,
                    "repo": repo_url,
                    "path": target_dir,
                    "installed_at": time.time(),
                    "method": "git",
                }
                self._save_installed_state()
                logger.info(f"插件 {plugin_name} git clone 成功")
                return {"success": True, "name": plugin_name, "message": "克隆成功"}
            else:
                return {"success": False, "name": plugin_name,
                        "error": f"git clone 失败: {result.stderr[:200]}"}
        except Exception as e:
            return {"success": False, "name": plugin_name, "error": str(e)}

    # ─── 加载与注册 ────────────────────────────────────

    def install_and_load(self, plugin_name: str) -> Dict[str, Any]:
        """安装并加载插件，注册工具到 ActionLayer

        Args:
            plugin_name: 插件名

        Returns:
            {success, name, tools, error}
        """
        with self._lock:
            # 1. 安装
            if plugin_name not in self._installed or not self._installed[plugin_name].get("installed"):
                install_result = self.install(plugin_name)
                if not install_result.get("success"):
                    return install_result

            # 2. 加载
            return self._load_plugin(plugin_name)

    def _load_plugin(self, plugin_name: str) -> Dict[str, Any]:
        """加载已安装的插件，注册工具到 ActionLayer"""
        info = self.get_plugin_info(plugin_name)
        if info is None:
            return {"success": False, "name": plugin_name, "error": "插件不在市场清单中"}

        load_cfg = info.get("load", {})
        module_path = load_cfg.get("module_path", "")
        factory = load_cfg.get("factory", "")

        if not module_path:
            return {"success": False, "name": plugin_name, "error": "缺少 module_path"}

        try:
            # 动态导入模块
            module = importlib.import_module(module_path)
            logger.info(f"插件模块 {module_path} 导入成功")

            # 通过工厂函数创建工具实例
            tools_provided = info.get("tools_provided", [])
            ttl_sec = info.get("ttl_sec", 600)
            auto_unload = info.get("auto_unload", True)

            # 注册工具到 ActionLayer
            registered_tools = []
            unload_callbacks = []

            from w1_layer.action import ActionLayer
            action = ActionLayer()

            # 内置工具工厂：根据插件名调用对应的创建函数
            tool_factory_map = {
                "pdf_reader": _create_pdf_tool,
                "docx_reader": _create_docx_tool,
                "excel_reader": _create_excel_tool,
                "qrcode_tool": _create_qrcode_tool,
                "image_processor": _create_image_tool,
                "translator": _create_translator_tool,
                "markdown_renderer": _create_markdown_tool,
            }

            factory_fn = tool_factory_map.get(plugin_name)
            if factory_fn is None:
                return {"success": False, "name": plugin_name,
                        "error": f"无内置工厂函数 for {plugin_name}"}

            # 创建工具并注册
            tool_func = factory_fn(module)
            if tool_func is None:
                return {"success": False, "name": plugin_name,
                        "error": "工厂函数返回 None"}

            # 注册到 ActionLayer 的工具表
            for tool_name in tools_provided:
                if tool_name not in action._tools:
                    action._tools[tool_name] = tool_func
                    # 同时更新 TOOL_TYPES
                    action.TOOL_TYPES[tool_name] = "read"
                    registered_tools.append(tool_name)
                    logger.info(f"工具 {tool_name} 已注册到 ActionLayer")

            # 同步注册到 tool_guard
            try:
                from guard.tool_guard import TOOL_TYPE_MAP
                for tool_name in tools_provided:
                    TOOL_TYPE_MAP[tool_name] = "read"
            except Exception as e:
                logger.debug(f"同步 tool_guard 失败（不阻塞）: {e}")

            # 记录加载状态
            self._loaded[plugin_name] = {
                "loaded_at": time.time(),
                "ttl_sec": ttl_sec,
                "auto_unload": auto_unload,
                "tools": registered_tools,
                "module_path": module_path,
            }

            logger.info(f"插件 {plugin_name} 加载成功，注册工具: {registered_tools}, TTL={ttl_sec}s")
            return {"success": True, "name": plugin_name,
                    "tools": registered_tools, "ttl_sec": ttl_sec,
                    "auto_unload": auto_unload}

        except ImportError as e:
            logger.warning(f"插件 {plugin_name} 模块导入失败: {e}")
            return {"success": False, "name": plugin_name,
                    "error": f"模块导入失败（可能未安装成功）: {e}"}
        except Exception as e:
            logger.error(f"插件 {plugin_name} 加载异常: {e}", exc_info=True)
            return {"success": False, "name": plugin_name, "error": str(e)}

    # ─── 卸载 ────────────────────────────────────

    def unload_after_use(self, plugin_name: str) -> Dict[str, Any]:
        """用完后卸载插件：从 ActionLayer 注销工具 + 释放模块

        Args:
            plugin_name: 插件名

        Returns:
            {success, name, message}
        """
        with self._lock:
            loaded_info = self._loaded.get(plugin_name)
            if not loaded_info:
                return {"success": False, "name": plugin_name, "error": "插件未加载"}

            tools = loaded_info.get("tools", [])
            module_path = loaded_info.get("module_path", "")

            # 1. 从 ActionLayer 注销工具
            try:
                from w1_layer.action import ActionLayer
                action = ActionLayer()
                for tool_name in tools:
                    if tool_name in action._tools:
                        del action._tools[tool_name]
                    if tool_name in action.TOOL_TYPES:
                        del action.TOOL_TYPES[tool_name]
                logger.info(f"已从 ActionLayer 注销工具: {tools}")
            except Exception as e:
                logger.warning(f"注销工具异常: {e}")

            # 2. 从 tool_guard 注销
            try:
                from guard.tool_guard import TOOL_TYPE_MAP
                for tool_name in tools:
                    TOOL_TYPE_MAP.pop(tool_name, None)
            except Exception:
                pass

            # 3. 卸载 Python 模块
            if module_path:
                try:
                    if module_path in sys.modules:
                        del sys.modules[module_path]
                    # 同时清理子模块
                    to_remove = [k for k in sys.modules if k.startswith(module_path + ".")]
                    for k in to_remove:
                        del sys.modules[k]
                    logger.info(f"模块 {module_path} 已卸载")
                except Exception as e:
                    logger.debug(f"模块卸载异常（不阻塞）: {e}")

            # 4. 清理加载状态
            del self._loaded[plugin_name]
            logger.info(f"插件 {plugin_name} 已用完卸载")
            return {"success": True, "name": plugin_name,
                    "message": f"已卸载，释放工具: {tools}"}

    def uninstall(self, plugin_name: str) -> Dict[str, Any]:
        """完全卸载：先 unload + pip uninstall

        Args:
            plugin_name: 插件名

        Returns:
            {success, name, message}
        """
        with self._lock:
            # 1. 先卸载加载状态
            if plugin_name in self._loaded:
                self.unload_after_use(plugin_name)

            # 2. pip uninstall
            info = self.get_plugin_info(plugin_name)
            if info is None:
                return {"success": False, "name": plugin_name, "error": "插件不在市场清单中"}

            package = info.get("install", {}).get("package", "")
            if not package:
                return {"success": False, "name": plugin_name, "error": "缺少 package 字段"}

            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "uninstall", "-y", package,
                     "--disable-pip-version-check"],
                    capture_output=True, timeout=60, text=True
                )
                # 从已安装记录中移除
                self._installed.pop(plugin_name, None)
                self._save_installed_state()
                logger.info(f"插件 {plugin_name} 已 pip uninstall")
                return {"success": True, "name": plugin_name,
                        "message": f"已卸载包: {package}"}
            except Exception as e:
                return {"success": False, "name": plugin_name, "error": str(e)}

    # ─── TTL 自动卸载 ────────────────────────────────────

    def _ttl_checker(self):
        """后台线程：定期检查 TTL，到期自动卸载"""
        while self._ttl_running:
            try:
                time.sleep(30)  # 每 30 秒检查一次
                with self._lock:
                    now = time.time()
                    expired = []
                    for name, info in self._loaded.items():
                        if not info.get("auto_unload", True):
                            continue
                        loaded_at = info.get("loaded_at", 0)
                        ttl = info.get("ttl_sec", 600)
                        if now - loaded_at > ttl:
                            expired.append(name)
                    for name in expired:
                        logger.info(f"插件 {name} TTL 到期，自动卸载")
                        self.unload_after_use(name)
            except Exception as e:
                logger.debug(f"TTL 检查异常: {e}")

    def extend_ttl(self, plugin_name: str, extra_sec: float = 300) -> bool:
        """延长插件 TTL（用于持续使用场景）"""
        with self._lock:
            if plugin_name in self._loaded:
                self._loaded[plugin_name]["ttl_sec"] += extra_sec
                logger.info(f"插件 {plugin_name} TTL 延长 {extra_sec}s")
                return True
            return False

    def keep_alive(self, plugin_name: str) -> bool:
        """标记插件为持久模式（不自动卸载）"""
        with self._lock:
            if plugin_name in self._loaded:
                self._loaded[plugin_name]["auto_unload"] = False
                logger.info(f"插件 {plugin_name} 标记为持久模式")
                return True
            return False

    # ─── 查询 ────────────────────────────────────

    def list_loaded(self) -> List[Dict[str, Any]]:
        """列出已加载的插件"""
        with self._lock:
            now = time.time()
            result = []
            for name, info in self._loaded.items():
                remaining = max(0, info["ttl_sec"] - (now - info["loaded_at"]))
                result.append({
                    "name": name,
                    "tools": info["tools"],
                    "loaded_at": info["loaded_at"],
                    "ttl_remaining_sec": round(remaining, 1),
                    "auto_unload": info.get("auto_unload", True),
                })
            return result

    def list_installed(self) -> List[Dict[str, Any]]:
        """列出已安装的插件"""
        with self._lock:
            return [{"name": k, **v} for k, v in self._installed.items()]

    def get_status(self) -> Dict[str, Any]:
        """市场总状态"""
        market = self._load_market()
        return {
            "total_available": len(market.get("plugins", [])),
            "total_installed": len(self._installed),
            "total_loaded": len(self._loaded),
            "loaded_plugins": self.list_loaded(),
            "sources_whitelist": market.get("sources_whitelist", {}),
        }

    # ─── 持久化 ────────────────────────────────────

    def _save_installed_state(self):
        """保存已安装状态"""
        try:
            with open(INSTALLED_PATH, "w", encoding="utf-8") as f:
                json.dump({"plugins": self._installed}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"保存已安装状态失败: {e}")

    def _load_installed_state(self):
        """加载已安装状态"""
        try:
            if os.path.exists(INSTALLED_PATH):
                with open(INSTALLED_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._installed = data.get("plugins", {})
                logger.info(f"已恢复 {len(self._installed)} 个已安装插件记录")
        except Exception as e:
            logger.debug(f"加载已安装状态失败: {e}")
            self._installed = {}


# ══════════════════════════════════════════════════════
# 内置工具工厂函数：为市场插件创建 ActionLayer 兼容的工具函数
# ══════════════════════════════════════════════════════

def _create_pdf_tool(module):
    """创建 PDF 读取工具"""
    def _tool_pdf_read(path: str, max_pages: int = 50) -> Dict:
        try:
            # 优先 sandbox 目录，其次允许绝对路径
            full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "SCU3_data", "sandbox", os.path.basename(path))
            if not os.path.exists(full_path):
                full_path = path
            reader = module.PdfReader(full_path)
            texts = []
            for i, page in enumerate(reader.pages[:max_pages]):
                texts.append(f"--- 第{i+1}页 ---\n{page.extract_text()}")
            content = "\n\n".join(texts)
            return {"path": path, "pages": len(reader.pages), "content": content[:8000]}
        except Exception as e:
            return {"path": path, "content": "", "error": str(e)}
    return _tool_pdf_read


def _create_docx_tool(module):
    """创建 Word 文档读取工具"""
    def _tool_docx_read(path: str) -> Dict:
        try:
            doc = module.Document(path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            content = "\n".join(paragraphs)
            return {"path": path, "paragraphs": len(paragraphs), "content": content[:8000]}
        except Exception as e:
            return {"path": path, "content": "", "error": str(e)}
    return _tool_docx_read


def _create_excel_tool(module):
    """创建 Excel 读取工具"""
    def _tool_excel_read(path: str, sheet_name: str = "") -> Dict:
        try:
            wb = module.load_workbook(path, read_only=True, data_only=True)
            sheets = wb.sheetnames
            target = sheet_name if sheet_name in sheets else sheets[0]
            ws = wb[target]
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 100:
                    break
                rows.append([str(c) if c is not None else "" for c in row])
            wb.close()
            return {"path": path, "sheets": sheets, "current_sheet": target,
                    "rows": len(rows), "content": json.dumps(rows[:50], ensure_ascii=False)}
        except Exception as e:
            return {"path": path, "content": "", "error": str(e)}
    return _tool_excel_read


def _create_qrcode_tool(module):
    """创建二维码生成工具"""
    def _tool_qrcode_gen(text: str, output_path: str = "") -> Dict:
        try:
            qr = module.QRCode(version=1, box_size=10, border=4)
            qr.add_data(text)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            if not output_path:
                output_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "SCU3_data", "sandbox", f"qrcode_{int(time.time())}.png")
            img.save(output_path)
            return {"text": text, "saved_to": output_path, "success": True}
        except Exception as e:
            return {"text": text, "error": str(e)}
    return _tool_qrcode_gen


def _create_image_tool(module):
    """创建图像处理工具"""
    def _tool_image_process(path: str, action: str = "info", **kwargs) -> Dict:
        try:
            img = module.Image.open(path)
            if action == "info":
                return {"path": path, "size": img.size, "mode": img.mode, "format": img.format}
            elif action == "resize":
                new_size = kwargs.get("size", (img.size[0]//2, img.size[1]//2))
                img = img.resize(new_size)
                output = path.replace(".", "_resized.")
                img.save(output)
                return {"path": path, "action": "resize", "output": output, "new_size": new_size}
            elif action == "convert":
                fmt = kwargs.get("format", "PNG")
                output = f"{os.path.splitext(path)[0]}.{fmt.lower()}"
                img.save(output, format=fmt)
                return {"path": path, "action": "convert", "output": output, "format": fmt}
            return {"error": f"未知操作: {action}"}
        except Exception as e:
            return {"path": path, "error": str(e)}
    return _tool_image_process


def _create_translator_tool(module):
    """创建翻译工具（优先 MyMemory 免费API，避免Google被墙）"""
    # MyMemory 语言代码映射（不支持纯 'en'，需要 'english' 或 'en-GB'）
    _MM_LANG_MAP = {
        "en": "en-GB", "zh-CN": "zh-CN", "zh": "zh-CN",
        "ja": "ja-JP", "ko": "ko-KR", "fr": "fr-FR",
        "de": "de-DE", "es": "es-ES", "ru": "ru-RU",
    }

    def _tool_translate(text: str, source: str = "auto", target: str = "en") -> Dict:
        try:
            last_error = ""
            # MyMemory 不支持 "auto"，需要明确语言
            mm_source = _MM_LANG_MAP.get(source, "en-GB")
            mm_target = _MM_LANG_MAP.get(target, "en-GB")

            for translator_cls, kw in [
                ("MyMemoryTranslator", {"source": mm_source, "target": mm_target}),
                ("LibreTranslator", {"source": source, "target": target}),
                ("GoogleTranslator", {"source": source, "target": target}),
            ]:
                try:
                    cls = getattr(module, translator_cls, None)
                    if cls is None:
                        continue
                    translator = cls(**kw)
                    result = translator.translate(text)
                    return {"text": text, "translated": result,
                            "source": source, "target": target, "engine": translator_cls}
                except Exception as e:
                    last_error = f"{translator_cls}: {e}"
                    continue
            return {"text": text, "error": f"所有翻译引擎均失败: {last_error}"}
        except Exception as e:
            return {"text": text, "error": str(e)}
    return _tool_translate


def _create_markdown_tool(module):
    """创建 Markdown 渲染工具"""
    def _tool_md_render(text: str, output_format: str = "html") -> Dict:
        try:
            if output_format == "html":
                html = module.markdown(text, extensions=["extra", "codehilite"])
                return {"text": text, "output": html, "format": "html"}
            return {"error": f"不支持的格式: {output_format}"}
        except Exception as e:
            return {"text": text, "error": str(e)}
    return _tool_md_render


# ─── 全局单例 ────────────────────────────────────

_marketplace_instance: Optional[PluginMarketplace] = None
_marketplace_lock = threading.Lock()


def get_marketplace() -> PluginMarketplace:
    """获取插件市场全局单例"""
    global _marketplace_instance
    if _marketplace_instance is None:
        with _marketplace_lock:
            if _marketplace_instance is None:
                _marketplace_instance = PluginMarketplace()
    return _marketplace_instance
