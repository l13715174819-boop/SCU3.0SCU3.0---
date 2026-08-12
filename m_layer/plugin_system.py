# -*- coding: utf-8 -*-
"""
m_layer/plugin_system.py — 可扩展插件系统（M层）
====================================================
v5.0第四批：提供Agent能力的插件化扩展机制

能力对标：AI助手的"插件/扩展"机制（如浏览器扩展、IDE插件）

功能:
  1. Plugin 基类：定义统一的生命周期钩子接口
     - on_load() / on_unload()         加载/卸载
     - on_message(message)             消息处理
     - on_tool_call(tool, params)      工具调用前拦截
     - on_response(response)           响应生成后处理
  2. PluginManager：插件注册/卸载/启用/禁用/钩子触发
  3. 内置示例插件：
     - LoggingPlugin   记录所有消息与工具调用
     - MetricsPlugin   统计工具使用频率与响应时间
     - SafetyPlugin    敏感词过滤扩展
  4. 插件沙箱：异常隔离 + 执行超时 + 资源限制
  5. 状态持久化到 SCU3_data/plugins_state.json
  6. 单例 get_plugin_manager()

架构归属：M层（元认知层的扩展机制）
依赖：标准库 importlib / threading / json
"""

import os
import sys
import json
import time
import logging
import threading
import importlib
import importlib.util
from typing import Dict, Any, List, Optional, Callable, Tuple

logger = logging.getLogger("SCU3.m.plugins")

# ─── 沙箱默认配置 ────────────────────────────────────
_DEFAULT_TIMEOUT_SEC: float = 5.0          # 单个钩子执行超时（秒）
_DEFAULT_MAX_CALLS: int = 10000            # 单个插件最大调用次数（资源限制）
_DEFAULT_MAX_EXEC_SEC: float = 300.0       # 单个插件累计执行时间上限（秒）


class Plugin:
    """插件基类

    所有自定义插件继承此类，按需重写生命周期钩子。

    用法:
        class MyPlugin(Plugin):
            name = "my_plugin"
            version = "1.0.0"
            author = "SCU3"
            description = "示例插件"

            def on_message(self, message):
                return {"echo": message}
    """

    # 插件元信息（子类覆盖）
    name: str = "base_plugin"
    version: str = "0.0.1"
    author: str = "unknown"
    description: str = "插件基类"

    def __init__(self):
        self.enabled: bool = True
        # 由 PluginManager 注入的配置（dict）
        self.config: Dict[str, Any] = {}

    # ─── 生命周期钩子 ────────────────────────────────
    def on_load(self) -> None:
        """插件加载时调用（可选重写：初始化资源）"""
        pass

    def on_unload(self) -> None:
        """插件卸载时调用（可选重写：释放资源）"""
        pass

    def on_message(self, message: Any) -> Any:
        """消息处理钩子（可选重写）

        Args:
            message: 消息内容（str 或 dict）

        Returns:
            处理结果，将被 PluginManager 收集
        """
        return None

    def on_tool_call(self, tool: str, params: Dict[str, Any]) -> Any:
        """工具调用前钩子（可选重写：拦截/修改/记录工具调用）

        Args:
            tool: 工具名称
            params: 工具参数

        Returns:
            处理结果，将被 PluginManager 收集
        """
        return None

    def on_response(self, response: Any) -> Any:
        """响应生成后钩子（可选重写：后处理响应）

        Args:
            response: 响应内容

        Returns:
            处理结果，将被 PluginManager 收集
        """
        return None

    def get_info(self) -> Dict[str, Any]:
        """获取插件元信息"""
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "enabled": self.enabled,
            "config": self.config,
        }


