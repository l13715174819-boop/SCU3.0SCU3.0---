# -*- coding: utf-8 -*-
"""
m_layer/mcp_protocol.py — MCP（Model Context Protocol）协议支持模块
================================================================
实现MCP协议客户端和服务端，支持SCU3工具以MCP标准对外暴露，
并连接外部MCP服务器扩展能力边界。

功能：
  1. MCPClient：连接外部MCP服务器，列出/调用远程工具，订阅/读取资源，自动重连
  2. MCPServer：把SCU3本地工具（action.py 13种 + extended_tools.py 扩展工具）暴露为MCP服务
  3. JSON-RPC 2.0 消息格式实现
  4. MCPRegistry：多服务器连接管理、工具路由（本地优先远程兜底）、健康检查、故障转移
  5. 状态持久化到 SCU3_data/mcp_state.json

消息格式（JSON-RPC 2.0）：
  请求: {"jsonrpc": "2.0", "id": "xxx", "method": "tools/list", "params": {...}}
  响应: {"jsonrpc": "2.0", "id": "xxx", "result": {...}}
  错误: {"jsonrpc": "2.0", "id": "xxx", "error": {"code": -32601, "message": "..."}}

架构归属：M层（元认知/协议层）
依赖：w1_layer/action, w1_layer/extended_tools
"""
import os
import json
import time
import uuid
import logging
import threading
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List, Callable, Tuple
from core.abc import PersistableMixin, StatusableMixin

logger = logging.getLogger("SCU3.m.mcp")

# 项目根目录与数据目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "SCU3_data")
STATE_FILE = os.path.join(DATA_DIR, "mcp_state.json")

# JSON-RPC 2.0 标准错误码
RPC_PARSE_ERROR = -32700       # 解析错误
RPC_INVALID_REQUEST = -32600   # 无效请求
RPC_METHOD_NOT_FOUND = -32601  # 方法不存在
RPC_INVALID_PARAMS = -32602    # 参数无效
RPC_INTERNAL_ERROR = -32603    # 内部错误

# MCP协议版本
MCP_PROTOCOL_VERSION = "2024-11-05"

# 默认HTTP超时（秒）
DEFAULT_TIMEOUT = 15.0

# 自动重连参数
RECONNECT_MAX_RETRIES = 3
RECONNECT_BACKOFF_BASE = 1.0  # 指数退避基数（秒）


# ─── JSON-RPC 2.0 消息构造 ────────────────────────────────────

def make_request(method: str, params: Optional[Dict] = None,
                 req_id: Optional[str] = None) -> Dict[str, Any]:
    """构造JSON-RPC 2.0请求消息"""
    msg = {"jsonrpc": "2.0", "id": req_id or str(uuid.uuid4()), "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_response(req_id: str, result: Any) -> Dict[str, Any]:
    """构造JSON-RPC 2.0成功响应"""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id: Optional[str], code: int, message: str,
               data: Any = None) -> Dict[str, Any]:
    """构造JSON-RPC 2.0错误响应"""
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def is_valid_request(msg: Dict) -> bool:
    """校验是否为合法的JSON-RPC 2.0请求"""
    return (isinstance(msg, dict)
            and msg.get("jsonrpc") == "2.0"
            and "method" in msg)


# ─── HTTP传输层 ────────────────────────────────────

