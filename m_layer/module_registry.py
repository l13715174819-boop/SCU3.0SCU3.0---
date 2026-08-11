# -*- coding: utf-8 -*-
"""
m_layer/module_registry.py — 功能模块注册表（M层）
====================================================
v5.2 新增：运行时动态卸载/重载功能模块。

能力边界：
  - 注册：启动时扫描并注册核心功能模块（自动化/语音/视觉/知识库等）
  - 卸载：调用模块的 unload() 方法释放资源（关闭浏览器、停止监听等），
          并将模块标记为 disabled，相关端点将返回 503
  - 重载：重新调用模块的初始化逻辑，恢复可用
  - 不删文件、不改代码：仅控制运行时状态

安全约束：
  - 核心安全模块（CUF守卫/防火墙/熵账本）不可卸载（在 PROTECTED 列表）
  - 卸载前调用模块的 unload()，失败时记录但不阻止 disable 标记
  - 所有操作记录到 module_registry.json，便于审计

架构归属：M层（元认知层管理模块生命周期）
依赖方向：M层→W1层（调用模块），M层→D层（只读axioms）
"""
import os
import json
import time
import logging
import threading
from typing import Dict, Any, List, Optional, Callable

logger = logging.getLogger("scu2.m.module_registry")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "scu2_data")
STATE_PATH = os.path.join(DATA_DIR, "module_registry.json")
os.makedirs(DATA_DIR, exist_ok=True)

# 受保护模块（不可卸载，对应 D 层核心 + 安全守卫）
PROTECTED_MODULES = {
    "cuf.firewall",       # CUF 逻辑防火墙
    "cuf.entropy_ledger", # 熵税账本
    "cuf.axioms",         # 公理层
    "engine",             # 引擎核心
    "meta_guard",         # 元认知守卫
    "baseline",           # 基线
    "code_self_modify",   # 自修改模块（卸载会破坏回滚能力）
    "module_registry",    # 自身（卸载会失去管理能力）
}


class ModuleInfo:
    """单个功能模块的注册信息"""

    def __init__(
        self,
        name: str,
        description: str,
        loader: Callable[[], Any],
        unloader: Optional[Callable[[], Any]] = None,
        category: str = "general",
    ):
        """初始化模块信息

        Args:
            name: 模块唯一名（如 "automation.browser"）
            description: 模块描述
            loader: 加载函数，返回模块实例
            unloader: 卸载函数，接收模块实例，释放资源
            category: 分类（automation/voice/vision/knowledge/security/core）
        """
        self.name = name
        self.description = description
        self.loader = loader
        self.unloader = unloader
        self.category = category
        self.instance: Any = None
        self.loaded = False
        self.disabled = False  # 主动禁用（运行时不加载）
        self.load_count = 0
        self.unload_count = 0
        self.last_loaded_at: float = 0.0
        self.last_unloaded_at: float = 0.0
        self.last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "loaded": self.loaded,
            "disabled": self.disabled,
            "protected": self.name in PROTECTED_MODULES,
            "load_count": self.load_count,
            "unload_count": self.unload_count,
            "last_loaded_at": self.last_loaded_at,
            "last_unloaded_at": self.last_unloaded_at,
            "last_error": self.last_error,
        }


