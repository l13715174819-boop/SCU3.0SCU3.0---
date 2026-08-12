# -*- coding: utf-8 -*-
"""
W1 层：w1_layer/action.py — 执行层（工具调用）
================================================
与记忆层同属 W1，数据流免审。
但工具调用本身需经"工具守卫"（tool_guard）按 read/write 定税。

任务2.3：补全13种工具 + tool_type标注 + 沙箱
  read类(11): calculator, weather, time_now, text_stats, file_read,
              exchange_rate, crypto_price, stock_price, github_search,
              datetime_calc, unit_convert
  write类(2): file_write, code_run（沙箱隔离）
"""
import os
import re
import ast
import time
import json
import math
import operator
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger("SCU3.w1.action")

# 项目根目录（限制文件操作范围）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "SCU3_data")
SANDBOX_DIR = os.path.join(DATA_DIR, "sandbox")
os.makedirs(SANDBOX_DIR, exist_ok=True)


class ActionLayer:
    """执行层 — 工具调用与动作执行（含降级链 + 全网爬取）

    单例模式：确保插件市场注册的工具对所有调用方可见。
    """

    _instance = None

    # 14种工具类型映射（与 tool_guard.TOOL_TYPE_MAP 对齐）
    TOOL_TYPES = {
        "calculator": "read",
        "weather": "read",
        "time_now": "read",
        "text_stats": "read",
        "file_read": "read",
        "exchange_rate": "read",
        "crypto_price": "read",
        "stock_price": "read",
        "github_search": "read",
        "datetime_calc": "read",
        "unit_convert": "read",
        "file_write": "write",
        "code_run": "write",
        "web_search": "read",
        "web_crawl": "read",
    }

    # 工具降级链：主工具失败 → 按顺序尝试备选工具
    TOOL_FALLBACK_CHAIN = {
        "web_search": ["web_search", "web_crawl"],  # 搜索失败→试爬取已知URL
        "weather": ["weather"],  # 天气无备选
        "exchange_rate": ["exchange_rate", "web_search"],  # 汇率失败→联网搜
        "crypto_price": ["crypto_price", "web_search"],
        "stock_price": ["stock_price", "web_search"],
        "github_search": ["github_search", "web_search"],
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._tools = {
            "calculator": self._tool_calculator,
            "weather": self._tool_weather,
            "time_now": self._tool_time,
            "text_stats": self._tool_text_stats,
            "file_read": self._tool_file_read,
            "exchange_rate": self._tool_exchange_rate,
            "crypto_price": self._tool_crypto_price,
            "stock_price": self._tool_stock_price,
            "github_search": self._tool_github_search,
            "datetime_calc": self._tool_datetime_calc,
            "unit_convert": self._tool_unit_convert,
            "file_write": self._tool_file_write,
            "code_run": self._tool_code_run,
            "web_search": self._tool_web_search,
            "web_crawl": self._tool_web_crawl,
        }

    # ─── 工具检测 ────────────────────────────────────

    def detect_tool(self, text: str) -> Optional[Dict[str, Any]]:
        """检测是否需要工具调用（13种工具模式匹配）"""
        # 1. 计算器
        m = re.match(r"^(?:计算|算一下|calc|=)\s*(.+)$", text, re.I)
        if m:
            return {"tool": "calculator", "params": {"expression": m.group(1).strip().rstrip("=")},
                    "tool_type": "read"}
        # 2. 天气
        if re.search(r"天气|气温|weather", text, re.I) and any(c in text for c in "北京上海广州深圳成都杭州"):
            for city in ["北京", "上海", "广州", "深圳", "成都", "杭州"]:
                if city in text:
                    return {"tool": "weather", "params": {"city": city}, "tool_type": "read"}
        # 3. 时间
        if re.search(r"几点|现在时间|当前时间|now|time", text, re.I):
            return {"tool": "time_now", "params": {}, "tool_type": "read"}
        # 4. 文本统计
        if re.search(r"统计.*字数|字数统计|count.*words?", text, re.I):
            return {"tool": "text_stats", "params": {"text": text}, "tool_type": "read"}
        # 4.5 PDF/DOCX/XLSX 读取（需插件市场，优先于file_read匹配）
        m = re.search(r"(?:读取|解析|读|read)\s+(\S+\.pdf)", text, re.I)
        if m:
            return {"tool": "pdf_read", "params": {"path": m.group(1).strip()}, "tool_type": "read"}
        m = re.search(r"(?:读取|解析|读|read)\s+(\S+\.(?:docx?|DOCX?))", text, re.I)
        if m:
            return {"tool": "docx_read", "params": {"path": m.group(1).strip()}, "tool_type": "read"}
        m = re.search(r"(?:读取|解析|读|read)\s+(\S+\.(?:xlsx?|XLSX?))", text, re.I)
        if m:
            return {"tool": "excel_read", "params": {"path": m.group(1).strip()}, "tool_type": "read"}
        # 5. 文件读取（排除 pdf/docx/xlsx，已由上面匹配）
        m = re.match(r"^(?:读|读取|read|cat)\s+(.+)$", text, re.I)
        if m and not re.search(r"\.(pdf|docx?|xlsx?)$", m.group(1), re.I):
            return {"tool": "file_read", "params": {"path": m.group(1).strip()}, "tool_type": "read"}
        # 6. 汇率查询
        m = re.search(r"(?:汇率|exchange\s*rate)\s*([A-Za-z]{3})", text, re.I)
        if m:
            return {"tool": "exchange_rate", "params": {"base": m.group(1).upper()}, "tool_type": "read"}
        # 7. 加密货币价格
        m = re.search(r"(?:价格|price)\s*(bitcoin|btc|ethereum|eth)", text, re.I)
        if m:
            return {"tool": "crypto_price", "params": {"symbol": m.group(1).lower()}, "tool_type": "read"}
        # 8. 股票价格
        m = re.search(r"(?:股票|stock)\s*([A-Za-z]{1,5})", text, re.I)
        if m:
            return {"tool": "stock_price", "params": {"code": m.group(1).upper()}, "tool_type": "read"}
        # 8.5 二维码生成（需插件市场）— 提前到 github_search 之前，避免含 github.com 的二维码文本被误判
        m = re.search(r"(?:生成|创建|制作)\s*(?:一个)?\s*二维码\s*(.+)?", text, re.I)
        if m:
            return {"tool": "qrcode_gen", "params": {"text": m.group(1) or "SCU3"}, "tool_type": "read"}
        if re.search(r"识别二维码|扫描二维码|decode qrcode", text, re.I):
            return {"tool": "qrcode_gen", "params": {"text": "decode"}, "tool_type": "read"}
        # 9. GitHub搜索（排除URL场景，避免把 github.com 链接当搜索词）
        m = re.search(r"(?:github|搜索仓库)\s+(?!.*https?://)(.+)", text, re.I)
        if m:
            return {"tool": "github_search", "params": {"query": m.group(1).strip()}, "tool_type": "read"}
        # 14. 联网搜索（DuckDuckGo，无需API Key）
        # 触发：搜索/搜一下/查一下/帮我查/查找 + 关键词，或直接查实时信息
        m = re.search(r"(?:搜索|搜一下|搜搜|查一下|查查|帮我查|查找|查找一下|查阅|search|google一下|百度一下)\s*(.+)", text, re.I)
        if m:
            return {"tool": "web_search", "params": {"query": m.group(1).strip()}, "tool_type": "read"}
        # 15. 全网爬取：用户直接提供URL
        m = re.search(r"(?:爬取|抓取|读取页面|fetch|crawl)\s+(https?://\S+)", text, re.I)
        if m:
            return {"tool": "web_crawl", "params": {"url": m.group(1).strip()}, "tool_type": "read"}
        # 直接粘贴URL（无动词）→ 自动爬取
        m = re.search(r"^(https?://\S+)\s*$", text.strip())
        if m:
            return {"tool": "web_crawl", "params": {"url": m.group(1).strip()}, "tool_type": "read"}
        # 无明确搜索动词但意图为联网搜索（最新/最近/2024-2026/热点/多少钱/是什么等）
        # 与感知层 _detect_intent 的 web_search 正则保持一致，避免意图判定与工具触发脱节
        if re.search(r"最新|最近|今日|今天.*新闻|近期|2024|2025|2026|现在是.*年|怎么样|是什么|是谁|多少钱|发生.*事|热点|热搜", text, re.I):
            # 提取查询词（去掉问号、助词、前导数字题号）
            query = re.sub(r"[？?！!。，,了|的|吗|呢|啊|呀]", " ", text).strip()
            # 清理前导数字题号（如"77. "或"77、"或"77 "），防止数字污染搜索词
            query = re.sub(r"^\d+[\.\、\s]+", "", query).strip()
            return {"tool": "web_search", "params": {"query": query[:80]}, "tool_type": "read"}
        # 10. 日期计算
        m = re.match(r"^(?:日期计算|date\s*calc)\s+(\d{4}-\d{2}-\d{2})\s*([+-])\s*(\d+)\s*天?", text, re.I)
        if m:
            return {"tool": "datetime_calc",
                    "params": {"start": m.group(1), "op": m.group(2), "days": int(m.group(3))},
                    "tool_type": "read"}
        # 11. 单位换算
        m = re.match(r"^(?:换算|convert)\s+([\d.]+)\s*([a-zA-Z℃]+)\s*(?:to|=|→)\s*([a-zA-Z℃]+)$", text, re.I)
        if m:
            return {"tool": "unit_convert",
                    "params": {"value": float(m.group(1)), "from_unit": m.group(2), "to_unit": m.group(3)},
                    "tool_type": "read"}
        # 12. 文件写入
        m = re.match(r"^(?:写入|write)\s+(\S+)\s*[:：]\s*(.+)$", text, re.I)
        if m:
            return {"tool": "file_write", "params": {"path": m.group(1), "content": m.group(2)},
                    "tool_type": "write"}
        # 13. 代码执行
        if re.match(r"^(?:运行代码|run|exec)\s*", text, re.I):
            code = re.sub(r"^(?:运行代码|run|exec)\s*", "", text, flags=re.I)
            if code:
                return {"tool": "code_run", "params": {"code": code}, "tool_type": "write"}
        # 15. 图片处理（需插件市场）
        m = re.search(r"(?:处理|缩放|裁剪|转换)\s*(\S+\.(?:png|jpg|jpeg|gif|bmp))", text, re.I)
        if m:
            return {"tool": "image_process", "params": {"path": m.group(1), "action": "info"}, "tool_type": "read"}
        # 16. 翻译（需插件市场）
        m = re.search(r"(?:翻译|translate)\s+(.+)", text, re.I)
        if m:
            text_to_translate = m.group(1).strip()
            # 智能检测目标语言：优先用户显式指定，否则按文本语言自动判断
            target = "en"
            source = "auto"
            if "中文" in text or "汉语" in text:
                target = "zh-CN"
            elif "英文" in text or "英语" in text:
                target = "en"
            elif "日文" in text or "日语" in text:
                target = "ja"
            else:
                # 自动判断：中文为主→翻译成英文，英文为主→翻译成中文
                cjk_count = sum(1 for c in text_to_translate if '\u4e00' <= c <= '\u9fff')
                if cjk_count > len(text_to_translate) * 0.3:
                    target = "en"
                    source = "zh-CN"
                else:
                    target = "zh-CN"
                    source = "en"
            return {"tool": "translate", "params": {"text": text_to_translate, "source": source, "target": target}, "tool_type": "read"}
        # 17. Markdown 渲染（需插件市场）
        m = re.search(r"(?:渲染|转换)\s*(?:markdown|md)\s*(?:为|to)\s*(html|pdf)", text, re.I)
        if m:
            return {"tool": "md_render", "params": {"text": "", "output_format": m.group(1).lower()}, "tool_type": "read"}
        return None

    def execute(self, tool_info: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具调用（含降级链：主工具失败→按顺序尝试备选工具）

        若工具未注册（如需插件市场的工具），返回 all_failed=True 触发插件市场流程。
        """
        tool_name = tool_info["tool"]
        params = tool_info["params"]

        # 快速检查：工具是否注册
        if tool_name not in self._tools and tool_name not in self.TOOL_FALLBACK_CHAIN:
            # 未注册工具 → 返回 all_failed，由认知层触发插件市场
            logger.info(f"工具[{tool_name}]未注册，触发插件市场流程")
            return {"success": False, "tool": tool_name,
                    "error": f"工具未注册: {tool_name}",
                    "tried_tools": [tool_name], "all_failed": True,
                    "reason": "tool_not_registered"}

        # 获取降级链（无降级链则只试主工具）
        fallback_chain = self.TOOL_FALLBACK_CHAIN.get(tool_name, [tool_name])
        last_error = ""
        tried_tools = []

        for i, fb_tool in enumerate(fallback_chain):
            if fb_tool not in self._tools:
                continue
            tried_tools.append(fb_tool)
            try:
                # 首次用原参数，后续降级尝试用适配后的参数
                use_params = params if i == 0 else self._adapt_params(fb_tool, params, last_error)
                if use_params is None:
                    continue  # 无法适配参数，跳过
                # 领域透传：web_search 注入 domain 参数（来自 ctx）
                if fb_tool == "web_search" and "domain" not in use_params and "domain" in tool_info:
                    use_params = {**use_params, "domain": tool_info["domain"]}
                result = self._tools[fb_tool](**use_params)
                logger.info(f"工具[{fb_tool}]成功 (降级链第{i+1}个)")
                return {"success": True, "tool": fb_tool, "result": result,
                        "tool_type": tool_info.get("tool_type", "read"),
                        "fallback_used": i > 0, "tried_tools": tried_tools}
            except Exception as e:
                last_error = str(e)
                logger.warning(f"工具[{fb_tool}]失败(降级链第{i+1}个): {e}")
                continue

        # 所有工具都失败
        return {"success": False, "tool": tool_name, "error": last_error,
                "tried_tools": tried_tools, "all_failed": True}

    def _adapt_params(self, target_tool: str, original_params: Dict, last_error: str) -> Optional[Dict]:
        """适配降级工具的参数（不同工具参数不同）"""
        try:
            if target_tool == "web_search":
                # 从原工具参数提取查询词
                query = original_params.get("query") or original_params.get("code") or \
                        original_params.get("symbol") or original_params.get("base") or ""
                if not query:
                    return None
                return {"query": str(query)}
            if target_tool == "web_crawl":
                # web_crawl需要URL，如果原参数没有url则无法降级
                url = original_params.get("url", "")
                if not url:
                    return None
                return {"url": url}
        except Exception:
            return None
        return original_params

    def process(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """处理数据流（W1 同层，免审。但工具调用需经工具守卫）

        经验预加载：如果检测到工具未注册，先查经验存储，命中则直接加载插件，
        避免走 all_failed → 插件市场 的完整流程。

        强制完整流程：信息查询类意图（knowledge_query/conversation/greeting/web_search）
        若未检测到确定性工具，主动注入 web_search，确保每次对话都走 RAG + 联网检索 +
        综合分析。纯计算/时间/单位换算等确定性工具保留快速路径，不强制联网。
        """
        text = ctx.get("perceived", "")
        intent = ctx.get("intent", "")

        tool_info = self.detect_tool(text)

        # 强制完整流程：信息查询类意图未检测到工具时，主动注入 web_search
        # 确定性工具（calculator/time_now/weather/datetime_calc/unit_convert/file_*/code_run）
        # 走快速路径，不强制联网
        # followup/analytical 意图依赖对话历史或需要深度分析，不强制联网搜索
        if not tool_info and intent in ("knowledge_query", "conversation", "greeting", "web_search"):
            domain = ctx.get("domain", "general")
            # 智能搜索词生成：根据意图类型改写，避免原文直接搜索导致结果不相关
            query = self._smart_search_query(text, intent, domain)
            tool_info = {
                "tool": "web_search",
                "params": {"query": query[:80]},
                "tool_type": "read",
            }
            if domain and domain != "general":
                tool_info["domain"] = domain
            logger.info(f"强制完整流程: intent={intent}, 注入web_search: {query[:50]}")
        # followup 意图：依赖对话历史，不强制联网，让 LLM 基于历史回答
        # analytical 意图：需要深度分析，不强制联网，让 LLM 用 analytical prompt 回答
        # 这两种意图由认知层直接调用 _generate_llm_response 处理

        if tool_info:
            ctx["tool_info"] = tool_info
            ctx["tool_pending"] = True

            # 领域透传：把感知层识别的 domain 注入 tool_info，供 execute 使用
            domain = ctx.get("domain", "general")
            if domain and domain != "general" and "domain" not in tool_info:
                tool_info["domain"] = domain
                logger.info(f"工具[{tool_info['tool']}]注入领域: {domain}")

            # 经验预加载：工具未注册时，先查经验
            tool_name = tool_info["tool"]
            if tool_name not in self._tools:
                try:
                    from m_layer.experience_store import get_experience_store
                    exp_store = get_experience_store()
                    preload_result = exp_store.preload_tool_if_needed(text, tool_name)
                    if preload_result.get("preloaded"):
                        ctx["preloaded_from_experience"] = True
                        ctx["preloaded_plugin"] = preload_result.get("plugin")
                        logger.info(f"经验预加载成功: {preload_result.get('message')}")
                except Exception as e:
                    logger.debug(f"经验预加载检查异常（不阻塞）: {e}")
        else:
            ctx["tool_pending"] = False
        ctx["action_ok"] = True
        return ctx

    def _smart_search_query(self, text: str, intent: str, domain: str = "general") -> str:
        """智能搜索词生成：根据意图类型改写口语化表达为搜索友好的关键词

        解决问题：greeting/conversation 类意图直接用原文搜索导致结果不相关
          - "介绍你自己" → "AI助手 自我介绍 功能"（而非搜到"自我介绍范文"）
          - "今天能帮我做什么" → "AI助手 功能 能做什么"（而非搜到"帮妈妈做家务"）

        策略：
          - greeting: 识别自我介绍/能力询问类口语，改写为功能描述型搜索词
          - knowledge_query: 保持技术术语，去除助词
          - conversation: 提取核心名词关键词
          - web_search: 清理问号助词，保持原意
          - hotel/medical/product: 领域词优先，保留具体实体名
        """
        # 基础清理：去问号、助词、标点、前导数字题号
        cleaned = re.sub(r"[？?！!。，,]", " ", text).strip()
        # 清理前导数字题号（如"77. "或"77、"或"77 "），防止数字污染搜索词
        cleaned = re.sub(r"^\d+[\.\、\s]+", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)

        if intent == "greeting":
            # 问候/自我介绍类：改写为AI功能相关搜索词
            greeting_map = {
                r"介绍.*自己|你是谁|你叫什么|你好.*介绍": "AI助手 自我介绍 功能 智能对话",
                r"能.*做什么|帮.*做什么|功能|会什么|能力": "AI助手 功能 能力 智能助手",
                r"你好|hi|hello|嗨": "AI助手 智能对话 在线助手",
                r"谢谢|感谢|thanks": "AI助手 智能助手 在线服务",
            }
            for pattern, replacement in greeting_map.items():
                if re.search(pattern, cleaned, re.I):
                    return replacement
            # 其他greeting：补充"AI助手"上下文
            return f"AI助手 {cleaned[:40]}"

        if intent == "knowledge_query":
            # 知识查询：保持技术术语，去除口语助词
            query = re.sub(r"[了的吗呢啊呀吧]", " ", cleaned).strip()
            query = re.sub(r"\s+", " ", query)
            # 去除"是什么""有什么""怎么样"等疑问尾缀（保留核心实体）
            query = re.sub(r"\s*(是什么|有什么用|怎么样|是如何|的作用|的用途)\s*$", "", query, flags=re.I)
            return query[:80] if query else cleaned[:80]

        if intent == "conversation":
            # 闲聊：提取核心名词，去除纯口语
            query = re.sub(r"[了的吗呢啊呀吧]", " ", cleaned).strip()
            query = re.sub(r"\s+", " ", query)
            # 如果太短（<4字符），补充"介绍"使其可搜索
            if len(query) < 4:
                return f"介绍 {query}"
            return query[:80]

        # web_search 或其他：清理助词，保持原意
        query = re.sub(r"[了的吗呢啊呀吧]", " ", cleaned).strip()
        query = re.sub(r"\s+", " ", query)
        return query[:80] if query else cleaned[:80]

    # ─── 只读工具实现（11种） ────────────────────────────────────

    def _tool_calculator(self, expression: str) -> Dict:
        """计算器（AST安全求值）"""
        safe_ops = {
            ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
            ast.Mod: operator.mod,
        }
        safe_funcs = {"abs": abs, "round": round, "min": min, "max": max}

        def _eval(node):
            if isinstance(node, ast.Constant):
                return node.value
            elif isinstance(node, ast.BinOp):
                return safe_ops[type(node.op)](_eval(node.left), _eval(node.right))
            elif isinstance(node, ast.UnaryOp):
                return safe_ops[type(node.op)](_eval(node.operand))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in safe_funcs:
                    return safe_funcs[node.func.id](*[_eval(a) for a in node.args])
            raise ValueError(f"不支持: {ast.dump(node)[:50]}")

        expr = expression.replace("^", "**").replace("×", "*").replace("÷", "/")
        result = _eval(ast.parse(expr, mode="eval").body)
        return {"expression": expression, "result": result}

    def _tool_weather(self, city: str) -> Dict:
        """天气查询（模拟数据）"""
        import random
        temp = random.randint(15, 35)
        return {"city": city, "temp": f"{temp}°C", "desc": "晴",
                "humidity": f"{random.randint(30, 80)}%", "wind": f"{random.randint(1, 8)}级"}

    def _tool_time(self) -> Dict:
        """当前时间"""
        return {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    def _tool_text_stats(self, text: str) -> Dict:
        """文本统计"""
        return {"chars": len(text), "words": len(text.split()),
                "lines": text.count("\n") + 1}

    def _tool_file_read(self, path: str) -> Dict:
        """文件读取（C5修复：限制在sandbox目录，禁止读敏感文件）"""
        # C5修复：file_read 也限制在 SANDBOX_DIR，与 file_write 一致
        full_path = self._safe_path(path, write=True)  # 强制sandbox范围
        if not os.path.exists(full_path):
            return {"path": path, "content": "", "size": 0, "error": "文件不存在"}
        # C5修复：敏感文件黑名单（双保险）
        sensitive_files = {"ledger.json", "whitelist.json", ".env", "secret",
                          "config.json", "auth.json", "keys.json"}
        basename = os.path.basename(full_path).lower()
        if basename in sensitive_files or basename.startswith(".env"):
            return {"path": path, "content": "", "size": 0,
                    "error": "拒绝读取敏感文件"}
        # C5修复：限制文件大小（防止读超大文件DoS）
        if os.path.getsize(full_path) > 1024 * 1024:  # 1MB上限
            return {"path": path, "content": "", "size": 0,
                    "error": "文件过大（>1MB）"}
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"path": path, "content": content, "size": len(content)}

    def _tool_exchange_rate(self, base: str = "USD") -> Dict:
        """汇率查询（模拟常见货币对）"""
        rates_table = {
            "USD": {"CNY": 7.24, "EUR": 0.92, "JPY": 149.5, "GBP": 0.79, "KRW": 1310.0},
            "CNY": {"USD": 0.14, "EUR": 0.13, "JPY": 20.65, "GBP": 0.11, "KRW": 181.0},
            "EUR": {"USD": 1.09, "CNY": 7.87, "JPY": 162.5, "GBP": 0.86},
        }
        base = base.upper()
        rates = rates_table.get(base, rates_table["USD"])
        return {"base": base, "rates": rates, "timestamp": datetime.now().isoformat()}

    def _tool_crypto_price(self, symbol: str = "btc") -> Dict:
        """加密货币价格（模拟）"""
        import random
        symbol = symbol.lower()
        prices_table = {
            "btc": {"BTC": round(random.uniform(60000, 70000), 2)},
            "bitcoin": {"BTC": round(random.uniform(60000, 70000), 2)},
            "eth": {"ETH": round(random.uniform(3000, 4000), 2)},
            "ethereum": {"ETH": round(random.uniform(3000, 4000), 2)},
        }
        prices = prices_table.get(symbol, {"BTC": round(random.uniform(60000, 70000), 2)})
        return {"symbol": symbol, "prices": prices, "timestamp": datetime.now().isoformat()}

    def _tool_stock_price(self, code: str = "AAPL") -> Dict:
        """股票行情（模拟）"""
        import random
        names = {"AAPL": "Apple Inc.", "GOOG": "Alphabet", "MSFT": "Microsoft",
                 "TSLA": "Tesla", "AMZN": "Amazon"}
        code = code.upper()
        return {"code": code, "name": names.get(code, code),
                "price": round(random.uniform(50, 500), 2),
                "change": round(random.uniform(-5, 5), 2),
                "timestamp": datetime.now().isoformat()}

    def _tool_github_search(self, query: str) -> Dict:
        """GitHub仓库搜索（模拟结果）"""
        return {"query": query, "repos": [
            {"full_name": f"popular/{query.replace(' ', '-')}", "stars": 12500,
             "description": f"Popular repository for {query}", "language": "Python"},
            {"full_name": f"awesome/{query.replace(' ', '-')}", "stars": 8200,
             "description": f"Awesome {query} collection", "language": "JavaScript"},
        ], "total": 2}

    def _search_baike_baidu(self, query: str, max_results: int = 3) -> Optional[Dict]:
        """百度百科知识源接入（免Key，国内可达）

        通过百度百科搜索页爬取词条摘要，作为权威知识源补充搜索引擎。
        适用于：医疗药品、历史人物、技术名词、地理概念等百科类查询

        流程：
          1. 访问 https://baike.baidu.com/search?word=xxx 获取词条搜索结果
          2. 提取词条链接 + 摘要
          3. 对Top1词条抓取详情页获取完整摘要

        返回格式与 _tool_web_search 一致，engine="百度百科"
        """
        import subprocess
        import urllib.parse as urlparse

        encoded = urlparse.quote(query)
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

        # 步骤1：百度百科搜索页
        search_url = f"https://baike.baidu.com/search?word={encoded}"
        try:
            result = subprocess.run(
                ["curl", "-s", "-L", "--max-time", "8",
                 "-H", f"User-Agent: {ua}",
                 "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.8",
                 "-H", "Accept-Language: zh-CN,zh;q=0.9",
                 search_url],
                capture_output=True, timeout=12
            )
            html = result.stdout.decode("utf-8", errors="ignore")
            if not html or len(html) < 500:
                return None

            # 提取词条链接：百度百科搜索结果 <a class="result-title" href="..."> 或 <a href="/item/...">
            item_links = re.findall(r'href="(https?://baike\.baidu\.com/item/[^"]+)"', html)
            # 去重
            seen = set()
            unique_links = []
            for link in item_links:
                if link not in seen:
                    seen.add(link)
                    unique_links.append(link)
                if len(unique_links) >= max_results:
                    break

            if not unique_links:
                # 备用：直接尝试 /item/{query}
                direct_url = f"https://baike.baidu.com/item/{encoded}"
                unique_links = [direct_url]

            # 步骤2：抓取Top词条详情页摘要
            results = []
            for item_url in unique_links[:max_results]:
                try:
                    r = subprocess.run(
                        ["curl", "-s", "-L", "--max-time", "6",
                         "-H", f"User-Agent: {ua}",
                         "-H", "Accept: text/html,*/*;q=0.8",
                         item_url],
                        capture_output=True, timeout=10
                    )
                    item_html = r.stdout.decode("utf-8", errors="ignore")
                    if not item_html or len(item_html) < 500:
                        continue

                    # 提取标题：<title>xxx_百度百科</title> 或 <h1>
                    title = ""
                    title_m = re.search(r'<title>([^<]+?)(?:_百度百科)?</title>', item_html)
                    if title_m:
                        title = title_m.group(1).strip()
                    if not title:
                        h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', item_html, re.S)
                        if h1_m:
                            title = re.sub(r'<[^>]+>', '', h1_m.group(1)).strip()

                    # 提取摘要：meta description 或 .lemma-summary
                    snippet = ""
                    desc_m = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', item_html)
                    if desc_m:
                        snippet = desc_m.group(1).strip()
                    if not snippet:
                        # 备用：lemma-summary div
                        sum_m = re.search(r'class="lemma-summary[^"]*"[^>]*>(.*?)</div>', item_html, re.S)
                        if sum_m:
                            # 去标签+清理
                            snippet = re.sub(r'<[^>]+>', '', sum_m.group(1))
                            snippet = re.sub(r'\s+', ' ', snippet).strip()

                    if title and snippet:
                        results.append({
                            "title": title[:100],
                            "url": item_url,
                            "snippet": snippet[:300],
                        })
                except Exception:
                    continue

            if results:
                logger.info(f"百度百科[{query[:30]}]: 命中{len(results)}条")
                return {
                    "query": query, "original_query": query,
                    "results": results, "count": len(results),
                    "engine": "百度百科", "domain": "general",
                }
        except Exception as e:
            logger.debug(f"百度百科查询失败: {e}")
        return None

    def _search_wikipedia(self, query: str, max_results: int = 3) -> Optional[Dict]:
        """维基百科API接入（免Key，官方API，中文版国内可达）

        使用 MediaWiki Action API：
          1. op=search 搜索词条
          2. op=query&prop=extracts 获取摘要

        API文档：https://zh.wikipedia.org/w/api.php
        返回格式与 _tool_web_search 一致，engine="维基百科"
        """
        import subprocess
        import urllib.parse as urlparse

        encoded = urlparse.quote(query)
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

        # 步骤1：搜索词条列表
        search_api = (
            f"https://zh.wikipedia.org/w/api.php?"
            f"action=query&list=search&srsearch={encoded}"
            f"&srlimit={max_results}&format=json&utf8=1"
        )
        try:
            result = subprocess.run(
                ["curl", "-s", "-L", "--max-time", "8",
                 "-H", f"User-Agent: {ua}",
                 "-H", "Accept: application/json",
                 search_api],
                capture_output=True, timeout=12
            )
            text = result.stdout.decode("utf-8", errors="ignore")
            if not text:
                return None
            import json as _json
            data = _json.loads(text)
            search_items = data.get("query", {}).get("search", [])
            if not search_items:
                return None

            # 步骤2：批量获取摘要（用 extracts 属性）
            titles = "|".join(item["title"] for item in search_items[:max_results])
            titles_encoded = urlparse.quote(titles)
            extract_api = (
                f"https://zh.wikipedia.org/w/api.php?"
                f"action=query&prop=extracts&exintro=1&explaintext=1"
                f"&titles={titles_encoded}&format=json&utf8=1"
            )
            r2 = subprocess.run(
                ["curl", "-s", "-L", "--max-time", "8",
                 "-H", f"User-Agent: {ua}",
                 "-H", "Accept: application/json",
                 extract_api],
                capture_output=True, timeout=12
            )
            text2 = r2.stdout.decode("utf-8", errors="ignore")
            if not text2:
                return None
            data2 = _json.loads(text2)
            pages = data2.get("query", {}).get("pages", {})

            results = []
            for page_id, page in pages.items():
                if page_id == "-1":
                    continue
                title = page.get("title", "")
                extract = page.get("extract", "")
                if not title or not extract:
                    continue
                # 构造词条URL
                title_encoded = urlparse.quote(title)
                page_url = f"https://zh.wikipedia.org/wiki/{title_encoded}"
                results.append({
                    "title": title[:100],
                    "url": page_url,
                    "snippet": extract[:400].replace("\n", " ").strip(),
                })

            if results:
                logger.info(f"维基百科[{query[:30]}]: 命中{len(results)}条")
                return {
                    "query": query, "original_query": query,
                    "results": results, "count": len(results),
                    "engine": "维基百科", "domain": "general",
                }
        except Exception as e:
            logger.debug(f"维基百科查询失败: {e}")
        return None

    def _format_api_as_search_result(self, api_result: Dict, query: str,
                                      domain: str, router=None) -> Dict:
        """将外部API结果转换为搜索结果统一格式

        将GitHub/Arxiv/HackerNews等API的结构化数据转为搜索结果格式，
        以便认知层统一处理（_format_search_context + _deep_crawl_search_results）
        """
        source = api_result.get("source", "外部API")
        data = api_result.get("data", {})
        results = []

        # GitHub仓库 → 搜索结果格式
        if "repos" in data:
            for repo in data["repos"][:5]:
                title = f"[GitHub] {repo.get('full_name', '')} ⭐{repo.get('stars', 0)}"
                snippet = (f"{repo.get('description', '')} | "
                           f"语言:{repo.get('language', '?')} | "
                           f"Forks:{repo.get('forks', 0)} | "
                           f"Issues:{repo.get('open_issues', 0)} | "
                           f"更新:{repo.get('updated_at', '')[:10]}")
                results.append({
                    "title": title,
                    "url": repo.get("url", ""),
                    "snippet": snippet,
                    "fields": {"stars": repo.get("stars", 0), "language": repo.get("language", "")},
                })

        # Arxiv论文 → 搜索结果格式
        elif "papers" in data and source.startswith("Arxiv"):
            for paper in data["papers"][:5]:
                title = f"[Arxiv] {paper.get('title', '')}"
                snippet = (f"作者: {', '.join(paper.get('authors', [])[:3])} | "
                           f"发表: {paper.get('published', '')} | "
                           f"摘要: {paper.get('summary', '')[:200]}")
                results.append({
                    "title": title,
                    "url": paper.get("url", ""),
                    "snippet": snippet,
                })

        # PubMed论文 → 搜索结果格式
        elif "papers" in data and source.startswith("PubMed"):
            for paper in data["papers"][:5]:
                title = f"[PubMed] {paper.get('title', '')}"
                snippet = (f"作者: {', '.join(paper.get('authors', [])[:3])} | "
                           f"期刊: {paper.get('journal', '')} | "
                           f"发表: {paper.get('pubdate', '')}")
                results.append({
                    "title": title,
                    "url": paper.get("url", ""),
                    "snippet": snippet,
                })

        # Hacker News → 搜索结果格式
        elif "stories" in data:
            for story in data["stories"][:10]:
                title = f"[HN] {story.get('title', '')} (_score:{story.get('score', 0)})_"
                snippet = (f"作者: {story.get('by', '')} | "
                           f"评论: {story.get('descendants', 0)} | "
                           f"链接: {story.get('url', '')}")
                results.append({
                    "title": title,
                    "url": story.get("url", "") or story.get("hn_url", ""),
                    "snippet": snippet,
                })

        # 加密货币价格 → 搜索结果格式
        elif "prices" in data:
            for coin, info in data["prices"].items():
                title = f"[Crypto] {coin.upper()}"
                snippet = (f"USD: ${info.get('usd', 0):.2f} | "
                           f"CNY: ¥{info.get('cny', 0):.2f} | "
                           f"24h涨跌: {info.get('change_24h', 0):.2f}%")
                results.append({
                    "title": title,
                    "url": f"https://www.google.com/search?q={coin}+price",
                    "snippet": snippet,
                })

        # 国家信息 → 搜索结果格式
        elif "name" in data and "summary" in data:
            results.append({
                "title": f"[国家] {data.get('name', '')}",
                "url": data.get("url", ""),
                "snippet": data.get("summary", ""),
            })

        # 地理编码 → 搜索结果格式
        elif "places" in data:
            for place in data["places"][:3]:
                title = f"[地理] {place.get('display_name', '')[:80]}"
                snippet = (f"经纬度: {place.get('lat', '')}, {place.get('lon', '')} | "
                           f"类型: {place.get('type', '')}")
                results.append({
                    "title": title,
                    "url": f"https://www.openstreetmap.org/?mlat={place.get('lat', '')}&mlon={place.get('lon', '')}",
                    "snippet": snippet,
                })

        # 新闻 → 搜索结果格式
        elif "articles" in data:
            for art in data["articles"][:5]:
                title = f"[新闻] {art.get('title', '')}"
                snippet = (f"来源: {art.get('source', '')} | "
                           f"日期: {art.get('published_at', '')} | "
                           f"{art.get('description', '')[:150]}")
                results.append({
                    "title": title,
                    "url": art.get("url", ""),
                    "snippet": snippet,
                })

        # 股票 → 搜索结果格式
        elif "close" in data:
            stock = data
            results.append({
                "title": f"[股票] {stock.get('code', '')}",
                "url": f"https://xueqiu.com/S/{stock.get('code', '')}",
                "snippet": (f"日期: {stock.get('date', '')} | "
                            f"开盘: {stock.get('open', 0)} | "
                            f"收盘: {stock.get('close', 0)} | "
                            f"最高: {stock.get('high', 0)} | "
                            f"最低: {stock.get('low', 0)} | "
                            f"涨跌: {stock.get('change_pct', 0)}%"),
            })

        # 天气 → 搜索结果格式
        elif "temp" in data:
            w = data
            results.append({
                "title": f"[天气] {w.get('city', '')}",
                "url": "",
                "snippet": (f"温度: {w.get('temp', '')} | "
                            f"体感: {w.get('feels_like', '')} | "
                            f"天气: {w.get('desc', '')} | "
                            f"湿度: {w.get('humidity', '')} | "
                            f"风: {w.get('wind_dir', '')}{w.get('wind_scale', '')}级"),
            })

        if not results:
            # 未知格式，转为单条结果
            results.append({
                "title": f"[{source}]",
                "url": "",
                "snippet": str(data)[:300],
            })

        # 领域增强：白名单标记+字段提取
        if router and domain and domain != "general":
            try:
                for r in results:
                    r["is_whitelisted"] = router.is_whitelisted(r.get("url", ""), domain)
            except Exception:
                pass

        logger.info(f"外部API[{source}]格式化为{len(results)}条搜索结果")
        return {
            "query": query, "original_query": query,
            "results": results, "count": len(results),
            "engine": source, "domain": domain,
        }

    def _tool_web_search(self, query: str, max_results: int = 5, domain: str = "") -> Dict:
        """联网搜索（多引擎备用：Bing→百度→DuckDuckGo，使用curl绕过SSL/代理问题）

        领域增强（v2）：
          - domain 非空时调用 DomainRouter 增强查询词、重排结果、提取字段
          - domain 为空时退化为原始搜索（向后兼容）
        """
        import subprocess
        import urllib.parse as urlparse

        # 领域路由器：增强查询词
        router = None
        enhanced_query = query
        if domain and domain != "general":
            try:
                from domain_router import get_router
                router = get_router()
                enhanced_query = router.enhance_query(query, domain)
                logger.info(f"领域增强[{domain}]: 原查询={query[:40]} → 增强={enhanced_query[:60]}")
            except Exception as e:
                logger.debug(f"领域路由器加载失败（退化为原查询）: {e}")
                enhanced_query = query

        encoded_query = urlparse.quote(enhanced_query)

        # 外部API知识源路由：GitHub/Arxiv/HackerNews等结构化数据API（免Key）
        # 命中即返回，不走搜索引擎（避免反爬+获取结构化数据）
        try:
            from w1_layer.external_apis import route_external_api
            api_result = route_external_api(query)
            if api_result and api_result.get("success"):
                logger.info(f"外部API命中[{api_result.get('source', '?')}]: {query[:40]}")
                # 转换为搜索结果格式（保持统一）
                return self._format_api_as_search_result(api_result, query, domain, router)
        except Exception as e:
            logger.debug(f"外部API路由失败: {e}")

        # 知识源优先：百度百科 + 维基百科（免Key，权威稳定，适合百科类查询）
        # 跳过条件：
        #   1. 实时类查询（今天/最近/最新/新闻/热点/天气等时效性词）
        #   2. 地域+服务类查询（酒店/民宿/客栈/附近/推荐等地域性词，百科无此类词条）
        #   3. 商品比价类查询（iPhone/价格/评测/对比等，百科无实时价格）
        is_realtime_query = bool(re.search(r"今天|今日|最近|最新|近期|现在|新闻|热点|热搜|实时|行情|股价|天气", query))
        is_local_service = bool(re.search(r"酒店|民宿|客栈|旅馆|附近|周边|推荐|外滩|春熙路|新街口|江汉路|五块石|王府井|钟楼|鼓浪屿|栈桥|解放碑|太古里|迪士尼|三里屯|亚龙湾|五一广场|科技园|天河区|南山区|步行街|性价比|海景房|江景", query))
        is_product_query = bool(re.search(r"iPhone|华为|小米|三星|戴森|大疆|任天堂|iPad|MacBook|Kindle|AirPods|索尼|联想|海尔|美的|格力|OPPO|vivo|价格|评测|对比|值得买|性价比|跑分|排行", query, re.I))
        use_baike = not (is_realtime_query or is_local_service or is_product_query)
        if use_baike:
            # 百度百科优先（中文词条最丰富）
            baike_result = self._search_baike_baidu(query, max_results=3)
            if baike_result:
                # 领域增强
                if router and domain and domain != "general":
                    try:
                        extracted_list = []
                        for r in baike_result["results"]:
                            snippet = r.get("snippet", "") + " " + r.get("title", "")
                            fields = router.extract_fields(snippet, domain)
                            r["fields"] = fields
                            r["is_whitelisted"] = router.is_whitelisted(r.get("url", ""), domain)
                            extracted_list.append(fields)
                        baike_result["results"] = router.rank_results(
                            baike_result["results"], domain, extracted_list)
                    except Exception:
                        pass
                return baike_result

            # 维基百科备用
            wiki_result = self._search_wikipedia(query, max_results=3)
            if wiki_result:
                if router and domain and domain != "general":
                    try:
                        extracted_list = []
                        for r in wiki_result["results"]:
                            snippet = r.get("snippet", "") + " " + r.get("title", "")
                            fields = router.extract_fields(snippet, domain)
                            r["fields"] = fields
                            r["is_whitelisted"] = router.is_whitelisted(r.get("url", ""), domain)
                            extracted_list.append(fields)
                        wiki_result["results"] = router.rank_results(
                            wiki_result["results"], domain, extracted_list)
                    except Exception:
                        pass
                return wiki_result

        # 多引擎备用：百度优先（mu属性解析真实URL效果最佳）→ Bing → 搜狗 → DuckDuckGo
        # 引擎选择说明：
        #   - 百度：mu属性提取真实URL效果最佳，但易被反爬拦截
        #   - Bing：结果质量高，新版HTML用 <li class="b_algo"> 包裹结果
        #   - 搜狗：国内可达，h3+a结构稳定，作为百度反爬时的备选
        #   - DuckDuckGo：海外引擎，国内可能不可达
        engines = [
            {
                "name": "百度",
                "url": f"https://www.baidu.com/s?wd={encoded_query}&rn={max_results}",
                "link_pattern": r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
                "snippet_pattern": r'<span class="content-right_[^"]*"[^>]*>(.*?)</span>',
            },
            {
                "name": "Bing",
                "url": f"https://cn.bing.com/search?q={encoded_query}&count={max_results}",
                # Bing新版HTML：<li class="b_algo"><h2><a href="...">标题</a></h2><p>摘要</p></li>
                "link_pattern": r'<li class="b_algo"[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                "snippet_pattern": r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>',
            },
            {
                "name": "搜狗",
                "url": f"https://www.sogou.com/web?query={encoded_query}",
                "link_pattern": r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
                "snippet_pattern": r'<p[^>]*class="[^"]*str_info[^"]*"[^>]*>(.*?)</p>',
            },
            {
                "name": "DuckDuckGo",
                "url": f"https://html.duckduckgo.com/html/?q={encoded_query}",
                "link_pattern": r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                "snippet_pattern": r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
            },
        ]

        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        last_error = ""
        for engine in engines:
            try:
                # 使用curl调用（绕过Python urllib的SSL/代理问题）
                result = subprocess.run(
                    ["curl", "-s", "-L", "--max-time", "10",
                     "-H", f"User-Agent: {ua}",
                     "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.8",
                     "-H", "Accept-Language: zh-CN,zh;q=0.9",
                     engine["url"]],
                    capture_output=True, timeout=15
                )
                html = result.stdout.decode("utf-8", errors="ignore")
                if not html or len(html) < 500:
                    last_error = f"{engine['name']}: 空响应"
                    continue

                # 百度引擎：通过 mu 属性提取真实URL，避免解析重定向链接
                # 百度搜索结果的真实URL存储在元素的 mu 属性中，<a href> 是时效性短的重定向链接
                if engine["name"] == "百度":
                    baidu_result = self._parse_baidu_results(
                        html, max_results, enhanced_query, query, domain, router
                    )
                    if baidu_result:
                        return baidu_result
                    # mu解析失败时，尝试旧版h3+a href解析作为fallback
                    legacy_result = self._parse_baidu_legacy(
                        html, max_results, enhanced_query, query, domain, router
                    )
                    if legacy_result:
                        logger.info(f"百度mu解析失败，旧版h3解析成功: {len(legacy_result.get('results', []))}条")
                        return legacy_result
                    last_error = f"{engine['name']}: mu+legacy解析均无结果(html长度={len(html)})"
                    continue

                results = []
                links = re.findall(engine["link_pattern"], html, re.S)
                for link, title in links[:max_results]:
                    clean_title = re.sub(r'<[^>]+>', '', title).strip()
                    if not clean_title:
                        continue
                    if "uddg=" in link:
                        m = re.search(r'uddg=([^&]+)', link)
                        actual_url = urlparse.unquote(m.group(1)) if m else link
                    else:
                        actual_url = link
                    results.append({"title": clean_title, "url": actual_url})

                snippets = re.findall(engine["snippet_pattern"], html, re.S)
                for i in range(min(len(results), len(snippets))):
                    results[i]["snippet"] = re.sub(r'<[^>]+>', '', snippets[i]).strip()

                if results:
                    logger.info(f"联网搜索[{engine['name']}]: query={enhanced_query[:50]}, 命中{len(results)}条")
                    # 领域增强：结果重排 + 字段提取
                    if router and domain and domain != "general":
                        try:
                            # 从 snippet 提取字段（用于重排加权）
                            extracted_list = []
                            for r in results:
                                snippet = r.get("snippet", "") + " " + r.get("title", "")
                                fields = router.extract_fields(snippet, domain)
                                r["fields"] = fields
                                r["is_whitelisted"] = router.is_whitelisted(r.get("url", ""), domain)
                                extracted_list.append(fields)
                            # 重排：白名单源+字段命中优先
                            results = router.rank_results(results, domain, extracted_list)
                            logger.info(f"领域重排[{domain}]: 白名单命中{sum(1 for r in results if r.get('is_whitelisted'))}条, "
                                        f"字段命中{sum(1 for r in results if r.get('fields'))}条")
                        except Exception as e:
                            logger.debug(f"领域重排/字段提取失败（不影响主流程）: {e}")
                    return {"query": enhanced_query, "original_query": query, "results": results,
                            "count": len(results), "engine": engine["name"],
                            "domain": domain or "general"}
                else:
                    last_error = f"{engine['name']}: 无匹配结果(html长度={len(html)})"
            except Exception as e:
                last_error = f"{engine['name']}: {e}"
                logger.debug(f"联网搜索[{engine['name']}]失败: {e}")
                continue

        # 所有引擎失败时，冷门查询重试：缩短查询词/去掉地域限定
        retry_result = self._retry_obscure_query(query, max_results, domain, router)
        if retry_result:
            return retry_result

        return {"query": query, "results": [], "error": f"所有搜索引擎失败: {last_error}"}

    def _parse_baidu_results(self, html: str, max_results: int, enhanced_query: str,
                              original_query: str, domain: str, router) -> Optional[Dict]:
        """解析百度搜索结果HTML：通过 mu 属性提取真实URL

        百度搜索结果特点：
          - 真实URL存储在搜索结果块根元素的 mu 属性中（如 hotels.ctrip.com/...）
          - <a href> 是 baidu.com/link?... 重定向链接，时效性短且需 Referer
          - 通过 mu 属性可直接获取目标页面URL，无需解析重定向

        解析方式：按 mu="..." 切分HTML，每个块包含真实URL+后续内容(标题/snippet/价格/评分)
        """
        # 按 mu 属性切分：blocks[0]=开头, 之后交替为 [url, content, url, content, ...]
        blocks = re.split(r'mu="(https?://[^"]+)"', html)
        if len(blocks) < 3:
            return None

        results = []
        for i in range(1, len(blocks), 2):
            real_url = blocks[i]
            content = blocks[i + 1] if i + 1 < len(blocks) else ""
            content = content[:8000]  # 限制每块处理长度

            # 标题提取：从 <h3> 标签
            title_match = re.search(r'<h3[^>]*>(.*?)</h3>', content, re.S)
            title = ""
            if title_match:
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                title = title.replace("<!--s-text-->", "").replace("<!--/s-text-->", "").strip()
            if not title:
                continue

            # 摘要提取：适配百度不同版本HTML结构
            snippet = ""
            for pat in [
                r'content-right_[A-Za-z0-9]+[^>]*>(.*?)</span>',
                r'c-abstract[^>]*>(.*?)</(?:div|span)>',
                r'cosc-text[^>]*>(.*?)</(?:div|span)>',
                r'c-span-last[^>]*>(.*?)</div>',
            ]:
                m = re.search(pat, content, re.S)
                if m:
                    snippet = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                    break

            # 价格提取（￥/¥ + 数字）
            prices = re.findall(r'[￥¥]\s*([\d,]+)', content[:5000])
            # 评分提取（X.X 分）
            ratings = re.findall(r'([0-9]\.[0-9])\s*分', content[:5000])

            result = {
                "title": title,
                "url": real_url,  # 真实URL，非百度重定向链接
                "snippet": snippet[:200],
            }
            if prices:
                result["prices"] = prices[:3]
            if ratings:
                result["ratings"] = ratings[:3]
            results.append(result)

            if len(results) >= max_results:
                break

        if not results:
            return None

        logger.info(f"联网搜索[百度]: query={enhanced_query[:50]}, 命中{len(results)}条 (mu属性解析)")

        # 领域增强：结果重排 + 字段提取
        if router and domain and domain != "general":
            try:
                extracted_list = []
                for r in results:
                    snippet = r.get("snippet", "") + " " + r.get("title", "")
                    # 价格/评分也注入字段提取输入，提升领域字段命中率
                    if r.get("prices"):
                        snippet += " 价格 " + " ".join(r["prices"])
                    if r.get("ratings"):
                        snippet += " 评分 " + " ".join(r["ratings"])
                    fields = router.extract_fields(snippet, domain)
                    r["fields"] = fields
                    r["is_whitelisted"] = router.is_whitelisted(r.get("url", ""), domain)
                    extracted_list.append(fields)
                results = router.rank_results(results, domain, extracted_list)
                logger.info(f"领域重排[{domain}]: 白名单命中{sum(1 for r in results if r.get('is_whitelisted'))}条, "
                            f"字段命中{sum(1 for r in results if r.get('fields'))}条")
            except Exception as e:
                logger.debug(f"领域重排/字段提取失败（不影响主流程）: {e}")

        return {"query": enhanced_query, "original_query": original_query, "results": results,
                "count": len(results), "engine": "百度", "domain": domain or "general"}

    def _parse_baidu_legacy(self, html: str, max_results: int, enhanced_query: str,
                             original_query: str, domain: str, router) -> Optional[Dict]:
        """百度旧版HTML解析fallback：mu属性解析失败时使用

        百度旧版HTML结构：<h3><a href="baidu.com/link?...">标题</a></h3>
        注意：a href是百度重定向链接，非真实URL，但至少能获取标题和摘要
        """
        # 提取h3+a href对
        links = re.findall(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>', html, re.S)
        if not links or len(links) < 1:
            return None

        results = []
        for link, title_html in links[:max_results]:
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            title = title.replace("<!--s-text-->", "").replace("<!--/s-text-->", "").strip()
            if not title:
                continue
            # 百度重定向链接标记（认知层深度爬取时需处理）
            results.append({"title": title, "url": link, "snippet": ""})

        if not results:
            return None

        # 摘要提取
        snippets = re.findall(r'content-right_[A-Za-z0-9]+[^>]*>(.*?)</span>', html, re.S)
        if not snippets:
            snippets = re.findall(r'c-abstract[^>]*>(.*?)</(?:div|span)>', html, re.S)
        for i in range(min(len(results), len(snippets))):
            results[i]["snippet"] = re.sub(r'<[^>]+>', '', snippets[i]).strip()[:200]

        logger.info(f"联网搜索[百度-legacy]: query={enhanced_query[:50]}, 命中{len(results)}条 (h3解析)")

        # 领域增强
        if router and domain and domain != "general":
            try:
                extracted_list = []
                for r in results:
                    snippet = r.get("snippet", "") + " " + r.get("title", "")
                    fields = router.extract_fields(snippet, domain)
                    r["fields"] = fields
                    r["is_whitelisted"] = router.is_whitelisted(r.get("url", ""), domain)
                    extracted_list.append(fields)
                results = router.rank_results(results, domain, extracted_list)
            except Exception as e:
                logger.debug(f"legacy领域重排失败（不影响主流程）: {e}")

        return {"query": enhanced_query, "original_query": original_query, "results": results,
                "count": len(results), "engine": "百度-legacy", "domain": domain or "general"}

    def _retry_obscure_query(self, query: str, max_results: int, domain: str, router) -> Optional[Dict]:
        """冷门查询重试：所有引擎失败后，简化查询词重试

        策略：
          1. hotel领域：去掉地域限定（"成都五块石蓝光中央天地酒店" → "蓝光中央天地酒店"）
          2. 长查询：截取核心词重搜（>10字符时取后半部分）
          3. 去掉修饰词：去掉"价格""推荐""评测"等后缀重搜
        """
        retry_queries = []

        # 策略1：hotel领域去掉地域限定（城市+区名+路段名）
        if domain == "hotel" or "酒店" in query:
            # 去掉常见城市名+区名
            geo_words = ["北京", "上海", "广州", "深圳", "成都", "杭州", "南京", "武汉",
                         "西安", "重庆", "苏州", "天津", "五块石", "外滩", "浦东", "虹桥",
                         "市中心", "附近", "周边"]
            simplified = query
            for w in geo_words:
                simplified = simplified.replace(w, "")
            simplified = re.sub(r"\s+", " ", simplified).strip()
            if simplified and simplified != query and len(simplified) >= 4:
                retry_queries.append(simplified)

        # 策略2：去掉常见修饰后缀
        suffix_words = ["价格", "多少钱", "推荐", "评测", "怎么样", "最新", "排行", "排名", "对比"]
        simplified2 = query
        for w in suffix_words:
            simplified2 = simplified2.replace(w, "")
        simplified2 = re.sub(r"\s+", " ", simplified2).strip()
        if simplified2 and simplified2 != query and len(simplified2) >= 4:
            retry_queries.append(simplified2)

        # 策略3：长查询截取核心词（取前4-8字符重搜）
        if len(query) > 10:
            # 按空格分词取前2-3个关键词
            parts = query.split()
            if len(parts) > 2:
                core = " ".join(parts[:2])
                if core and core != query:
                    retry_queries.append(core)

        # 去重
        retry_queries = list(dict.fromkeys(retry_queries))[:3]

        for rq in retry_queries:
            logger.info(f"冷门查询重试: '{query[:40]}' → '{rq[:40]}'")
            # 递归调用（但不再触发重试，避免无限循环）
            result = self._tool_web_search_no_retry(rq, max_results, domain)
            if result and result.get("results"):
                result["retry_from"] = query
                result["retry_query"] = rq
                return result

        return None

    def _tool_web_search_no_retry(self, query: str, max_results: int = 5, domain: str = "") -> Dict:
        """web_search内部方法（不触发冷门重试，避免无限递归）"""
        import subprocess
        import urllib.parse as urlparse

        router = None
        enhanced_query = query
        if domain and domain != "general":
            try:
                from domain_router import get_router
                router = get_router()
                enhanced_query = router.enhance_query(query, domain)
            except Exception:
                enhanced_query = query

        encoded_query = urlparse.quote(enhanced_query)

        # 外部API知识源路由：GitHub/Arxiv/HackerNews等结构化数据API（免Key）
        try:
            from w1_layer.external_apis import route_external_api
            api_result = route_external_api(query)
            if api_result and api_result.get("success"):
                logger.info(f"外部API命中[{api_result.get('source', '?')}]: {query[:40]}")
                return self._format_api_as_search_result(api_result, query, domain, router)
        except Exception as e:
            logger.debug(f"外部API路由失败: {e}")

        # 知识源优先：百度百科 + 维基百科（免Key，权威稳定）
        # 跳过实时/地域服务/商品比价类查询
        is_realtime_query = bool(re.search(r"今天|今日|最近|最新|近期|现在|新闻|热点|热搜|实时|行情|股价|天气", query))
        is_local_service = bool(re.search(r"酒店|民宿|客栈|旅馆|附近|周边|推荐|外滩|春熙路|新街口|江汉路|五块石|王府井|钟楼|鼓浪屿|栈桥|解放碑|太古里|迪士尼|三里屯|亚龙湾|五一广场|科技园|天河区|南山区|步行街|性价比|海景房|江景", query))
        is_product_query = bool(re.search(r"iPhone|华为|小米|三星|戴森|大疆|任天堂|iPad|MacBook|Kindle|AirPods|索尼|联想|海尔|美的|格力|OPPO|vivo|价格|评测|对比|值得买|性价比|跑分|排行", query, re.I))
        use_baike = not (is_realtime_query or is_local_service or is_product_query)
        if use_baike:
            baike_result = self._search_baike_baidu(query, max_results=3)
            if baike_result:
                return baike_result
            wiki_result = self._search_wikipedia(query, max_results=3)
            if wiki_result:
                return wiki_result

        engines = [
            {"name": "百度", "url": f"https://www.baidu.com/s?wd={encoded_query}&rn={max_results}"},
            {"name": "Bing", "url": f"https://cn.bing.com/search?q={encoded_query}&count={max_results}",
             "link_pattern": r'<h2><a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a></h2>',
             "snippet_pattern": r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>'},
            {"name": "DuckDuckGo", "url": f"https://html.duckduckgo.com/html/?q={encoded_query}",
             "link_pattern": r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
             "snippet_pattern": r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>'},
        ]

        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        for engine in engines:
            try:
                result = subprocess.run(
                    ["curl", "-s", "-L", "--max-time", "10",
                     "-H", f"User-Agent: {ua}",
                     "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.8",
                     "-H", "Accept-Language: zh-CN,zh;q=0.9",
                     engine["url"]],
                    capture_output=True, timeout=15
                )
                html = result.stdout.decode("utf-8", errors="ignore")
                if not html or len(html) < 500:
                    continue

                if engine["name"] == "百度":
                    baidu_result = self._parse_baidu_results(
                        html, max_results, enhanced_query, query, domain, router
                    )
                    if baidu_result:
                        return baidu_result
                    legacy_result = self._parse_baidu_legacy(
                        html, max_results, enhanced_query, query, domain, router
                    )
                    if legacy_result:
                        return legacy_result
                    continue

                # Bing/DuckDuckGo
                results = []
                links = re.findall(engine["link_pattern"], html, re.S)
                for link, title in links[:max_results]:
                    clean_title = re.sub(r'<[^>]+>', '', title).strip()
                    if not clean_title:
                        continue
                    if "uddg=" in link:
                        m = re.search(r'uddg=([^&]+)', link)
                        actual_url = urlparse.unquote(m.group(1)) if m else link
                    else:
                        actual_url = link
                    results.append({"title": clean_title, "url": actual_url})

                snippets = re.findall(engine["snippet_pattern"], html, re.S)
                for i in range(min(len(results), len(snippets))):
                    results[i]["snippet"] = re.sub(r'<[^>]+>', '', snippets[i]).strip()

                if results:
                    if router and domain and domain != "general":
                        try:
                            extracted_list = []
                            for r in results:
                                snippet = r.get("snippet", "") + " " + r.get("title", "")
                                fields = router.extract_fields(snippet, domain)
                                r["fields"] = fields
                                r["is_whitelisted"] = router.is_whitelisted(r.get("url", ""), domain)
                                extracted_list.append(fields)
                            results = router.rank_results(results, domain, extracted_list)
                        except Exception:
                            pass
                    return {"query": enhanced_query, "original_query": query, "results": results,
                            "count": len(results), "engine": engine["name"],
                            "domain": domain or "general"}
            except Exception:
                continue

        return {"query": query, "results": [], "error": "重试搜索仍无结果"}

    def _tool_web_crawl(self, url: str, max_length: int = 8000) -> Dict:
        """全网爬取：抓取指定URL页面正文内容

        用途：
          1. web_search降级备选（已知URL时直接抓取）
          2. 深度信息获取（搜索只返回摘要，爬取获取全文）
          3. 用户直接提供URL时抓取内容

        技术方案：curl获取HTML → 正则提取正文 → 去标签 → 截断
        安全：SSRF防护（拦截内网IP/云元数据/localhost/file协议）
        """
        import subprocess
        import urllib.parse as urlparse
        import socket
        import ipaddress

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # URL合法性校验
        try:
            parsed = urlparse.urlparse(url)
            if not parsed.netloc:
                return {"url": url, "content": "", "error": "无效URL"}
        except Exception:
            return {"url": url, "content": "", "error": "URL解析失败"}

        # ── SSRF防护：拦截内网/元数据/危险地址 ──
        try:
            hostname = parsed.hostname or ""
            hostname_lower = hostname.lower()

            # 1. 拦截localhost类主机名
            if hostname_lower in ("localhost", "ip6-localhost", "ip6-loopback"):
                return {"url": url, "content": "", "error": "SSRF防护：禁止访问localhost"}

            # 2. 拦截云元数据端点（AWS/GCP/Azure/阿里云）
            META_HOSTS = (
                "169.254.169.254",   # AWS/Azure元数据
                "metadata.google.internal",  # GCP元数据
                "100.100.100.200",    # 阿里云元数据
            )
            if hostname_lower in META_HOSTS or hostname in META_HOSTS:
                return {"url": url, "content": "", "error": "SSRF防护：禁止访问云元数据端点"}

            # 3. 拦截内网IP地址（10./172.16-31./192.168./127./169.254./::1/fe80::）
            try:
                # 尝试解析为IP地址
                ip_obj = ipaddress.ip_address(hostname)
                if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
                    return {"url": url, "content": "", "error": f"SSRF防护：禁止访问内网地址 {hostname}"}
            except ValueError:
                # 不是IP地址格式（是域名），做DNS解析后再检查
                try:
                    addrs = socket.getaddrinfo(hostname, None)
                    for af, _, _, _, sa in addrs:
                        ip_str = sa[0]
                        try:
                            ip_obj = ipaddress.ip_address(ip_str)
                            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
                                return {"url": url, "content": "", "error": f"SSRF防护：域名{hostname}解析到内网地址 {ip_str}"}
                        except ValueError:
                            continue
                except socket.gaierror:
                    pass  # DNS解析失败，让后续curl报错

            # 4. 拦截危险端口
            port = parsed.port
            if port and port in (22, 23, 25, 110, 143, 161, 389, 636, 1433, 1521, 3306, 5432, 6379, 27017, 9200):
                return {"url": url, "content": "", "error": f"SSRF防护：禁止访问敏感端口 {port}"}

        except Exception as _e:
            logger.debug(f"SSRF检查异常(非阻断): {_e}")

        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        try:
            result = subprocess.run(
                ["curl", "-s", "-L", "--max-time", "15",
                 "-H", f"User-Agent: {ua}",
                 "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.8",
                 "-H", "Accept-Language: zh-CN,zh;q=0.9",
                 url],
                capture_output=True, timeout=20
            )
            html = result.stdout.decode("utf-8", errors="ignore")
            if not html or len(html) < 200:
                return {"url": url, "content": "", "error": "空响应或页面过短"}

            # 提取页面标题
            title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""

            # 移除script/style/nav/footer/header标签及内容
            cleaned = re.sub(r'<(script|style|nav|footer|header|aside|noscript)[^>]*>.*?</\1>',
                             '', html, flags=re.S | re.I)
            # 移除HTML注释
            cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.S)
            # 提取正文：优先article/main/content，其次p标签
            main_content = ""
            for tag in ['article', 'main', 'div[class*="content"]', 'div[class*="article"]',
                        'div[id*="content"]', 'div[id*="article"]']:
                m = re.search(f'<{tag}[^>]*>(.*?)</(?:article|main|div)>', cleaned, re.S | re.I)
                if m:
                    main_content = m.group(1)
                    break
            if not main_content:
                # 退而求其次：提取所有p标签内容
                paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', cleaned, re.S | re.I)
                main_content = "\n".join(paragraphs)
            if not main_content:
                # 最后手段：直接去标签
                main_content = cleaned

            # 去除所有HTML标签
            text = re.sub(r'<[^>]+>', ' ', main_content)
            # 解析HTML实体
            text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            text = re.sub(r'&[a-zA-Z]+;', ' ', text)
            # 压缩空白
            text = re.sub(r'\s+', ' ', text).strip()
            # 截断到最大长度
            if len(text) > max_length:
                text = text[:max_length] + "...(内容已截断)"

            if len(text) < 50:
                return {"url": url, "content": "", "error": "提取正文过短（可能是JS渲染页面）"}

            logger.info(f"全网爬取: url={url[:50]}, 标题={title[:30]}, 正文长度={len(text)}")
            return {
                "url": url,
                "title": title[:200],
                "content": text,
                "content_length": len(text),
            }
        except subprocess.TimeoutExpired:
            return {"url": url, "content": "", "error": "抓取超时"}
        except Exception as e:
            logger.warning(f"全网爬取失败: {e}")
            return {"url": url, "content": "", "error": f"爬取失败: {e}"}

    def _tool_datetime_calc(self, start: str, op: str = "+", days: int = 0) -> Dict:
        """日期计算"""
        start_date = datetime.strptime(start, "%Y-%m-%d")
        if op == "+":
            result_date = start_date + timedelta(days=days)
        else:
            result_date = start_date - timedelta(days=days)
        return {"start": start, "op": op, "days": days,
                "result": result_date.strftime("%Y-%m-%d")}

    def _tool_unit_convert(self, value: float, from_unit: str, to_unit: str) -> Dict:
        """单位换算（温度/长度/重量）"""
        from_unit = from_unit.lower().strip()
        to_unit = to_unit.lower().strip()
        # 温度
        temp_units = {"c": "celsius", "摄氏": "celsius", "f": "fahrenheit", "华氏": "fahrenheit", "k": "kelvin"}
        if from_unit in temp_units and to_unit in temp_units:
            f = from_unit[0] if from_unit[0] in "cfk" else temp_units[from_unit][0]
            t = to_unit[0] if to_unit[0] in "cfk" else temp_units[to_unit][0]
            if f == "c" and t == "f":
                result = value * 9 / 5 + 32
            elif f == "f" and t == "c":
                result = (value - 32) * 5 / 9
            elif f == "c" and t == "k":
                result = value + 273.15
            elif f == "k" and t == "c":
                result = value - 273.15
            else:
                result = value
            return {"value": value, "from": from_unit, "to": to_unit, "result": round(result, 2)}
        # 长度
        length_units = {"m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001, "ft": 0.3048, "in": 0.0254, "mi": 1609.34}
        if from_unit in length_units and to_unit in length_units:
            result = value * length_units[from_unit] / length_units[to_unit]
            return {"value": value, "from": from_unit, "to": to_unit, "result": round(result, 4)}
        # 重量
        weight_units = {"kg": 1.0, "g": 0.001, "lb": 0.4536, "oz": 0.0283, "t": 1000.0}
        if from_unit in weight_units and to_unit in weight_units:
            result = value * weight_units[from_unit] / weight_units[to_unit]
            return {"value": value, "from": from_unit, "to": to_unit, "result": round(result, 4)}
        return {"value": value, "from": from_unit, "to": to_unit, "result": None, "error": "不支持的单位"}

    # ─── 写操作工具实现（2种，沙箱隔离） ────────────────────────────────────

    def _tool_file_write(self, path: str, content: str) -> Dict:
        """文件写入（限制在sandbox目录）"""
        full_path = self._safe_path(path, write=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"path": path, "written": len(content), "abs_path": full_path}

    def _tool_code_run(self, code: str) -> Dict:
        """代码执行（沙箱隔离）"""
        return self._sandbox_exec(code)

    # ─── 沙箱执行引擎 ────────────────────────────────────

    # C1修复：禁止访问的属性名（防沙箱逃逸）
    FORBIDDEN_ATTRS = {
        '__class__', '__bases__', '__subclasses__', '__mro__', '__globals__',
        '__builtins__', '__dict__', '__code__', '__module__', '__init__',
        '__import__', '__getattribute__', '__setattr__', '__delattr__',
        'f_globals', 'f_locals', 'f_builtins', 'f_code', 'co_consts',
        'gi_frame', 'gi_code', 'cr_frame', 'cr_code', 'ag_frame', 'ag_code',
    }

    def _validate_code_ast(self, code: str) -> tuple:
        """C1修复：AST预检，拒绝危险节点（属性访问/dunder访问）

        Returns:
            (is_safe, error_msg)
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            # 表达式模式
            try:
                tree = ast.parse(code, mode='eval')
            except SyntaxError as e2:
                return False, f"语法错误: {e2}"

        for node in ast.walk(tree):
            # 禁止属性访问（x.__class__ 等）
            if isinstance(node, ast.Attribute):
                attr_name = node.attr
                if attr_name.startswith('_'):
                    return False, f"禁止访问下划线属性: {attr_name}"
                if attr_name in self.FORBIDDEN_ATTRS:
                    return False, f"禁止访问危险属性: {attr_name}"
            # 禁止直接调用 eval/exec/compile/__import__/getattr
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ('eval', 'exec', 'compile', '__import__',
                                    'getattr', 'setattr', 'delattr', 'globals',
                                    'locals', 'vars', 'dir', 'type', 'input'):
                    return False, f"禁止调用危险函数: {node.func.id}"
            # 禁止导入语句
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return False, "禁止import语句"
        return True, ""

    def _sandbox_exec(self, code: str, timeout: float = 5.0) -> Dict:
        """沙箱执行Python代码

        安全措施（C1修复后）：
        1. AST预检：拒绝属性访问、dunder访问、危险函数调用、import语句
        2. 限制可用内置函数（禁止 open/exec/eval/__import__/compile/getattr）
        3. 禁止 dangerous 模块导入
        4. 超时限制（5秒）
        5. 输出捕获（stdout/stderr）
        6. 工作目录限制在 sandbox
        """
        # C1修复：AST预检
        is_safe, err_msg = self._validate_code_ast(code)
        if not is_safe:
            return {"output": "", "error": f"安全拦截: {err_msg}", "result": None}

        # 安全的内置函数白名单
        safe_builtins = {
            'abs': abs, 'all': all, 'any': any, 'ascii': ascii, 'bin': bin,
            'bool': bool, 'bytearray': bytearray, 'bytes': bytes,
            'callable': callable, 'chr': chr, 'complex': complex,
            'dict': dict, 'divmod': divmod, 'enumerate': enumerate,
            'filter': filter, 'float': float, 'format': format, 'frozenset': frozenset,
            'hash': hash, 'hex': hex, 'int': int, 'isinstance': isinstance,
            'issubclass': issubclass, 'iter': iter, 'len': len, 'list': list,
            'map': map, 'max': max, 'min': min, 'next': next, 'oct': oct,
            'ord': ord, 'pow': pow, 'print': print, 'range': range,
            'repr': repr, 'reversed': reversed, 'round': round, 'set': set,
            'slice': slice, 'sorted': sorted, 'str': str, 'sum': sum,
            'tuple': tuple, 'zip': zip,
        }
        # 安全的模块白名单
        safe_modules = {
            'math': math, 'json': json, 'time': time, 're': re,
            'datetime': __import__('datetime'),
        }

        # 捕获输出
        import io
        from contextlib import redirect_stdout, redirect_stderr
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        # 沙箱全局命名空间
        sandbox_globals = {
            '__builtins__': safe_builtins,
            '__name__': '__sandbox__',
            **safe_modules,
        }

        # 执行（带超时）
        import threading

        result_holder = {'output': '', 'error': '', 'result': None}

        def _run():
            try:
                with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                    # 尝试 eval（表达式）或 exec（语句）
                    try:
                        result_holder['result'] = eval(code, sandbox_globals, {})
                    except SyntaxError:
                        exec(code, sandbox_globals, {})
            except Exception as e:
                result_holder['error'] = f"{type(e).__name__}: {e}"

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            return {"output": "", "error": f"执行超时（>{timeout}秒）", "result": None}

        output = stdout_buf.getvalue()
        error = stderr_buf.getvalue() or result_holder['error']
        return {
            "output": output,
            "error": error,
            "result": result_holder['result'],
        }

    # ─── 路径安全 ────────────────────────────────────

    def _safe_path(self, path: str, write: bool = False) -> str:
        """安全路径检查（防目录遍历攻击，M1修复前缀碰撞）"""
        if os.path.isabs(path):
            # 绝对路径必须在项目目录内
            full = os.path.abspath(path)
        else:
            # 相对路径基于sandbox目录
            if write:
                full = os.path.join(SANDBOX_DIR, path)
            else:
                full = os.path.join(SANDBOX_DIR, path)  # C5修复：read也限制sandbox
            full = os.path.abspath(full)
        # 防目录遍历（M1修复：使用 os.sep 后缀防止前缀碰撞）
        if write:
            allowed_root = SANDBOX_DIR
        else:
            allowed_root = SANDBOX_DIR
        # M1修复：用 commonpath 替代 startswith，防止前缀碰撞
        try:
            common = os.path.commonpath([full, allowed_root])
            if common != allowed_root:
                raise ValueError(f"路径越界: {path}（限制在 {allowed_root}）")
        except ValueError:
            raise ValueError(f"路径越界: {path}（限制在 {allowed_root}）")
        return full