def _http_post_json(url: str, payload: Dict, api_key: Optional[str] = None,
                    timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """通过HTTP POST发送JSON-RPC消息并返回响应

    使用urllib.request避免外部依赖。
    """
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"SCU3-MCP/1.0 (protocol={MCP_PROTOCOL_VERSION})",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        # 尝试读取错误响应体
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
            return json.loads(err_body)
        except (json.JSONDecodeError, ValueError):
            return make_error(None, RPC_INTERNAL_ERROR,
                              f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        return make_error(None, RPC_INTERNAL_ERROR, f"连接失败: {e.reason}")
    except json.JSONDecodeError:
        return make_error(None, RPC_PARSE_ERROR, "响应非合法JSON")


# ─── 本地工具Schema定义 ────────────────────────────────────

def _build_action_tool_schemas() -> List[Dict[str, Any]]:
    """构建 action.py 的13种工具MCP Schema"""
    return [
        {
            "name": "calculator",
            "description": "计算器（AST安全求值，支持加减乘除幂模及abs/round/min/max）",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "数学表达式"}},
                "required": ["expression"],
            },
        },
        {
            "name": "weather",
            "description": "天气查询",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名"}},
                "required": ["city"],
            },
        },
        {
            "name": "time_now",
            "description": "获取当前时间",
            "type": "read",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "text_stats",
            "description": "文本统计（字符数/词数/行数）",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "待统计文本"}},
                "required": ["text"],
            },
        },
        {
            "name": "file_read",
            "description": "文件读取（限制在sandbox目录，禁止读敏感文件）",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"],
            },
        },
        {
            "name": "exchange_rate",
            "description": "汇率查询",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"base": {"type": "string", "default": "USD", "description": "基准货币"}},
            },
        },
        {
            "name": "crypto_price",
            "description": "加密货币价格查询",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "default": "btc", "description": "币种符号"}},
            },
        },
        {
            "name": "stock_price",
            "description": "股票行情查询",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string", "default": "AAPL", "description": "股票代码"}},
            },
        },
        {
            "name": "github_search",
            "description": "GitHub仓库搜索",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
        },
        {
            "name": "datetime_calc",
            "description": "日期计算（加减天数）",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                    "op": {"type": "string", "enum": ["+", "-"], "default": "+"},
                    "days": {"type": "integer", "default": 0},
                },
                "required": ["start"],
            },
        },
        {
            "name": "unit_convert",
            "description": "单位换算（温度/长度/重量）",
            "type": "read",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "value": {"type": "number"},
                    "from_unit": {"type": "string"},
                    "to_unit": {"type": "string"},
                },
                "required": ["value", "from_unit", "to_unit"],
            },
        },
        {
            "name": "file_write",
            "description": "文件写入（限制在sandbox目录）",
            "type": "write",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "code_run",
            "description": "代码执行（沙箱隔离，AST预检+超时限制）",
            "type": "write",
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python代码"}},
                "required": ["code"],
            },
        },
    ]


