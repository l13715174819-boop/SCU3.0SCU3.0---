# -*- coding: utf-8 -*-
"""
m_layer/nl_tool_selector.py — 自然语言工具选择器（M层）
======================================================
v5.0第二批：用LLM决定用什么工具，替代规则匹配

能力对标：AI助手的自然语言理解→工具选择能力

功能:
  1. 接收用户自然语言
  2. 调用LLM分析意图，选择最合适的工具
  3. 生成工具参数
  4. 无LLM时降级为规则匹配

架构归属：M层（认知层工具选择）
依赖：m_layer/llm_client
"""
import json
import re
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger("SCU3.m.nl_selector")


class NLToolSelector:
    """自然语言工具选择器

    用法:
        selector = NLToolSelector()
        result = selector.select("帮我搜索Python教程")
        # result = {"tool": "web_search", "params": {"query": "Python教程"}, "confidence": 0.9}
    """

    # 工具描述（供LLM参考）
    TOOL_DESCRIPTIONS = {
        "calculator": "数学计算，如'计算2+3'、'算一下100*200'",
        "weather": "天气查询，如'北京天气'、'上海气温'",
        "time_now": "获取当前时间，如'几点了'、'现在时间'",
        "text_stats": "文本统计，如'统计字数'、'分析文本'",
        "file_read": "读取文件，如'读取readme.md'、'看test.txt内容'",
        "file_write": "写入文件，如'写入result.txt'、'保存到output.md'",
        "code_run": "执行Python代码，如'运行print(hello)'、'执行这段代码'",
        "web_search": "网络搜索，如'搜索Python教程'、'查一下FastAPI'",
        "web_fetch": "抓取网页，如'获取example.com内容'",
        "git_status": "Git状态，如'查看git状态'、'有什么改动'",
        "git_log": "Git日志，如'查看提交历史'、'最近的git log'",
        "exchange_rate": "汇率查询，如'美元汇率'、'USD汇率'",
        "crypto_price": "加密货币价格，如'比特币价格'、'BTC价格'",
        "stock_price": "股票价格，如'AAPL股票'、'苹果股价'",
        "github_search": "GitHub搜索，如'搜索Python仓库'",
        "datetime_calc": "日期计算，如'2026-01-01加30天'",
        "unit_convert": "单位换算，如'100摄氏度转华氏'、'1km转英里'",
        "json_query": "JSON查询，如'查询JSON的a.b.c字段'",
        "regex_match": "正则匹配，如'用\\d+匹配数字'",
        "hash_calc": "哈希计算，如'计算MD5'、'算SHA256'",
        "base64_codec": "Base64编解码，如'base64编码'",
        "url_codec": "URL编解码，如'URL编码'",
        "shell_exec": "Shell命令，如'执行ls'、'运行dir'",
        "file_copy": "文件复制，如'复制a.txt到b.txt'",
        "file_move": "文件移动，如'移动a.txt到dir/'",
        "file_delete": "文件删除，如'删除temp.txt'",
        "dir_create": "创建目录，如'创建newdir目录'",
        "dir_list": "列出目录，如'列出当前目录'",
    }

    def __init__(self):
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            try:
                from m_layer.llm_client import get_client
                self._llm = get_client()
            except Exception:
                pass
        return self._llm

    def select(self, query: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """选择工具

        Args:
            query: 用户自然语言
            context: 额外上下文

        Returns:
            {
                "tool": "工具名",
                "params": {...},
                "confidence": float,
                "source": "llm" | "rule",
            }
        """
        # 尝试LLM选择
        llm = self._get_llm()
        if llm and llm.mode != "rule_based":
            result = self._select_with_llm(query, context)
            if result:
                return result

        # 降级：规则匹配
        return self._select_with_rules(query)

    def select_multi(self, query: str, max_tools: int = 3) -> List[Dict[str, Any]]:
        """选择多个候选工具（按置信度排序）"""
        # LLM选择
        llm = self._get_llm()
        if llm and llm.mode != "rule_based":
            result = self._select_multi_with_llm(query, max_tools)
            if result:
                return result

        # 降级：规则匹配
        return [self._select_with_rules(query)]

    def _select_with_llm(self, query: str, context: Optional[Dict]) -> Optional[Dict]:
        """用LLM选择工具"""
        tools_desc = "\n".join(
            f"  - {name}: {desc}" for name, desc in self.TOOL_DESCRIPTIONS.items()
        )

        prompt = f"""分析用户意图，选择最合适的工具。

用户输入: {query}

可用工具:
{tools_desc}

请严格按JSON格式输出:
```json
{{
  "tool": "工具名",
  "params": {{"参数名": "值"}},
  "confidence": 0.0-1.0,
  "reasoning": "选择理由"
}}
```

如果无合适工具，返回:
```json
{{
  "tool": "none",
  "params": {{}},
  "confidence": 0.0,
  "reasoning": "无匹配工具"
}}
```"""

        try:
            result = self._llm.chat(prompt, system_prompt="analytical")
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            return self._parse_llm_selection(content)
        except Exception as e:
            logger.warning(f"LLM工具选择失败: {e}")
            return None

    def _select_multi_with_llm(self, query: str, max_tools: int) -> Optional[List[Dict]]:
        """用LLM选择多个工具"""
        tools_desc = "\n".join(
            f"  - {name}: {desc}" for name, desc in self.TOOL_DESCRIPTIONS.items()
        )

        prompt = f"""分析用户意图，选择最多{max_tools}个合适的工具。

用户输入: {query}

可用工具:
{tools_desc}

请严格按JSON数组格式输出:
```json
[
  {{"tool": "工具名", "params": {{}}, "confidence": 0.9}},
  {{"tool": "工具名", "params": {{}}, "confidence": 0.7}}
]
```"""

        try:
            result = self._llm.chat(prompt, system_prompt="analytical")
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            # 解析JSON数组
            json_match = re.search(r'\[.*\]', content, re.S)
            if json_match:
                selections = json.loads(json_match.group(0))
                for s in selections:
                    s["source"] = "llm"
                return selections[:max_tools]
        except Exception as e:
            logger.warning(f"LLM多工具选择失败: {e}")
        return None

    def _parse_llm_selection(self, content: str) -> Optional[Dict]:
        """解析LLM选择结果"""
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.S)
        if json_match:
            raw = json_match.group(1)
        else:
            json_match = re.search(r'(\{.*\})', content, re.S)
            raw = json_match.group(1) if json_match else content

        try:
            data = json.loads(raw)
            if data.get("tool") == "none":
                return None
            return {
                "tool": data.get("tool", ""),
                "params": data.get("params", {}),
                "confidence": float(data.get("confidence", 0.5)),
                "source": "llm",
                "reasoning": data.get("reasoning", ""),
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def _select_with_rules(self, query: str) -> Dict[str, Any]:
        """规则匹配选择工具（降级方案）"""
        q = query.lower().strip()

        # 网络搜索
        if any(kw in q for kw in ["搜索", "查一下", "search", "google", "百度"]):
            # 提取搜索词
            search_term = re.sub(r'^(?:搜索|查一下|search|google|百度)\s*', '', query, flags=re.I)
            return {"tool": "web_search", "params": {"query": search_term.strip()},
                    "confidence": 0.8, "source": "rule"}

        # 网页抓取
        if any(kw in q for kw in ["获取网页", "抓取", "fetch", "访问url"]) or re.search(r'https?://', q):
            url_match = re.search(r'(https?://[^\s]+)', query)
            return {"tool": "web_fetch", "params": {"url": url_match.group(1) if url_match else ""},
                    "confidence": 0.8, "source": "rule"}

        # 天气
        for city in ["北京", "上海", "广州", "深圳", "成都", "杭州"]:
            if city in query and any(kw in q for kw in ["天气", "气温", "weather"]):
                return {"tool": "weather", "params": {"city": city},
                        "confidence": 0.9, "source": "rule"}

        # 计算
        if re.search(r'[\d\s+\-*/().]+', query) and any(kw in q for kw in ["计算", "算", "calc", "="]):
            expr = re.sub(r'^(?:计算|算一下|calc|=)\s*', '', query, flags=re.I).rstrip("=")
            return {"tool": "calculator", "params": {"expression": expr.strip()},
                    "confidence": 0.9, "source": "rule"}

        # 时间
        if any(kw in q for kw in ["几点", "现在时间", "当前时间", "now", "time"]):
            return {"tool": "time_now", "params": {}, "confidence": 0.9, "source": "rule"}

        # 文件读取
        m = re.search(r'(?:读|读取|read|cat)\s+([\w.-]+\.\w+)', query, re.I)
        if m:
            return {"tool": "file_read", "params": {"path": m.group(1)},
                    "confidence": 0.8, "source": "rule"}

        # 文件写入
        m = re.search(r'(?:写入|write|保存)\s+([\w.-]+\.\w+)', query, re.I)
        if m:
            return {"tool": "file_write", "params": {"path": m.group(1), "content": ""},
                    "confidence": 0.7, "source": "rule"}

        # 代码执行
        if any(kw in q for kw in ["运行代码", "run", "exec", "执行代码"]):
            return {"tool": "code_run", "params": {"code": ""},
                    "confidence": 0.7, "source": "rule"}

        # Git
        if any(kw in q for kw in ["git状态", "git status", "有什么改动"]):
            return {"tool": "git_status", "params": {"repo_path": "."},
                    "confidence": 0.8, "source": "rule"}
        if any(kw in q for kw in ["git日志", "git log", "提交历史"]):
            return {"tool": "git_log", "params": {"repo_path": ".", "limit": 10},
                    "confidence": 0.8, "source": "rule"}

        # 汇率
        m = re.search(r'([A-Za-z]{3})\s*(?:汇率|exchange)', q)
        if m:
            return {"tool": "exchange_rate", "params": {"base": m.group(1).upper()},
                    "confidence": 0.8, "source": "rule"}

        # 加密货币
        if any(kw in q for kw in ["比特币", "btc", "ethereum", "eth"]):
            symbol = "btc" if "btc" in q or "比特币" in q else "eth"
            return {"tool": "crypto_price", "params": {"symbol": symbol},
                    "confidence": 0.8, "source": "rule"}

        # 股票
        m = re.search(r'(?:股票|stock)\s*([A-Za-z]{1,5})', q)
        if m:
            return {"tool": "stock_price", "params": {"code": m.group(1).upper()},
                    "confidence": 0.8, "source": "rule"}

        # 目录列表
        if any(kw in q for kw in ["列出目录", "list dir", "ls", "dir"]):
            return {"tool": "dir_list", "params": {"path": "."},
                    "confidence": 0.7, "source": "rule"}

        # 哈希
        if any(kw in q for kw in ["md5", "sha256", "hash", "哈希"]):
            algo = "md5" if "md5" in q else ("sha256" if "sha256" in q else "md5")
            return {"tool": "hash_calc", "params": {"text": "", "algorithm": algo},
                    "confidence": 0.7, "source": "rule"}

        # 无匹配
        return {"tool": "none", "params": {}, "confidence": 0.0, "source": "rule",
                "reasoning": "无匹配工具"}


# ─── 单例 ────────────────────────────────────
_selector_instance: Optional[NLToolSelector] = None


def get_nl_selector() -> NLToolSelector:
    """获取自然语言工具选择器单例"""
    global _selector_instance
    if _selector_instance is None:
        _selector_instance = NLToolSelector()
    return _selector_instance
