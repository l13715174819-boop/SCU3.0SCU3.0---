# -*- coding: utf-8 -*-
"""
W2 层：w2_layer/perception.py — 感知层
=======================================
最外层，直接接收用户输入。属于 CUF W2 层。
数据流：感知(W2) → 记忆(W1) 需经守卫①跨层审计。
"""
import re
import logging
from typing import Dict, Any

logger = logging.getLogger("scu2.w2.perception")


class PerceptionLayer:
    """感知层 — 输入解析与意图识别"""

    def process(self, user_input: str, ctx: Dict[str, Any] = None) -> Dict[str, Any]:
        """解析用户输入"""
        ctx = ctx or {}
        # 防御None/非字符串输入
        if user_input is None:
            user_input = ""
        if not isinstance(user_input, str):
            user_input = str(user_input)
        text = user_input.strip()
        ctx["input"] = text
        ctx["perceived"] = text
        ctx["intent"] = self._detect_intent(text)
        # 领域识别：web_search 意图时进一步识别所属领域（hotel/product/medical/general）
        # 用于驱动领域插件配置（关键词增强+源白名单+字段解析）
        ctx["domain"] = "general"
        if ctx["intent"] == "web_search":
            try:
                from domain_router import detect_domain
                ctx["domain"] = detect_domain(text)
            except Exception as e:
                logger.debug(f"领域识别异常（不阻塞）: {e}")
                ctx["domain"] = "general"
        ctx["perception_ok"] = True
        logger.info(f"感知层: intent={ctx['intent']}, domain={ctx.get('domain', 'general')}, input={text[:50]}")
        return ctx

    def _detect_intent(self, text: str) -> str:
        """意图识别（含插件市场触发意图）"""
        # 优先级最高：追问/修正意图检测（依赖对话历史，不触发独立搜索）
        # 解决多轮对话中代词式查询（"刚才/再详细/不是这个"）被误判为独立搜索的问题
        if re.search(
            r"我刚才|刚才那个|刚才问的|刚才说的|上面.*提到|前文|上一个|"
            r"再详细|再深入|再解释|详细解释|深入分析|展开说|继续说|接着说|"
            r"不是这个|不对|不是.*意思|换个|另一个方面|另一方面|"
            r"反对.*理由|支持.*理由|这个方案|你.*说的|你.*提到|你的.*回答|"
            r"基于.*刚才|基于.*上面|基于.*前文",
            text, re.I
        ):
            return "followup"

        # 分析/批判类意图检测（优先于knowledge_query，引导深度分析）
        # 解决"分析潜在假设"等批判性查询被default prompt处理导致深度不足的问题
        # 方案C：扩展识别模式，让"分析XX的可能性/可行性/影响/原因/趋势/前景"触发阴阳对子思考
        if re.search(
            r"分析.*假设|潜在.*假设|批判|反思|反驳|论证|"
            r"逻辑.*漏洞|推理|辩证|第一性原理|苏格拉底|"
            r"反对.*理由|支持.*理由|利弊分析|优缺点分析|对比.*分析|"
            r"分析.*(?:可能性|可行性|影响|原因|趋势|前景|利弊|优缺点|风险|机会|本质|原理|影响)",
            text, re.I
        ):
            return "analytical"

        if re.search(r"计算|算一下|calc|=", text, re.I):
            return "calculate"
        if re.search(r"天气|气温|weather", text, re.I):
            return "weather"
        if re.search(r"几点|时间|now|time", text, re.I):
            return "time"
        if re.search(r"统计|字数", text, re.I):
            return "text_stats"
        # 文档读取意图（需插件市场）
        if re.search(r"\.pdf|读取pdf|解析pdf|pdf内容|read pdf", text, re.I):
            return "document_read"
        if re.search(r"\.docx?|读取word|读取docx|解析word|read docx", text, re.I):
            return "document_read"
        if re.search(r"\.xlsx?|读取excel|解析xlsx|读取表格|read excel", text, re.I):
            return "document_read"
        # 翻译意图（需插件市场）
        if re.search(r"翻译|translate|英文翻译|中文翻译", text, re.I):
            return "translate"
        # 二维码意图（需插件市场）
        if re.search(r"二维码|qrcode|生成码", text, re.I):
            return "qrcode"
        # 图片处理意图（需插件市场）
        if re.search(r"处理图片|缩放图片|裁剪图片|图片转格式|image process|\.(?:png|jpg|jpeg|gif|bmp)", text, re.I):
            return "image_process"
        # Markdown 渲染意图（需插件市场）
        if re.search(r"渲染markdown|md转|markdown转|渲染md", text, re.I):
            return "md_render"
        if re.search(r"你好|hello|hi|介绍", text, re.I):
            return "greeting"
        # 本地知识查询：含项目专属词（SCU2/CUF/架构/守卫/三级记忆等）→ 走 RAG 知识库
        # 这类问题本地知识库有权威答案，不应优先联网
        if re.search(r"SCU2|CUF|本系统|本程序|本架构|三级记忆|L1|L2|L3|守卫|D层|M层|W1|W2|熵税|阴阳|Pair|自进化|自修改|插件市场|向量库", text, re.I):
            return "knowledge_query"
        # 领域触发词自动成为 web_search 意图触发词
        # 这样"酒店/住宿/宾馆"等高意图词无需搜索动词也能触发联网搜索
        try:
            from domain_router import detect_domain
            detected_domain = detect_domain(text)
            if detected_domain != "general":
                return "web_search"
        except Exception:
            pass
        # 联网搜索意图：搜索/搜一下/查一下/最新/新闻/近期/2024/2025/2026/怎么样/是什么/是谁/多少钱/发生
        if re.search(r"搜索|搜一下|搜搜|查一下|查查|帮我查|search|google一下|百度一下|最新|最近|今日|今天.*新闻|近期|2024|2025|2026|现在是.*年|怎么样|是什么|是谁|多少钱|发生.*事|热点|热搜", text, re.I):
            return "web_search"
        return "conversation"