class PluginSandbox:
    """插件沙箱：异常隔离 + 执行超时 + 资源限制

    - 异常隔离：单个插件抛异常不影响其他插件
    - 执行超时：钩子在子线程执行，超时则放弃结果
    - 资源限制：累计调用次数与累计执行时间上限
    """

    def __init__(self,
                 timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
                 max_calls: int = _DEFAULT_MAX_CALLS,
                 max_exec_sec: float = _DEFAULT_MAX_EXEC_SEC):
        self.timeout_sec = timeout_sec
        self.max_calls = max_calls
        self.max_exec_sec = max_exec_sec
        # 每个插件的资源统计: {plugin_name: {"calls": int, "exec_sec": float}}
        self._stats: Dict[str, Dict[str, float]] = {}
        self._lock = threading.Lock()

    def _ensure_stat(self, name: str) -> Dict[str, float]:
        if name not in self._stats:
            self._stats[name] = {"calls": 0, "exec_sec": 0.0}
        return self._stats[name]

    def execute(self, plugin: Plugin, hook: Callable, *args, **kwargs) -> Tuple[bool, Any]:
        """在沙箱内执行插件钩子

        Args:
            plugin: 插件实例
            hook: 可调用钩子（如 plugin.on_message）
            *args, **kwargs: 钩子参数

        Returns:
            (success, result)
            - success=False 表示执行失败/超时/资源耗尽，result 为错误信息或 None
            - success=True 表示执行成功，result 为钩子返回值
        """
        name = plugin.name

        with self._lock:
            stat = self._ensure_stat(name)
            # 资源限制检查
            if stat["calls"] >= self.max_calls:
                logger.warning(f"插件 {name} 达到调用次数上限 ({self.max_calls})，跳过执行")
                return False, None
            if stat["exec_sec"] >= self.max_exec_sec:
                logger.warning(f"插件 {name} 达到累计执行时间上限 ({self.max_exec_sec}s)，跳过执行")
                return False, None
            stat["calls"] += 1

        # 子线程执行 + 超时控制（兼容 Windows）
        result_box: Dict[str, Any] = {"result": None, "error": None, "done": False}

        def _runner():
            try:
                result_box["result"] = hook(*args, **kwargs)
            except Exception as e:
                result_box["error"] = e
            finally:
                result_box["done"] = True

        t0 = time.monotonic()
        worker = threading.Thread(target=_runner, daemon=True)
        worker.start()
        worker.join(timeout=self.timeout_sec)
        elapsed = time.monotonic() - t0

        with self._lock:
            stat = self._ensure_stat(name)
            stat["exec_sec"] += elapsed

        # 超时
        if worker.is_alive():
            logger.warning(f"插件 {name} 钩子执行超时 (>{self.timeout_sec}s)，已放弃")
            # 线程为 daemon，主进程退出时会终止；此处不强制 join
            return False, None

        # 异常隔离
        if result_box["error"] is not None:
            logger.warning(f"插件 {name} 钩子抛出异常: {result_box['error']}", exc_info=False)
            return False, None

        return True, result_box["result"]

    def get_stats(self) -> Dict[str, Dict[str, float]]:
        """获取所有插件的资源使用统计"""
        with self._lock:
            return {k: dict(v) for k, v in self._stats.items()}

    def reset_stats(self, name: Optional[str] = None) -> None:
        """重置资源统计（指定插件或全部）"""
        with self._lock:
            if name is None:
                self._stats.clear()
            else:
                self._stats.pop(name, None)