class ModuleRegistry:
    """功能模块注册表

    用法：
        registry = get_registry()
        # 注册模块（启动时）
        registry.register("automation.browser", "浏览器自动化",
                          loader=lambda: get_browser(),
                          unloader=lambda m: m.stop(),
                          category="automation")
        # 加载
        registry.load("automation.browser")
        # 卸载
        registry.unload("automation.browser")
        # 列表
        registry.list_modules()
    """

    def __init__(self):
        self._modules: Dict[str, ModuleInfo] = {}
        self._lock = threading.RLock()
        self._load_state()

    def register(
        self,
        name: str,
        description: str,
        loader: Callable[[], Any],
        unloader: Optional[Callable[[], Any]] = None,
        category: str = "general",
        auto_load: bool = False,
    ) -> Dict[str, Any]:
        """注册一个模块

        Args:
            name: 模块唯一名
            description: 描述
            loader: 加载函数
            unloader: 卸载函数
            category: 分类
            auto_load: 是否立即加载

        Returns:
            {success, name, loaded, error}
        """
        with self._lock:
            if name in self._modules:
                # 已注册，仅更新 loader/unloader
                m = self._modules[name]
                m.loader = loader
                if unloader:
                    m.unloader = unloader
                logger.debug(f"模块 {name} 已注册，更新 loader")
            else:
                m = ModuleInfo(name, description, loader, unloader, category)
                self._modules[name] = m
                logger.info(f"模块已注册: {name} ({category})")

            # 从持久化状态恢复 disabled 标记
            # （不恢复 loaded 状态，需显式 load）
            pending = getattr(self, "_pending_state", {})
            if name in pending:
                m.disabled = bool(pending[name].get("disabled", False))
                if m.disabled:
                    logger.info(f"模块 {name} 从持久化恢复为 disabled 状态")

            if auto_load and not m.disabled:
                return self.load(name)
            return {"success": True, "name": name, "loaded": m.loaded, "error": None}

    def load(self, name: str) -> Dict[str, Any]:
        """加载模块（调用 loader 获取实例）

        Args:
            name: 模块名

        Returns:
            {success, name, loaded, error}
        """
        with self._lock:
            m = self._modules.get(name)
            if m is None:
                return {"success": False, "error": f"未注册的模块: {name}"}
            if m.disabled:
                return {"success": False, "error": f"模块已禁用，请先 enable: {name}"}
            if m.loaded:
                return {"success": True, "name": name, "loaded": True, "message": "模块已加载"}

            try:
                start = time.time()
                m.instance = m.loader()
                m.loaded = True
                m.load_count += 1
                m.last_loaded_at = time.time()
                m.last_error = None
                latency = time.time() - start
                logger.info(f"模块加载成功: {name} (耗时 {latency:.2f}s)")
                self._save_state()
                return {"success": True, "name": name, "loaded": True, "latency": round(latency, 3), "error": None}
            except Exception as e:
                m.last_error = str(e)
                logger.error(f"模块加载失败 {name}: {e}")
                return {"success": False, "name": name, "loaded": False, "error": str(e)}

    def unload(self, name: str, force: bool = False) -> Dict[str, Any]:
        """卸载模块（调用 unloader 释放资源）

        Args:
            name: 模块名
            force: 强制卸载（即使受保护也卸载，慎用）

        Returns:
            {success, name, loaded, error}
        """
        with self._lock:
            m = self._modules.get(name)
            if m is None:
                return {"success": False, "error": f"未注册的模块: {name}"}
            if not m.loaded and m.instance is None:
                return {"success": True, "name": name, "loaded": False, "message": "模块未加载"}

            # 受保护模块检查
            if name in PROTECTED_MODULES and not force:
                return {
                    "success": False,
                    "error": f"受保护模块不可卸载: {name}（如需强制卸载请设 force=true，风险自负）",
                }

            # 调用 unloader
            unloader_error = None
            if m.unloader and m.instance is not None:
                try:
                    result = m.unloader(m.instance)
                    # unloader 可返回 dict 表示卸载详情
                    if isinstance(result, dict) and result.get("error"):
                        unloader_error = result.get("error")
                        logger.warning(f"模块 {name} unloader 返回错误: {unloader_error}")
                except Exception as e:
                    unloader_error = str(e)
                    logger.error(f"模块 {name} unloader 异常: {e}")

            # 无论 unloader 是否成功，都标记为已卸载
            m.instance = None
            m.loaded = False
            m.unload_count += 1
            m.last_unloaded_at = time.time()
            if unloader_error:
                m.last_error = f"unload: {unloader_error}"

            logger.info(f"模块已卸载: {name}")
            self._save_state()
            return {
                "success": True,
                "name": name,
                "loaded": False,
                "unloader_error": unloader_error,
                "error": None,
            }

    def reload(self, name: str) -> Dict[str, Any]:
        """重载模块（unload + load）"""
        with self._lock:
            unload_result = self.unload(name)
            # 即使卸载有警告，仍尝试重新加载
            load_result = self.load(name)
            return {
                "success": load_result.get("success", False),
                "name": name,
                "loaded": load_result.get("loaded", False),
                "unload": unload_result,
                "load": load_result,
                "error": load_result.get("error"),
            }

    def disable(self, name: str) -> Dict[str, Any]:
        """禁用模块（卸载 + 标记 disabled，之后无法 load 直到 enable）"""
        with self._lock:
            m = self._modules.get(name)
            if m is None:
                return {"success": False, "error": f"未注册的模块: {name}"}
            if name in PROTECTED_MODULES:
                return {"success": False, "error": f"受保护模块不可禁用: {name}"}
            # 先卸载
            if m.loaded:
                self.unload(name)
            m.disabled = True
            self._save_state()
            logger.info(f"模块已禁用: {name}")
            return {"success": True, "name": name, "disabled": True}

    def enable(self, name: str) -> Dict[str, Any]:
        """启用模块（清除 disabled 标记，但不自动加载）"""
        with self._lock:
            m = self._modules.get(name)
            if m is None:
                return {"success": False, "error": f"未注册的模块: {name}"}
            m.disabled = False
            self._save_state()
            logger.info(f"模块已启用: {name}")
            return {"success": True, "name": name, "disabled": False}

    def get(self, name: str) -> Any:
        """获取模块实例（未加载返回 None）"""
        with self._lock:
            m = self._modules.get(name)
            return m.instance if m and m.loaded else None

    def is_available(self, name: str) -> bool:
        """模块是否可用（已加载且未禁用）"""
        with self._lock:
            m = self._modules.get(name)
            return bool(m and m.loaded and not m.disabled)

    def list_modules(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出所有模块"""
        with self._lock:
            result = []
            for m in self._modules.values():
                if category and m.category != category:
                    continue
                result.append(m.to_dict())
            return result

    def status(self) -> Dict[str, Any]:
        """注册表总状态"""
        with self._lock:
            total = len(self._modules)
            loaded = sum(1 for m in self._modules.values() if m.loaded)
            disabled = sum(1 for m in self._modules.values() if m.disabled)
            protected = sum(1 for m in self._modules.values() if m.name in PROTECTED_MODULES)
            categories = {}
            for m in self._modules.values():
                categories[m.category] = categories.get(m.category, 0) + 1
            return {
                "total": total,
                "loaded": loaded,
                "disabled": disabled,
                "protected": protected,
                "categories": categories,
            }

    # ─── 持久化 ────────────────────────────────────

    def _save_state(self):
        """持久化 disabled 状态（loaded 状态不持久化，重启需重新 load）"""
        try:
            state = {
                "modules": {
                    name: {"disabled": m.disabled}
                    for name, m in self._modules.items()
                },
                "updated_at": time.time(),
            }
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"保存注册表状态失败: {e}")

    def _load_state(self):
        """从持久化恢复 disabled 状态"""
        try:
            if not os.path.exists(STATE_PATH):
                return
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            # 仅记录，等模块 register 后再应用
            self._pending_state = state.get("modules", {})
        except Exception as e:
            logger.debug(f"加载注册表状态失败: {e}")
            self._pending_state = {}


# ─── 全局单例 ────────────────────────────────────

_registry_instance: Optional[ModuleRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ModuleRegistry:
    """获取 ModuleRegistry 全局单例"""
    global _registry_instance
    if _registry_instance is None:
        with _registry_lock:
            if _registry_instance is None:
                _registry_instance = ModuleRegistry()
    return _registry_instance


def register_builtin_modules():
    """注册 SCU2 内置功能模块（启动时调用一次）"""
    registry = get_registry()

    # 浏览器自动化
    def _load_browser():
        from w1_layer.automation import get_browser
        ba = get_browser()
        return ba

    def _unload_browser(ba):
        result = ba.stop()
        # 重置单例，确保下次 load 获取全新实例
        from w1_layer.automation import reset_browser
        reset_browser()
        return result

    registry.register(
        "automation.browser",
        "浏览器自动化（Playwright）",
        loader=_load_browser,
        unloader=_unload_browser,
        category="automation",
    )

    # 屏幕截图
    def _load_screen():
        from w1_layer.automation import get_screen_capture
        return get_screen_capture()

    def _unload_screen(instance):
        from w1_layer.automation import reset_screen_capture
        reset_screen_capture()

    registry.register(
        "automation.screen",
        "屏幕截图（mss）",
        loader=_load_screen,
        unloader=_unload_screen,
        category="automation",
    )

    # 网页抓取
    def _load_scraper():
        from w1_layer.automation import get_web_scraper
        return get_web_scraper()

    def _unload_scraper(instance):
        from w1_layer.automation import reset_web_scraper
        reset_web_scraper()

    registry.register(
        "automation.web_scraper",
        "网页正文抓取（httpx+BS4）",
        loader=_load_scraper,
        unloader=_unload_scraper,
        category="automation",
    )

    # 桌面控制
    def _load_desktop():
        from w1_layer.automation import get_desktop_control
        return get_desktop_control()

    def _unload_desktop(instance):
        from w1_layer.automation import reset_desktop_control
        reset_desktop_control()

    registry.register(
        "automation.desktop",
        "桌面 GUI 控制（pyautogui）",
        loader=_load_desktop,
        unloader=_unload_desktop,
        category="automation",
    )

    # 语音 IO
    def _load_voice():
        from m_layer.voice_io import get_voice_io
        return get_voice_io()

    registry.register(
        "voice.io",
        "语音输入输出（STT+TTS）",
        loader=_load_voice,
        unloader=None,
        category="voice",
    )

    # 持续语音监听
    def _load_listener():
        from m_layer.voice_io import get_listener
        return get_listener()

    def _unload_listener(listener):
        return listener.stop()

    registry.register(
        "voice.listener",
        "实时持续语音监听（VAD+唤醒词）",
        loader=_load_listener,
        unloader=_unload_listener,
        category="voice",
    )

    # 本地模型
    def _load_local_model():
        from m_layer.local_model import get_local_model
        return get_local_model()

    def _unload_local_model(client):
        return client.unload_model()

    registry.register(
        "llm.local_model",
        "本地大模型（Qwen2.5-7B/VL）",
        loader=_load_local_model,
        unloader=_unload_local_model,
        category="llm",
    )

    # 知识库
    def _load_kb():
        from w1_layer.knowledge_store import get_store
        return get_store()

    def _unload_kb(instance):
        """卸载知识库：清空缓存并释放资源"""
        try:
            if instance is not None:
                instance.clear()  # KnowledgeStore.clear() 清空 FAISS 索引和缓存
        except Exception as e:
            logger.warning(f"卸载知识库失败: {e}")

    registry.register(
        "knowledge.base",
        "知识库（FAISS+SBERT）",
        loader=_load_kb,
        unloader=_unload_kb,
        category="knowledge",
    )

    # 代码自修改引擎（受保护，不可卸载）
    def _load_code_self_modify():
        from m_layer.code_self_modify import get_modifier
        return get_modifier()

    registry.register(
        "code_self_modify",
        "代码自修改引擎（受保护）",
        loader=_load_code_self_modify,
        unloader=None,  # 受保护模块不允许卸载
        category="security",
    )

    logger.info(f"已注册 {len(registry.list_modules())} 个内置模块")

    # .env 配置驱动：读取 SCU2_DISABLED_MODULES 环境变量，禁用指定模块
    # 格式：SCU2_DISABLED_MODULES="automation.browser,voice.listener,llm.local_model"
    # 也支持 SCU2_ENABLED_ONLY（白名单模式，仅启用指定模块）
    disabled_env = os.environ.get("SCU2_DISABLED_MODULES", "").strip()
    enabled_only_env = os.environ.get("SCU2_ENABLED_ONLY", "").strip()

    if disabled_env:
        disabled_list = [m.strip() for m in disabled_env.split(",") if m.strip()]
        for mod_name in disabled_list:
            if mod_name in registry._modules and mod_name not in PROTECTED_MODULES:
                registry._modules[mod_name].disabled = True
                logger.info(f"根据 SCU2_DISABLED_MODULES 禁用模块: {mod_name}")

    if enabled_only_env:
        enabled_list = set(m.strip() for m in enabled_only_env.split(",") if m.strip())
        for mod_name, mod_info in registry._modules.items():
            if mod_name not in enabled_list and mod_name not in PROTECTED_MODULES:
                mod_info.disabled = True
                logger.info(f"根据 SCU2_ENABLED_ONLY 白名单禁用模块: {mod_name}")

    return registry