def _build_extended_tool_schemas() -> List[Dict[str, Any]]:
    """构建 extended_tools.py 的扩展工具MCP Schema"""
    return [
        {"name": "web_search", "description": "网络搜索（DuckDuckGo，无需API Key）", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "default": 5}},
                         "required": ["query"]}},
        {"name": "web_fetch", "description": "抓取网页内容", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"url": {"type": "string"}, "max_length": {"type": "integer", "default": 5000}},
                         "required": ["url"]}},
        {"name": "git_status", "description": "Git状态查询", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"repo_path": {"type": "string", "default": "."}}}},
        {"name": "git_log", "description": "Git日志查询", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"repo_path": {"type": "string", "default": "."}, "limit": {"type": "integer", "default": 10}}}},
        {"name": "git_diff", "description": "Git差异查询", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"repo_path": {"type": "string", "default": "."}, "file": {"type": "string", "default": ""}}}},
        {"name": "pdf_read", "description": "PDF文本提取", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"path": {"type": "string"}, "max_pages": {"type": "integer", "default": 50}},
                         "required": ["path"]}},
        {"name": "image_info", "description": "图片信息查询", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"name": "json_query", "description": "JSON数据查询（支持点号路径）", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"json_str": {"type": "string"}, "query": {"type": "string", "default": ""}},
                         "required": ["json_str"]}},
        {"name": "regex_match", "description": "正则匹配", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"text": {"type": "string"}, "pattern": {"type": "string"}, "flags": {"type": "string", "default": ""}},
                         "required": ["text", "pattern"]}},
        {"name": "hash_calc", "description": "哈希计算（md5/sha256等）", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"text": {"type": "string"}, "algorithm": {"type": "string", "default": "md5"}},
                         "required": ["text"]}},
        {"name": "base64_codec", "description": "Base64编解码", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"text": {"type": "string"}, "mode": {"type": "string", "enum": ["encode", "decode"], "default": "encode"}},
                         "required": ["text"]}},
        {"name": "url_codec", "description": "URL编解码", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"text": {"type": "string"}, "mode": {"type": "string", "enum": ["encode", "decode"], "default": "encode"}},
                         "required": ["text"]}},
        {"name": "shell_exec", "description": "Shell命令执行（沙箱+白名单+超时）", "type": "write",
         "inputSchema": {"type": "object",
                         "properties": {"command": {"type": "string"}, "timeout": {"type": "number", "default": 10.0}},
                         "required": ["command"]}},
        {"name": "file_copy", "description": "文件复制", "type": "write",
         "inputSchema": {"type": "object",
                         "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
                         "required": ["src", "dst"]}},
        {"name": "file_move", "description": "文件移动", "type": "write",
         "inputSchema": {"type": "object",
                         "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
                         "required": ["src", "dst"]}},
        {"name": "file_delete", "description": "文件删除（限sandbox）", "type": "write",
         "inputSchema": {"type": "object",
                         "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"name": "dir_create", "description": "创建目录", "type": "write",
         "inputSchema": {"type": "object",
                         "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"name": "dir_list", "description": "列出目录内容", "type": "read",
         "inputSchema": {"type": "object",
                         "properties": {"path": {"type": "string", "default": "."}}}},
    ]


# ─── MCP客户端 ────────────────────────────────────

class MCPClient:
    """MCP协议客户端

    连接外部MCP服务器，调用远程工具、读取/订阅资源。
    支持自动重连机制。

    用法:
        client = MCPClient()
        client.connect("https://mcp.example.com/rpc", api_key="sk-xxx")
        tools = client.list_tools()
        result = client.call_tool("remote_tool", {"arg": "val"})
        client.disconnect()
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self._server_url: Optional[str] = None
        self._api_key: Optional[str] = None
        self._connected: bool = False
        self._tools_cache: List[Dict] = []
        self._subscriptions: Dict[str, float] = {}  # resource_uri -> 订阅时间戳
        # 自动重连状态
        self._auto_reconnect: bool = True
        self._last_ping: float = 0.0
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def server_url(self) -> Optional[str]:
        return self._server_url

    def connect(self, server_url: str, api_key: Optional[str] = None) -> bool:
        """连接到外部MCP服务器

        Args:
            server_url: MCP服务器JSON-RPC端点URL
            api_key: 认证密钥（可选）

        Returns:
            是否连接成功
        """
        self._server_url = server_url
        self._api_key = api_key
        try:
            # 发送initialize握手
            resp = self._send_rpc("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}},
                "clientInfo": {"name": "SCU3-mcp-client", "version": "1.0"},
            })
            if "error" in resp:
                logger.warning(f"[{self.name}] MCP连接握手失败: {resp['error'].get('message')}")
                self._connected = False
                return False
            # 发送initialized通知
            self._send_rpc("notifications/initialized", {})
            self._connected = True
            self._last_ping = time.time()
            # 预加载工具列表
            self._tools_cache = self._do_list_tools()
            logger.info(f"[{self.name}] 已连接MCP服务器: {server_url}, "
                        f"可用工具{len(self._tools_cache)}个")
            return True
        except Exception as e:
            logger.warning(f"[{self.name}] 连接MCP服务器失败: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """断开与MCP服务器的连接"""
        if self._connected and self._server_url:
            try:
                self._send_rpc("shutdown", {})
            except Exception:
                pass
        self._connected = False
        self._tools_cache = []
        self._subscriptions.clear()
        logger.info(f"[{self.name}] 已断开MCP连接")

    def list_tools(self) -> List[Dict[str, Any]]:
        """列出远程服务器提供的工具

        Returns:
            工具Schema列表，失败时返回空列表
        """
        if not self._ensure_connected():
            return list(self._tools_cache)
        return self._do_list_tools()

    def _do_list_tools(self) -> List[Dict[str, Any]]:
        """实际执行工具列表查询"""
        resp = self._send_rpc("tools/list", {})
        if "error" in resp:
            logger.warning(f"[{self.name}] 列出工具失败: {resp['error'].get('message')}")
            return []
        result = resp.get("result", {})
        tools = result.get("tools", [])
        self._tools_cache = tools
        return tools

    def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """调用远程工具

        Args:
            tool_name: 工具名称
            params: 工具参数

        Returns:
            工具执行结果 {"success": bool, "result": ..., "error": ...}
        """
        if not self._ensure_connected():
            return {"success": False, "error": "未连接到MCP服务器"}
        resp = self._send_rpc("tools/call", {"name": tool_name, "arguments": params})
        if "error" in resp:
            return {"success": False, "tool": tool_name,
                    "error": resp["error"].get("message", "远程调用失败")}
        result = resp.get("result", {})
        # MCP标准结果可能包含 content 数组
        return {"success": True, "tool": tool_name, "result": result}

    def subscribe(self, resource_uri: str) -> bool:
        """订阅资源

        Args:
            resource_uri: 资源URI（如 "file:///path" 或 "SCU3://knowledge/xxx"）

        Returns:
            是否订阅成功
        """
        if not self._ensure_connected():
            return False
        resp = self._send_rpc("resources/subscribe", {"uri": resource_uri})
        if "error" in resp:
            logger.warning(f"[{self.name}] 订阅资源失败 {resource_uri}: "
                          f"{resp['error'].get('message')}")
            return False
        self._subscriptions[resource_uri] = time.time()
        logger.info(f"[{self.name}] 已订阅资源: {resource_uri}")
        return True

    def read_resource(self, resource_uri: str) -> Dict[str, Any]:
        """读取资源内容

        Args:
            resource_uri: 资源URI

        Returns:
            {"success": bool, "content": ..., "error": ...}
        """
        if not self._ensure_connected():
            return {"success": False, "error": "未连接到MCP服务器"}
        resp = self._send_rpc("resources/read", {"uri": resource_uri})
        if "error" in resp:
            return {"success": False, "uri": resource_uri,
                    "error": resp["error"].get("message", "读取资源失败")}
        result = resp.get("result", {})
        return {"success": True, "uri": resource_uri, "content": result}

    def ping(self) -> bool:
        """健康检查ping"""
        if not self._server_url:
            return False
        try:
            resp = self._send_rpc("ping", {}, timeout=5.0)
            ok = "error" not in resp
            if ok:
                self._last_ping = time.time()
            return ok
        except Exception:
            return False

    def get_status(self) -> Dict[str, Any]:
        """获取客户端状态"""
        return {
            "name": self.name,
            "server_url": self._server_url,
            "connected": self._connected,
            "tools_count": len(self._tools_cache),
            "subscriptions": list(self._subscriptions.keys()),
            "last_ping": self._last_ping,
        }

    # ─── 内部方法 ────────────────────────────────────

    def _ensure_connected(self) -> bool:
        """确保已连接，必要时尝试自动重连"""
        if self._connected:
            return True
        if not self._auto_reconnect or not self._server_url:
            return False
        return self._reconnect()

    def _reconnect(self) -> bool:
        """自动重连（指数退避）"""
        for attempt in range(1, RECONNECT_MAX_RETRIES + 1):
            wait = RECONNECT_BACKOFF_BASE * (2 ** (attempt - 1))
            logger.info(f"[{self.name}] 尝试重连({attempt}/{RECONNECT_MAX_RETRIES})，"
                        f"等待{wait:.1f}s...")
            time.sleep(wait)
            if self.connect(self._server_url, self._api_key):
                logger.info(f"[{self.name}] 重连成功")
                return True
        logger.warning(f"[{self.name}] 重连失败，已达最大重试次数")
        return False

    def _send_rpc(self, method: str, params: Optional[Dict] = None,
                  timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """发送JSON-RPC请求到远程服务器

        通知类方法（notifications/*）无id无响应，返回空dict。
        """
        if not self._server_url:
            return make_error(None, RPC_INTERNAL_ERROR, "未配置服务器URL")
        # 通知类消息：无id，不期望响应
        is_notification = method.startswith("notifications/")
        if is_notification:
            msg = {"jsonrpc": "2.0", "method": method}
            if params:
                msg["params"] = params
        else:
            msg = make_request(method, params)

        resp = _http_post_json(self._server_url, msg, self._api_key, timeout)
        if is_notification:
            return {}
        return resp


# ─── MCP服务端 ────────────────────────────────────

class MCPServer:
    """MCP协议服务端

    把SCU3本地工具暴露为MCP服务，处理JSON-RPC 2.0请求。

    用法:
        server = MCPServer()
        server.register_local_tools()
        response = server.handle_request(request_dict)
    """

    def __init__(self):
        self._tool_schemas: List[Dict[str, Any]] = []
        self._tool_handlers: Dict[str, Callable[[Dict], Dict]] = {}
        self._tool_types: Dict[str, str] = {}
        self._resources: Dict[str, Dict[str, Any]] = {}  # uri -> 资源内容
        self._subscribers: Dict[str, List[str]] = {}      # uri -> 订阅者列表
        self._lock = threading.Lock()
        self._initialized: bool = False

    def register_local_tools(self) -> int:
        """注册SCU3本地工具（action.py 13种 + extended_tools.py 扩展工具）

        Returns:
            注册的工具数量
        """
        # 注册action.py的13种工具
        action_schemas = _build_action_tool_schemas()
        try:
            from w1_layer.action import ActionLayer
            action_layer = ActionLayer()
            for schema in action_schemas:
                name = schema["name"]
                self._tool_handlers[name] = lambda params, _a=action_layer, _n=name: \
                    _a.execute({"tool": _n, "params": params, "tool_type": schema["type"]})
                self._tool_types[name] = schema["type"]
        except Exception as e:
            logger.warning(f"注册action.py工具失败: {e}")

        # 注册extended_tools.py的扩展工具
        ext_schemas = _build_extended_tool_schemas()
        try:
            from w1_layer.extended_tools import get_extended_tools
            ext_tools = get_extended_tools()
            for schema in ext_schemas:
                name = schema["name"]
                self._tool_handlers[name] = lambda params, _e=ext_tools, _n=name: \
                    _e.execute(_n, params)
                self._tool_types[name] = schema["type"]
        except Exception as e:
            logger.warning(f"注册extended_tools.py工具失败: {e}")

        self._tool_schemas = action_schemas + ext_schemas
        logger.info(f"MCPServer已注册{len(self._tool_schemas)}个本地工具")
        return len(self._tool_schemas)

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """处理MCP JSON-RPC 2.0请求

        Args:
            request: JSON-RPC 2.0请求dict

        Returns:
            JSON-RPC 2.0响应dict
        """
        # 校验请求格式
        if not is_valid_request(request):
            return make_error(request.get("id"), RPC_INVALID_REQUEST,
                              "无效的JSON-RPC 2.0请求")

        method = request["method"]
        params = request.get("params", {})
        req_id = request.get("id")

        # 通知类消息：无id，无需响应
        if method.startswith("notifications/"):
            if method == "notifications/initialized":
                self._initialized = True
                logger.info("MCP客户端已初始化")
            return {}

        try:
            handler = self._METHOD_HANDLERS.get(method)
            if handler is None:
                return make_error(req_id, RPC_METHOD_NOT_FOUND,
                                  f"未知方法: {method}")
            result = handler(self, params)
            return make_response(req_id, result)
        except Exception as e:
            logger.exception(f"处理MCP请求失败: method={method}")
            return make_error(req_id, RPC_INTERNAL_ERROR, f"内部错误: {e}")

    # ─── JSON-RPC方法处理器 ────────────────────────────────────

    def _handle_initialize(self, params: Dict) -> Dict[str, Any]:
        """处理 initialize 握手"""
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"subscribe": True, "listChanged": True},
            },
            "serverInfo": {"name": "SCU3-mcp-server", "version": "1.0"},
        }

    def _handle_ping(self, params: Dict) -> Dict[str, Any]:
        """处理 ping 健康检查"""
        return {"pong": True, "timestamp": time.time()}

    def _handle_shutdown(self, params: Dict) -> Dict[str, Any]:
        """处理 shutdown"""
        self._initialized = False
        return {}

    def _handle_tools_list(self, params: Dict) -> Dict[str, Any]:
        """处理 tools/list — 列出所有工具能力声明"""
        return {"tools": self._tool_schemas}

    def _handle_tools_call(self, params: Dict) -> Dict[str, Any]:
        """处理 tools/call — 调用指定工具"""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if tool_name not in self._tool_handlers:
            raise ValueError(f"未知工具: {tool_name}")
        handler = self._tool_handlers[tool_name]
        result = handler(arguments)
        # 将SCU3工具结果转换为MCP标准content格式
        if isinstance(result, dict) and result.get("success"):
            content = [{"type": "text", "text": json.dumps(result.get("result", result),
                                                           ensure_ascii=False)}]
            return {"content": content, "isError": False, "_raw": result}
        else:
            err_msg = result.get("error", "工具执行失败") if isinstance(result, dict) else str(result)
            content = [{"type": "text", "text": json.dumps({"error": err_msg},
                                                           ensure_ascii=False)}]
            return {"content": content, "isError": True, "_raw": result}

    def _handle_resources_list(self, params: Dict) -> Dict[str, Any]:
        """处理 resources/list — 列出可用资源"""
        resources = []
        for uri, info in self._resources.items():
            resources.append({
                "uri": uri,
                "name": info.get("name", uri),
                "description": info.get("description", ""),
                "mimeType": info.get("mimeType", "application/json"),
            })
        return {"resources": resources}

    def _handle_resources_read(self, params: Dict) -> Dict[str, Any]:
        """处理 resources/read — 读取资源内容"""
        uri = params.get("uri", "")
        if uri not in self._resources:
            raise ValueError(f"资源不存在: {uri}")
        info = self._resources[uri]
        return {"contents": [{
            "uri": uri,
            "mimeType": info.get("mimeType", "application/json"),
            "text": json.dumps(info.get("data", {}), ensure_ascii=False),
        }]}

    def _handle_resources_subscribe(self, params: Dict) -> Dict[str, Any]:
        """处理 resources/subscribe — 订阅资源"""
        uri = params.get("uri", "")
        subscriber = params.get("subscriber", "anonymous")
        if uri not in self._resources:
            raise ValueError(f"资源不存在: {uri}")
        with self._lock:
            if uri not in self._subscribers:
                self._subscribers[uri] = []
            if subscriber not in self._subscribers[uri]:
                self._subscribers[uri].append(subscriber)
        return {"subscribed": True, "uri": uri}

    def _handle_resources_unsubscribe(self, params: Dict) -> Dict[str, Any]:
        """处理 resources/unsubscribe — 取消订阅"""
        uri = params.get("uri", "")
        subscriber = params.get("subscriber", "anonymous")
        with self._lock:
            if uri in self._subscribers:
                self._subscribers[uri] = [s for s in self._subscribers[uri] if s != subscriber]
        return {"unsubscribed": True, "uri": uri}

    def _handle_tools_capability(self, params: Dict) -> Dict[str, Any]:
        """处理 tools/capability — 工具能力声明"""
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "tools": [{"name": s["name"], "type": s.get("type", "read"),
                       "description": s.get("description", "")}
                      for s in self._tool_schemas],
            "toolsCount": len(self._tool_schemas),
            "readCount": sum(1 for s in self._tool_schemas if s.get("type") == "read"),
            "writeCount": sum(1 for s in self._tool_schemas if s.get("type") == "write"),
        }

    # 方法路由表（在类定义后绑定）
    _METHOD_HANDLERS: Dict[str, Callable] = {}

    # ─── 资源管理 ────────────────────────────────────

    def register_resource(self, uri: str, name: str, data: Any,
                          description: str = "",
                          mime_type: str = "application/json") -> None:
        """注册本地资源"""
        self._resources[uri] = {
            "name": name,
            "description": description,
            "mimeType": mime_type,
            "data": data,
        }
        logger.info(f"已注册MCP资源: {uri}")

    def get_tool_names(self) -> List[str]:
        """获取所有已注册工具名"""
        return [s["name"] for s in self._tool_schemas]

    def get_server_info(self) -> Dict[str, Any]:
        """获取服务端信息"""
        return {
            "name": "SCU3-mcp-server",
            "version": "1.0",
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "toolsCount": len(self._tool_schemas),
            "resourcesCount": len(self._resources),
            "initialized": self._initialized,
        }


# 绑定方法处理器到路由表
MCPServer._METHOD_HANDLERS = {
    "initialize": MCPServer._handle_initialize,
    "ping": MCPServer._handle_ping,
    "shutdown": MCPServer._handle_shutdown,
    "tools/list": MCPServer._handle_tools_list,
    "tools/call": MCPServer._handle_tools_call,
    "tools/capability": MCPServer._handle_tools_capability,
    "resources/list": MCPServer._handle_resources_list,
    "resources/read": MCPServer._handle_resources_read,
    "resources/subscribe": MCPServer._handle_resources_subscribe,
    "resources/unsubscribe": MCPServer._handle_resources_unsubscribe,
}


# ─── MCP注册表 ────────────────────────────────────

class MCPRegistry(PersistableMixin, StatusableMixin):
    """MCP注册表

    管理多个MCP服务器连接，提供统一工具路由：
      - 本地工具优先
      - 远程工具兜底
    支持服务器健康检查与自动故障转移。

    用法:
        registry = get_mcp_registry()
        registry.connect_remote("search", "https://mcp.search.com/rpc", "key")
        result = registry.route_call("calculator", {"expression": "1+1"})
    """

    def __init__(self):
        self._server: MCPServer = MCPServer()
        self._clients: Dict[str, MCPClient] = {}  # name -> client
        self._tool_location: Dict[str, str] = {}  # tool_name -> "local" | client_name
        self._state_lock = threading.Lock()
        self._health_thread: Optional[threading.Thread] = None
        self._health_running: bool = False
        # 加载持久化状态
        self._load_state()

    # ─── 本地工具 ────────────────────────────────────

    def register_local_tools(self) -> int:
        """注册SCU3本地工具到MCP服务端"""
        count = self._server.register_local_tools()
        # 更新工具路由表：本地工具优先
        for schema in self._server._tool_schemas:
            self._tool_location[schema["name"]] = "local"
        logger.info(f"Registry: 已注册{count}个本地工具")
        self._save_state()
        return count

    def get_local_tools(self) -> List[Dict[str, Any]]:
        """获取本地工具Schema列表"""
        return self._server._tool_schemas

    # ─── 远程服务器管理 ────────────────────────────────────

    def connect_remote(self, name: str, server_url: str,
                       api_key: Optional[str] = None) -> bool:
        """连接远程MCP服务器

        Args:
            name: 连接名称（唯一标识）
            server_url: MCP服务器URL
            api_key: 认证密钥

        Returns:
            是否连接成功
        """
        with self._state_lock:
            # 若已存在同名连接，先断开
            if name in self._clients:
                self._clients[name].disconnect()
            client = MCPClient(name=name)
            if client.connect(server_url, api_key):
                self._clients[name] = client
                # 更新工具路由表：远程工具（不覆盖本地）
                for tool in client._tools_cache:
                    tname = tool.get("name", "")
                    if tname and tname not in self._tool_location:
                        self._tool_location[tname] = name
                logger.info(f"Registry: 已连接远程服务器 '{name}'，"
                            f"新增{len(client._tools_cache)}个远程工具")
                self._save_state()
                return True
            else:
                logger.warning(f"Registry: 连接远程服务器 '{name}' 失败")
                return False

    def disconnect_remote(self, name: str) -> None:
        """断开远程MCP服务器连接"""
        with self._state_lock:
            if name in self._clients:
                self._clients[name].disconnect()
                del self._clients[name]
                # 清理该服务器的工具路由
                self._tool_location = {t: loc for t, loc in self._tool_location.items()
                                       if loc != name}
                logger.info(f"Registry: 已断开远程服务器 '{name}'")
                self._save_state()

    # ─── 工具路由 ────────────────────────────────────

    def route_call(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """路由工具调用（本地优先，远程兜底）

        Args:
            tool_name: 工具名称
            params: 工具参数

        Returns:
            工具执行结果
        """
        location = self._tool_location.get(tool_name)

        # 本地工具优先
        if location == "local":
            return self._call_local(tool_name, params)

        # 远程工具兜底
        if location and location in self._clients:
            client = self._clients[location]
            result = client.call_tool(tool_name, params)
            if result.get("success"):
                return result
            # 远程调用失败，尝试故障转移
            logger.warning(f"Registry: 远程工具 '{tool_name}' 调用失败，"
                          f"尝试故障转移")
            failover_result = self._try_failover(tool_name, params)
            if failover_result is not None:
                return failover_result
            return result

        # 未知工具：尝试在所有远程服务器中查找
        logger.info(f"Registry: 工具 '{tool_name}' 路由未知，全量搜索远程服务器")
        for cname, client in self._clients.items():
            if not client.connected:
                continue
            tools = client.list_tools()
            for t in tools:
                if t.get("name") == tool_name:
                    self._tool_location[tool_name] = cname
                    self._save_state()
                    return client.call_tool(tool_name, params)

        return {"success": False, "tool": tool_name,
                "error": f"工具 '{tool_name}' 不存在于本地或任何远程服务器"}

    def list_all_tools(self) -> List[Dict[str, Any]]:
        """列出所有可用工具（本地+远程）"""
        all_tools = list(self._server._tool_schemas)
        seen = {s["name"] for s in all_tools}
        for client in self._clients.values():
            if client.connected:
                for tool in client._tools_cache:
                    if tool.get("name") not in seen:
                        all_tools.append(tool)
                        seen.add(tool.get("name", ""))
        return all_tools

    def _call_local(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """调用本地工具"""
        # 通过MCPServer的tools/call处理
        mcp_result = self._server._handle_tools_call({"name": tool_name, "arguments": params})
        raw = mcp_result.get("_raw", {})
        if isinstance(raw, dict):
            return raw
        return {"success": not mcp_result.get("isError", False),
                "tool": tool_name, "result": mcp_result.get("content", [])}

    def _try_failover(self, tool_name: str, params: Dict[str, Any]) -> Optional[Dict]:
        """故障转移：在其他远程服务器中查找同名工具"""
        for cname, client in self._clients.items():
            if not client.connected:
                continue
            # 跳过已失败的服务器
            if self._tool_location.get(tool_name) == cname:
                continue
            tools = client.list_tools()
            for t in tools:
                if t.get("name") == tool_name:
                    logger.info(f"Registry: 故障转移至 '{cname}' 调用 '{tool_name}'")
                    self._tool_location[tool_name] = cname
                    self._save_state()
                    return client.call_tool(tool_name, params)
        return None

    # ─── 健康检查 ────────────────────────────────────

    def start_health_check(self, interval: float = 60.0) -> None:
        """启动后台健康检查线程

        Args:
            interval: 检查间隔（秒）
        """
        if self._health_running:
            return
        self._health_running = True

        def _check_loop():
            while self._health_running:
                try:
                    self._check_all_servers()
                except Exception as e:
                    logger.warning(f"健康检查异常: {e}")
                time.sleep(interval)

        self._health_thread = threading.Thread(target=_check_loop, daemon=True,
                                               name="mcp-health-check")
        self._health_thread.start()
        logger.info(f"Registry: 健康检查已启动（间隔{interval}s）")

    def stop_health_check(self) -> None:
        """停止健康检查"""
        self._health_running = False
        if self._health_thread:
            self._health_thread.join(timeout=5.0)
            self._health_thread = None
        logger.info("Registry: 健康检查已停止")

    def _check_all_servers(self) -> None:
        """检查所有远程服务器健康状态"""
        dead_clients = []
        for name, client in self._clients.items():
            if not client.connected:
                continue
            if not client.ping():
                logger.warning(f"Registry: 服务器 '{name}' 健康检查失败")
                # 尝试重连
                if client._auto_reconnect:
                    if not client._reconnect():
                        dead_clients.append(name)
        # 清理彻底失联的服务器
        for name in dead_clients:
            logger.warning(f"Registry: 服务器 '{name}' 彻底失联，从注册表移除")
            self.disconnect_remote(name)

    # ─── 状态查询 ────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """获取注册表整体状态"""
        return {
            "local_tools": len(self._server._tool_schemas),
            "remote_servers": {
                name: client.get_status() for name, client in self._clients.items()
            },
            "tool_routing": dict(self._tool_location),
            "health_check_running": self._health_running,
        }

    def get_server(self) -> MCPServer:
        """获取本地MCPServer实例"""
        return self._server

    # ─── 状态持久化（PersistableMixin 接口实现）────────────

    def _state_path(self) -> str:
        return STATE_FILE

    def _serialize_state(self) -> dict:
        """序列化 MCP 状态"""
        state = {
            "remote_servers": [],
            "tool_routing": {
                t: loc for t, loc in self._tool_location.items() if loc == "local"
            },
            "saved_at": time.time(),
        }
        for name, client in self._clients.items():
            if client.connected:
                state["remote_servers"].append({
                    "name": name,
                    "server_url": client.server_url,
                    "connected": client.connected,
                    "tools_count": len(client._tools_cache),
                })
        return state

    def _deserialize_state(self, state: dict) -> None:
        """从状态恢复（远程连接需手动重建，只恢复本地工具路由）"""
        for tool, loc in state.get("tool_routing", {}).items():
            if loc == "local":
                self._tool_location[tool] = loc
        logger.info(f"Registry: 已加载MCP状态，"
                    f"本地工具路由{sum(1 for v in self._tool_location.values() if v == 'local')}个")


# ─── 单例 ────────────────────────────────────

_registry_instance: Optional[MCPRegistry] = None
_registry_lock = threading.Lock()


def get_mcp_registry() -> MCPRegistry:
    """获取MCP注册表单例

    首次调用时自动注册本地工具。
    """
    global _registry_instance
    if _registry_instance is None:
        with _registry_lock:
            if _registry_instance is None:
                _registry_instance = MCPRegistry()
                _registry_instance.register_local_tools()
    return _registry_instance


def get_mcp_server() -> MCPServer:
    """获取MCPServer实例（便捷方法）"""
    return get_mcp_registry().get_server()