class PluginManager:
    """插件管理器

    用法:
        pm = get_plugin_manager()
        pm.register_plugin(LoggingPlugin())
        pm.load_from_directory("plugins/")
        results = pm.trigger_hook("on_message", "你好")
    """

    def __init__(self, data_dir: Optional[str] = None):
        # 数据目录：SCU3_data/
        if data_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, "SCU3_data")
        self._data_dir = data_dir
        os.makedirs(self._data_dir, exist_ok=True)
        self._state_path = os.path.join(self._data_dir, "plugins_state.json")

        # 已注册插件: {name: Plugin}
        self._plugins: Dict[str, Plugin] = {}
        # 插件配置: {name: dict}
        self._configs: Dict[str, Dict[str, Any]] = {}
        # 沙箱
        self._sandbox = PluginSandbox()
        # 线程锁
        self._lock = threading.RLock()

        # 钩子名 → 插件方法名 映射
        self._hook_map: Dict[str, str] = {
            "on_load": "on_load",
            "on_unload": "on_unload",
            "on_message": "on_message",
            "on_tool_call": "on_tool_call",
            "on_response": "on_response",
        }

        # 恢复持久化状态（仅恢复配置与启用状态，插件实例需重新注册）
        self._load_state()

    # ─── 注册 / 卸载 ────────────────────────────────
    def register_plugin(self, plugin: Plugin) -> bool:
        """注册插件

        Args:
            plugin: Plugin 实例

        Returns:
            是否注册成功
        """
        if not isinstance(plugin, Plugin):
            logger.warning(f"注册失败：{type(plugin)} 不是 Plugin 子类")
            return False

        name = plugin.name
        with self._lock:
            if name in self._plugins:
                logger.warning(f"插件 {name} 已存在，覆盖注册")
                # 先卸载旧的
                try:
                    self._plugins[name].on_unload()
                except Exception as e:
                    logger.warning(f"旧插件 {name} 卸载异常: {e}")

            # 恢复持久化的配置与启用状态
            saved_config = self._configs.get(name)
            if saved_config is not None:
                plugin.config = dict(saved_config)
            else:
                self._configs[name] = dict(plugin.config)

            self._plugins[name] = plugin

            # 触发 on_load（沙箱内执行）
            self._sandbox.execute(plugin, plugin.on_load)
            logger.info(f"插件已注册: {name} v{plugin.version} (enabled={plugin.enabled})")
            self._save_state()
            return True

    def unregister_plugin(self, name: str) -> bool:
        """卸载插件

        Args:
            name: 插件名称

        Returns:
            是否卸载成功
        """
        with self._lock:
            plugin = self._plugins.pop(name, None)
            if plugin is None:
                logger.warning(f"卸载失败：插件 {name} 不存在")
                return False
            # 触发 on_unload（沙箱内执行）
            self._sandbox.execute(plugin, plugin.on_unload)
            logger.info(f"插件已卸载: {name}")
            self._save_state()
            return True

    # ─── 启用 / 禁用 ────────────────────────────────
    def enable_plugin(self, name: str) -> bool:
        """启用插件"""
        with self._lock:
            plugin = self._plugins.get(name)
            if plugin is None:
                logger.warning(f"启用失败：插件 {name} 不存在")
                return False
            plugin.enabled = True
            logger.info(f"插件已启用: {name}")
            self._save_state()
            return True

    def disable_plugin(self, name: str) -> bool:
        """禁用插件"""
        with self._lock:
            plugin = self._plugins.get(name)
            if plugin is None:
                logger.warning(f"禁用失败：插件 {name} 不存在")
                return False
            plugin.enabled = False
            logger.info(f"插件已禁用: {name}")
            self._save_state()
            return True

    # ─── 查询 ────────────────────────────────────────
    def get_plugin(self, name: str) -> Optional[Plugin]:
        """按名称获取插件"""
        with self._lock:
            return self._plugins.get(name)

    def list_plugins(self) -> List[Dict[str, Any]]:
        """列出所有插件信息"""
        with self._lock:
            return [p.get_info() for p in self._plugins.values()]

    # ─── 钩子触发 ────────────────────────────────────
    def trigger_hook(self, hook_name: str, *args, **kwargs) -> List[Dict[str, Any]]:
        """触发钩子，收集所有已启用插件的返回值

        Args:
            hook_name: 钩子名（on_message / on_tool_call / on_response 等）
            *args, **kwargs: 钩子参数

        Returns:
            [{"plugin": name, "success": bool, "result": Any}, ...]
        """
        method_name = self._hook_map.get(hook_name)
        if method_name is None:
            logger.warning(f"未知钩子: {hook_name}")
            return []

        results: List[Dict[str, Any]] = []
        # 复制一份避免迭代时被修改
        with self._lock:
            plugins_snapshot = list(self._plugins.values())

        for plugin in plugins_snapshot:
            if not plugin.enabled:
                continue
            hook = getattr(plugin, method_name, None)
            if hook is None or not callable(hook):
                continue
            success, result = self._sandbox.execute(plugin, hook, *args, **kwargs)
            results.append({
                "plugin": plugin.name,
                "success": success,
                "result": result,
            })
        return results

    # ─── 配置管理 ────────────────────────────────────
    def get_config(self, name: str) -> Optional[Dict[str, Any]]:
        """获取插件配置"""
        with self._lock:
            return dict(self._configs.get(name, {}))

    def set_config(self, name: str, config: Dict[str, Any]) -> bool:
        """设置插件配置

        Args:
            name: 插件名称
            config: 配置字典

        Returns:
            是否设置成功
        """
        if not isinstance(config, dict):
            logger.warning(f"配置必须是 dict，收到 {type(config)}")
            return False
        with self._lock:
            self._configs[name] = dict(config)
            plugin = self._plugins.get(name)
            if plugin is not None:
                plugin.config = dict(config)
            logger.info(f"插件配置已更新: {name}")
            self._save_state()
            return True

    # ─── 动态加载 ────────────────────────────────────
    def load_from_directory(self, plugins_dir: str) -> List[str]:
        """从目录动态加载插件

        约定：目录下每个 .py 文件为一个插件模块，模块需定义名为
        ``create_plugin() -> Plugin`` 的工厂函数，或包含一个 Plugin 子类实例
        属性 ``plugin`` / ``PLUGIN``。

        Args:
            plugins_dir: 插件目录路径

        Returns:
            成功加载的插件名称列表
        """
        if not os.path.isdir(plugins_dir):
            logger.warning(f"插件目录不存在: {plugins_dir}")
            return []

        loaded: List[str] = []
        # 保证目录可被 import
        if plugins_dir not in sys.path:
            sys.path.insert(0, plugins_dir)

        for fname in sorted(os.listdir(plugins_dir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            mod_name = fname[:-3]
            plugin = self._load_plugin_module(plugins_dir, fname, mod_name)
            if plugin is not None:
                if self.register_plugin(plugin):
                    loaded.append(plugin.name)

        logger.info(f"从目录 {plugins_dir} 加载了 {len(loaded)} 个插件: {loaded}")
        return loaded

    def _load_plugin_module(self, plugins_dir: str, fname: str,
                            mod_name: str) -> Optional[Plugin]:
        """加载单个插件模块（importlib 动态加载）"""
        file_path = os.path.join(plugins_dir, fname)
        try:
            # 使用 importlib 动态加载，避免模块名冲突
            spec = importlib.util.spec_from_file_location(
                f"SCU3_plugin_{mod_name}", file_path)
            if spec is None or spec.loader is None:
                logger.warning(f"无法为 {fname} 创建模块规格")
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # 优先使用工厂函数 create_plugin()
            factory = getattr(module, "create_plugin", None)
            if callable(factory):
                plugin = factory()
                if isinstance(plugin, Plugin):
                    return plugin
                logger.warning(f"{fname} 的 create_plugin() 未返回 Plugin 实例")
                return None

            # 其次使用模块级实例 plugin / PLUGIN
            for attr in ("plugin", "PLUGIN"):
                obj = getattr(module, attr, None)
                if isinstance(obj, Plugin):
                    return obj

            logger.warning(f"{fname} 未定义 create_plugin() 或 plugin/PLUGIN 实例，跳过")
            return None
        except Exception as e:
            logger.warning(f"加载插件模块 {fname} 失败: {e}", exc_info=True)
            return None

    # ─── 沙箱 / 统计 ────────────────────────────────
    def get_sandbox_stats(self) -> Dict[str, Dict[str, float]]:
        """获取插件沙箱资源使用统计"""
        return self._sandbox.get_stats()

    def reset_sandbox_stats(self, name: Optional[str] = None) -> None:
        """重置沙箱统计"""
        self._sandbox.reset_stats(name)

    # ─── 持久化 ──────────────────────────────────────
    def _save_state(self) -> None:
        """保存插件状态到 SCU3_data/plugins_state.json"""
        state = {
            "plugins": {},
            "saved_at": __import__("datetime").datetime.now().isoformat(),
        }
        with self._lock:
            for name, plugin in self._plugins.items():
                state["plugins"][name] = {
                    "enabled": plugin.enabled,
                    "config": dict(plugin.config),
                    "version": plugin.version,
                }
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存插件状态失败: {e}")

    def _load_state(self) -> None:
        """从 SCU3_data/plugins_state.json 恢复配置与启用状态

        注意：仅恢复配置字典与启用标志，插件实例需在重新注册后才会生效。
        """
        if not os.path.exists(self._state_path):
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                state = json.loads(f.read())
        except Exception as e:
            logger.warning(f"加载插件状态失败: {e}")
            return

        plugins_state = state.get("plugins", {})
        # 预存配置，待插件注册时应用
        for name, info in plugins_state.items():
            self._configs[name] = dict(info.get("config", {}))
        logger.info(f"已恢复 {len(plugins_state)} 个插件的配置状态")


# ══════════════════════════════════════════════════════
# 内置示例插件
# ══════════════════════════════════════════════════════
class LoggingPlugin(Plugin):
    """日志插件：记录所有消息与工具调用"""

    name = "logging"
    version = "1.0.0"
    author = "SCU3"
    description = "记录所有消息和工具调用到日志"

    def __init__(self):
        super().__init__()
        self._log_logger = logging.getLogger("SCU3.m.plugins.logging")

    def on_load(self) -> None:
        self._log_logger.info("LoggingPlugin 已加载")

    def on_unload(self) -> None:
        self._log_logger.info("LoggingPlugin 已卸载")

    def on_message(self, message: Any) -> Dict[str, Any]:
        self._log_logger.info(f"[消息] {message}")
        return {"logged": True, "type": "message"}

    def on_tool_call(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._log_logger.info(f"[工具调用] {tool} 参数={params}")
        return {"logged": True, "type": "tool_call", "tool": tool}

    def on_response(self, response: Any) -> Dict[str, Any]:
        self._log_logger.info(f"[响应] {response}")
        return {"logged": True, "type": "response"}


class MetricsPlugin(Plugin):
    """指标插件：统计工具使用频率与响应时间"""

    name = "metrics"
    version = "1.0.0"
    author = "SCU3"
    description = "统计工具使用频率与响应时间"

    def __init__(self):
        super().__init__()
        self._metrics_logger = logging.getLogger("SCU3.m.plugins.metrics")
        # 工具调用次数: {tool_name: count}
        self._tool_call_counts: Dict[str, int] = {}
        # 工具响应时间: {tool_name: [ms, ms, ...]}
        self._tool_response_times: Dict[str, List[float]] = {}
        # 消息计数
        self._message_count: int = 0
        self._lock = threading.Lock()

    def on_load(self) -> None:
        self._metrics_logger.info("MetricsPlugin 已加载")

    def on_unload(self) -> None:
        self._metrics_logger.info(f"MetricsPlugin 卸载，最终统计: {self.get_metrics()}")

    def on_message(self, message: Any) -> Dict[str, Any]:
        with self._lock:
            self._message_count += 1
        return {"messages": self._message_count}

    def on_tool_call(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._tool_call_counts[tool] = self._tool_call_counts.get(tool, 0) + 1
            # 记录调用起始时间（存入 params 副本，不污染原参数）
        return {"tool": tool, "count": self._tool_call_counts.get(tool, 0)}

    def on_response(self, response: Any) -> Dict[str, Any]:
        # 响应时间统计：若响应中带 elapsed_ms，则按工具归类
        if isinstance(response, dict) and "tool" in response and "elapsed_ms" in response:
            tool = response["tool"]
            ms = float(response["elapsed_ms"])
            with self._lock:
                self._tool_response_times.setdefault(tool, []).append(ms)
        return {"recorded": True}

    def get_metrics(self) -> Dict[str, Any]:
        """获取指标统计快照"""
        with self._lock:
            tool_stats = {}
            for tool, times in self._tool_response_times.items():
                tool_stats[tool] = {
                    "count": self._tool_call_counts.get(tool, 0),
                    "avg_ms": sum(times) / len(times) if times else 0.0,
                    "max_ms": max(times) if times else 0.0,
                    "min_ms": min(times) if times else 0.0,
                }
            # 仅记录了调用次数但无响应时间的工具
            for tool, cnt in self._tool_call_counts.items():
                if tool not in tool_stats:
                    tool_stats[tool] = {"count": cnt, "avg_ms": 0.0,
                                        "max_ms": 0.0, "min_ms": 0.0}
            return {
                "message_count": self._message_count,
                "tool_call_counts": dict(self._tool_call_counts),
                "tool_stats": tool_stats,
            }


class SafetyPlugin(Plugin):
    """安全插件：敏感词过滤扩展

    在 on_message / on_response 中对文本进行敏感词过滤，
    默认敏感词库可通过 config["sensitive_words"] 自定义。
    """

    name = "safety"
    version = "1.0.0"
    author = "SCU3"
    description = "敏感词过滤扩展"

    # 内置基础敏感词（可被 config 覆盖/扩展）
    _BUILTIN_WORDS: List[str] = [
        "密码", "token", "secret", "api_key", "private_key",
        "银行卡", "身份证号",
    ]

    def __init__(self):
        super().__init__()
        self._safety_logger = logging.getLogger("SCU3.m.plugins.safety")
        self._filtered_count: int = 0
        self._lock = threading.Lock()

    def on_load(self) -> None:
        words = self._get_sensitive_words()
        self._safety_logger.info(f"SafetyPlugin 已加载，敏感词库 {len(words)} 条")

    def on_message(self, message: Any) -> Dict[str, Any]:
        filtered, hits = self._filter_text(message)
        return {"filtered": filtered, "hits": hits, "stage": "message"}

    def on_response(self, response: Any) -> Dict[str, Any]:
        filtered, hits = self._filter_text(response)
        return {"filtered": filtered, "hits": hits, "stage": "response"}

    def _get_sensitive_words(self) -> List[str]:
        """获取敏感词库（内置 + config 扩展）"""
        words = list(self._BUILTIN_WORDS)
        extra = self.config.get("sensitive_words", [])
        if isinstance(extra, list):
            words.extend(str(w) for w in extra)
        # 去重保序
        seen = set()
        result = []
        for w in words:
            lw = w.lower()
            if lw not in seen:
                seen.add(lw)
                result.append(w)
        return result

    def _filter_text(self, text: Any) -> Tuple[Any, List[str]]:
        """对文本进行敏感词过滤

        Returns:
            (filtered_text, hits)  hits 为命中的敏感词列表
        """
        if not isinstance(text, str):
            # 非字符串（如 dict）尝试序列化后过滤再还原
            if isinstance(text, (dict, list)):
                try:
                    raw = json.dumps(text, ensure_ascii=False)
                except Exception:
                    return text, []
                filtered_raw, hits = self._filter_str(raw)
                if not hits:
                    return text, []
                try:
                    filtered = json.loads(filtered_raw)
                except Exception:
                    return filtered_raw, hits
                return filtered, hits
            return text, []

        return self._filter_str(text)

    def _filter_str(self, text: str) -> Tuple[str, List[str]]:
        """字符串敏感词过滤"""
        words = self._get_sensitive_words()
        hits: List[str] = []
        filtered = text
        for w in words:
            if w in filtered:
                hits.append(w)
                filtered = filtered.replace(w, "[REDACTED]")
        if hits:
            with self._lock:
                self._filtered_count += 1
            self._safety_logger.warning(f"敏感词过滤命中 {len(hits)} 处: {hits}")
        return filtered, hits

    def get_stats(self) -> Dict[str, Any]:
        """获取过滤统计"""
        return {
            "filtered_count": self._filtered_count,
            "word_count": len(self._get_sensitive_words()),
        }


# ─── 单例 ────────────────────────────────────
_plugin_manager_instance: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """获取插件管理器单例

    首次调用会自动注册三个内置插件（logging / metrics / safety）。
    """
    global _plugin_manager_instance
    if _plugin_manager_instance is None:
        _plugin_manager_instance = PluginManager()
        # 注册内置示例插件
        _register_builtin_plugins(_plugin_manager_instance)
    return _plugin_manager_instance


def _register_builtin_plugins(pm: PluginManager) -> None:
    """注册内置示例插件"""
    builtins = [LoggingPlugin(), MetricsPlugin(), SafetyPlugin()]
    for plugin in builtins:
        pm.register_plugin(plugin)
    logger.info(f"已注册 {len(builtins)} 个内置插件")
