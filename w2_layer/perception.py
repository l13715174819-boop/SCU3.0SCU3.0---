# -*- coding: utf-8 -*-
"""
W2 层：w2_layer/perception.py — 感知层
=======================================
最外层，直接接收用户输入。属于 CUF W2 层。
数据流：感知(W2) → 记忆(W1) 需经守卫①跨层审计。

感知流水线：防御 → 验证 → 归一化 → 脱敏 → 语言检测 → 意图识别 → 领域识别 → 写ctx
W2 是输入侧唯一清洗口，下游 M 层无需重复脱敏（通过 ctx["sanitized"]=True 标记判断）。
"""
import re
import unicodedata
import logging
from typing import Dict, Any, Tuple, List

from core.abc import PerceivableMixin, SanitizableMixin

logger = logging.getLogger("SCU3.w2.perception")

# 输入长度上限（超过则截断+告警）
_MAX_INPUT_LEN = 5000


class PerceptionLayer(PerceivableMixin, SanitizableMixin):
    """感知层 — 输入解析、清洗、脱敏与意图识别

    继承 PerceivableMixin/SanitizableMixin 获得接口契约。
    W2 是输入进入系统的唯一清洗口：验证→归一化→脱敏→语言→意图→领域。
    """

    def process(self, user_input: str, ctx: Dict[str, Any] = None,
                history: list = None) -> Dict[str, Any]:
        """解析用户输入（感知流水线入口）

        Args:
            user_input: 用户输入文本
            ctx: 上下文
            history: 最近对话历史（List[{"role":"user/assistant","content":"..."}]）
                     用于 LLM 语义推理时区分追问 vs 新话题

        流水线顺序：
            防御 → 验证 → 归一化 → 脱敏 → 语言检测 → 意图识别 → 领域识别 → 写ctx
        """
        ctx = ctx or {}
        history = history or []

        # ① 防御None/非字符串输入
        if user_input is None:
            user_input = ""
        if not isinstance(user_input, str):
            user_input = str(user_input)

        # ② 输入验证（长度/空输入/截断）
        validation = self._validate_input(user_input)
        if not validation["valid"]:
            ctx.update({"input": "", "perceived": "", "perceived_raw": "",
                        "intent": "conversation", "domain": "general",
                        "language": "unknown", "sanitized": False,
                        "perception_ok": False, "perception_reason": validation["reason"]})
            return ctx
        if validation["truncated"]:
            user_input = user_input[:_MAX_INPUT_LEN]
            logger.warning(f"输入过长已截断: original_len={validation['original_len']}")

        # ③ 输入归一化（全角半角统一/控制字符清理/空白合并）
        text = self._normalize_input(user_input)
        ctx["input"] = text
        ctx["perceived_raw"] = text  # 原始文本（脱敏前，仅供本地日志）

        # ④ PII/敏感信息脱敏（复用 guard.ContentFilter 50+规则）
        text_sanitized, sanitize_alerts = self.sanitize(text)
        ctx["perceived"] = text_sanitized  # 脱敏后文本（下游直接消费）
        ctx["sanitized"] = True
        ctx["sanitize_alerts"] = sanitize_alerts
        if sanitize_alerts:
            logger.info(f"感知层脱敏: {len(sanitize_alerts)} 项命中, alerts={sanitize_alerts[:3]}")

        # ⑤ 语言检测
        ctx["language"] = self._detect_language(text_sanitized)

        # ⑥ 意图识别
        ctx["intent"] = self._detect_intent(text_sanitized, history)

        # ⑦ 领域识别（全意图，不再仅限 web_search）
        ctx["domain"] = self._detect_domain(text_sanitized, ctx["intent"])

        ctx["perception_ok"] = True
        logger.info(f"感知层: intent={ctx['intent']}, domain={ctx.get('domain', 'general')}, "
                     f"lang={ctx['language']}, input={text_sanitized[:50]}")
        return ctx

    def perceive(self, user_input: str, ctx: Dict[str, Any] = None,
                 history: List[Dict] = None) -> Dict[str, Any]:
        """PerceivableMixin 契约实现：等价于 process()"""
        return self.process(user_input, ctx, history)

    # ═══════════════════════════════════════════════════════════
    #  输入验证
    # ═══════════════════════════════════════════════════════════

    def _validate_input(self, text: str) -> dict:
        """输入验证：空输入拒绝、长度上限截断

        Returns:
            {"valid": bool, "reason": str, "truncated": bool, "original_len": int}
        """
        original_len = len(text)
        if not text or not text.strip():
            return {"valid": False, "reason": "空输入", "truncated": False, "original_len": 0}
        if original_len > _MAX_INPUT_LEN:
            return {"valid": True, "reason": "长度超限已截断", "truncated": True, "original_len": original_len}
        return {"valid": True, "reason": "", "truncated": False, "original_len": original_len}

    # ═══════════════════════════════════════════════════════════
    #  输入归一化
    # ═══════════════════════════════════════════════════════════

    def _normalize_input(self, text: str) -> str:
        """输入归一化：全角半角统一、控制字符清理、多余空白合并

        从 w1_layer/action.py._smart_search_query 上移的清洗逻辑，
        消除 W1 对输入归一化的职责持有。
        """
        # 控制字符清理（保留换行和制表符）
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        # 全角空格→半角
        text = text.replace("\u3000", " ")
        # 连续空白合并为单个空格
        text = re.sub(r"[ \t]+", " ", text)
        # 去除首尾空白
        text = text.strip()
        return text

    # ═══════════════════════════════════════════════════════════
    #  PII/敏感信息脱敏（SanitizableMixin 实现）
    # ═══════════════════════════════════════════════════════════

    def sanitize(self, text: str) -> Tuple[str, List[str]]:
        """清洗输入：调用 guard.ContentFilter 做 PII 脱敏

        复用现有 50+ 正则规则（手机号/身份证/API key/密钥/内网IP/银行卡等），
        不重复造轮子。W2 统一脱敏一次，下游 M 层通过 ctx["sanitized"] 跳过重复脱敏。

        Returns:
            (脱敏后文本, 命中告警列表)
        """
        try:
            from guard.content_filter import ContentFilter
            cf = ContentFilter()
            filtered, alerts = cf.filter(text)
            return filtered, alerts or []
        except ImportError:
            logger.debug("guard.content_filter 不可用，跳过脱敏")
            return text, []
        except Exception as e:
            logger.warning(f"脱敏异常（不阻塞，返回原文）: {e}")
            return text, []

    def detect_pii(self, text: str) -> List[str]:
        """仅检测 PII 不替换，返回命中项列表"""
        try:
            from guard.content_filter import ContentFilter
            cf = ContentFilter()
            _, alerts = cf.filter(text)
            return alerts or []
        except Exception as e:
            logger.debug(f"PII检测异常: {e}")
            return []

    # ═══════════════════════════════════════════════════════════
    #  语言检测
    # ═══════════════════════════════════════════════════════════

    def _detect_language(self, text: str) -> str:
        """语言检测：基于 CJK 字符占比判断 zh/en/mixed

        简单高效（无需外部库），为后续多语言 prompt 路由、翻译意图提供依据。
        """
        if not text:
            return "unknown"
        cjk_count = 0
        ascii_count = 0
        for ch in text:
            cp = ord(ch)
            # CJK 统一表意文字范围
            if (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF) or \
               (0x20000 <= cp <= 0x2A6DF) or (0x3040 <= cp <= 0x30FF):
                cjk_count += 1
            elif ch.isascii() and ch.isalpha():
                ascii_count += 1
        total = cjk_count + ascii_count
        if total == 0:
            return "unknown"
        cjk_ratio = cjk_count / total
        if cjk_ratio > 0.6:
            return "zh"
        elif cjk_ratio < 0.2:
            return "en"
        return "mixed"

    # ═══════════════════════════════════════════════════════════
    #  领域识别（全意图解耦）
    # ═══════════════════════════════════════════════════════════

    def _detect_domain(self, text: str, intent: str) -> str:
        """领域识别：所有意图都识别（不再仅限 web_search）

        修复：原实现仅在 intent==web_search 时识别领域，导致
        hotel/medical/product 领域增强在 conversation/knowledge_query
        等意图下失效。现在全意图识别，由下游层按需消费。
        """
        try:
            from domain_router import detect_domain
            domain = detect_domain(text)
            return domain if domain else "general"
        except Exception as e:
            logger.debug(f"领域识别异常（不阻塞）: {e}")
            return "general"

    def _detect_intent(self, text: str, history: list = None) -> str:
        """意图识别（含插件市场触发意图、工作流自动触发）"""
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

        # ══ 工作流自动触发（优先级仅次于followup）══
        # 强信号词直接路由到对应预置工作流；宽松动词（"分析一下"等）默认走 research_report
        # 注意：必须在 analytical 之前判断，否则"分析"类词会被 analytical 吞掉
        wf_intent = self._detect_workflow_intent(text)
        if wf_intent:
            return wf_intent

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
        # 图片生成意图（AI文生图）
        if re.search(r"生成.*图片|画.*张|画.*幅|画.*个|生成.*图|创建.*图片|制作.*图片|画.*图|draw|generate.*image|文生图|AI画", text, re.I):
            return "image_generation"
        # 图片处理意图（需插件市场）
        if re.search(r"处理图片|缩放图片|裁剪图片|图片转格式|image process|\.(?:png|jpg|jpeg|gif|bmp)", text, re.I):
            return "image_process"
        # Markdown 渲染意图（需插件市场）
        if re.search(r"渲染markdown|md转|markdown转|渲染md", text, re.I):
            return "md_render"
        if re.search(r"你好|hello|hi|介绍", text, re.I):
            return "greeting"
        # 本地知识查询：含项目专属词（SCU3/CUF/架构/守卫/三级记忆等）→ 走 RAG 知识库
        # 这类问题本地知识库有权威答案，不应优先联网
        if re.search(r"SCU3|CUF|本系统|本程序|本架构|三级记忆|L1|L2|L3|守卫|D层|M层|W1|W2|熵税|阴阳|Pair|自进化|自修改|插件市场|向量库", text, re.I):
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

    # ═══════════════════════════════════════════════════════════
    #  工作流自动触发意图识别
    # ═══════════════════════════════════════════════════════════
    # 对话框输入 → 自动识别 → 路由到对应预置工作流
    # 触发策略：
    #   ① 强信号词：精确匹配工作流名称关键词，直接路由
    #   ② 宽松动词："分析一下/研究一下/写一篇" + 主题词 → 默认 research_report
    #   ③ 边界：纯动词无主题词不触发（如"分析一下"无后续内容），避免空跑
    # 返回 "workflow:<preset_id>" 或 None

    # 强信号词表（preset_id → 触发正则）
    WORKFLOW_STRONG_SIGNALS = {
        "research_report": r"深度研究|研究报告|全面调研|深度调研|专题研究|研究一下|调研一下|深入调研",
        "code_solution": r"完整代码方案|代码方案|实现方案|技术方案|写个方案|给个方案|完整方案",
        "decision_analysis": r"决策分析|帮我决策|帮我做决定|决策一下|决定一下|帮我选择|选择分析",
        "content_creation": r"创作一篇|写一篇.{0,4}文章|写篇文章|创作内容|写一篇.{0,4}文案|写个文案",
        "bug_investigation": r"排查bug|调试问题|排查问题|bug排查|调试bug|定位bug|排查一下",
        "learning_path": r"学习路径|学习路线|系统学习|学习计划|学习规划|怎么学|学习指南",
    }

    # 宽松动词：+ 主题词（≥2个非动词字符）→ 默认 research_report
    # 覆盖"分析一下XX"、"研究XX"、"写一下XX"等泛化表达
    WORKFLOW_LOOSE_VERBS = r"分析一下|研究一下|调研一下|写一下|了解一下|梳理一下|梳理下|整理一下|整理下|探讨一下|讨论一下"

    def _detect_workflow_intent(self, text: str, history: list = None) -> str:
        """识别工作流触发意图，返回 'workflow:<preset_id>' 或 ''

        三层触发策略：
        ① 强信号词精确路由（零成本，零延迟）
        ② 宽松动词 + 主题词 → research_report（零成本，零延迟）
        ③ LLM 完整语义推理（正则未命中时兜底，支持纯主题输入如"Python异步编程的优势"）
        """
        text = text.strip()
        if not text or len(text) < 4:
            return ""

        # ① 强信号词精确路由
        for preset_id, pattern in self.WORKFLOW_STRONG_SIGNALS.items():
            if re.search(pattern, text, re.I):
                logger.info(f"工作流强信号触发: preset={preset_id}, text={text[:40]}")
                return f"workflow:{preset_id}"

        # ② 宽松动词 + 主题词 → research_report（最通用）
        # 提取动词后的主题（去掉动词前缀和语气词），主题长度≥2才触发
        m = re.search(rf"(?:{self.WORKFLOW_LOOSE_VERBS})(.+)", text, re.I)
        if m:
            topic = m.group(1).strip()
            # 去除"的/了/吧/呢/啊"等语气词后的有效主题
            topic = re.sub(r"^[的了吧呢啊哈]+", "", topic).strip()
            if len(topic) >= 2:
                logger.info(f"工作流宽松触发: preset=research_report, topic={topic[:30]}, text={text[:40]}")
                return "workflow:research_report"

        # ③ LLM 完整语义推理（正则未命中兜底）
        # 覆盖纯主题输入（如"Python异步编程的优势"、"量子计算前景"）
        # 上下文感知：传入历史，LLM 能区分追问 vs 新话题
        llm_intent = self._detect_workflow_intent_llm(text, history or [])
        if llm_intent:
            return llm_intent

        return ""

    # ═══════════════════════════════════════════════════════════
    #  LLM 完整语义推理触发（第三层兜底）
    # ═══════════════════════════════════════════════════════════
    # 与助手自身工作方式一致：每轮 LLM 推理意图，覆盖正则无法匹配的纯主题输入
    # 上下文感知：传入最近对话历史，能区分"追问"和"新话题"
    # 成本：单次轻量调用（max_tokens=200, temperature=0.1），约0.5-1s
    # 降级：LLM 调用失败或返回不需要 → 返回 ""，走普通对话

    # 可用预置工作流（供 LLM 选择）
    AVAILABLE_PRESETS = ["research_report", "code_solution", "decision_analysis",
                         "content_creation", "bug_investigation", "learning_path"]

    def _detect_workflow_intent_llm(self, text: str, history: list) -> str:
        """LLM 完整语义推理：判断是否需要工作流，返回 'workflow:<preset_id>' 或 ''

        策略：
        - 文本长度≥6 才调用（避免对短输入浪费 token）
        - 传入最近3轮历史，LLM 能识别追问（"详细说说"）vs 新话题
        - LLM 返回 JSON: {"need_workflow": bool, "preset": str, "topic": str}
        - 失败降级：返回 ""，走普通对话
        """
        # 短文本不调用 LLM（"你好"、"谢谢"等）
        if len(text) < 6:
            return ""

        # 构建系统提示词
        system_prompt = (
            "你是意图分类器。判断用户输入是否需要调用预置工作流进行深度多步处理。\n\n"
            "可用工作流：\n"
            "- research_report: 深度研究报告（需搜集资料+分析+撰写完整报告）\n"
            "- code_solution: 完整代码方案（需调研+实现+审查）\n"
            "- decision_analysis: 决策分析（需搜集信息+利弊分析+建议）\n"
            "- content_creation: 内容创作（需素材+撰写+润色）\n"
            "- bug_investigation: Bug排查（需搜索+定位+修复方案）\n"
            "- learning_path: 学习路径规划（需资源+评估+计划）\n\n"
            "判断标准：\n"
            "✓ 需要：用户明确要求深度处理，或主题复杂需多步处理（如'Python异步编程的优势'需搜集+分析+撰写）\n"
            "✗ 不需要：追问/修正（'详细说说'、'再深入'）、简单问答（'你好'、'Python是什么'）、"
            "闲聊、单步可完成的任务\n\n"
            "结合对话历史判断：若用户是在追问上文，返回 need_workflow=false\n\n"
            "只返回JSON，不要其他文字：\n"
            '{"need_workflow": true, "preset": "research_report", "topic": "用户主题"}\n'
            '或 {"need_workflow": false, "preset": "", "topic": ""}'
        )

        # 构建历史上下文（最近3轮）
        history_text = ""
        if history:
            recent = history[-6:]  # 最多3轮（6条消息）
            lines = []
            for msg in recent:
                role = msg.get("role", "")
                content = msg.get("content", "")[:100]
                if role and content:
                    label = "用户" if role == "user" else "助手"
                    lines.append(f"{label}: {content}")
            if lines:
                history_text = "对话历史:\n" + "\n".join(lines) + "\n\n"

        user_prompt = f"{history_text}当前输入: {text}\n\n请判断:"

        try:
            from m_layer.llm_client import get_client
            client = get_client()
            result = client.chat(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.1,      # 低温度保证稳定
                max_tokens=200,       # 短输出
            )

            if result.get("error"):
                logger.debug(f"LLM意图推理失败: {result['error']}")
                return ""

            content = result.get("content", "").strip()
            if not content:
                return ""

            # 解析 JSON（LLM 可能返回带 ```json 包裹的格式）
            import json
            # 去除可能的 markdown 代码块包裹
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)

            data = json.loads(content)

            if not data.get("need_workflow", False):
                logger.info(f"LLM意图推理: 不需要工作流, reason={data.get('topic', '')[:30]}")
                return ""

            preset = data.get("preset", "").strip()
            if preset not in self.AVAILABLE_PRESETS:
                logger.warning(f"LLM意图推理: 未知preset={preset}, 降级到research_report")
                preset = "research_report"

            logger.info(f"LLM意图推理触发: preset={preset}, text={text[:40]}")
            return f"workflow:{preset}"

        except json.JSONDecodeError as e:
            logger.debug(f"LLM意图推理JSON解析失败: {e}, content={content[:100] if 'content' in dir() else ''}")
            return ""
        except Exception as e:
            logger.debug(f"LLM意图推理异常（降级到普通对话）: {e}")
            return ""
