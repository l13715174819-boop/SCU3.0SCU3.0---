# -*- coding: utf-8 -*-
"""
M 层：m_layer/cognition.py — 认知层
=====================================
推理与回复生成。与元认知层同属 M，流动免审。
数据流：执行(W1) → 认知(M) 需经守卫②跨层审计。

任务2.1：接入DeepSeek LLM客户端
"""
import os
import sys
import logging
from typing import Dict, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from m_layer.llm_client import get_client

logger = logging.getLogger("scu2.m.cognition")


class CognitionLayer:
    """认知层 — 推理与回复生成（接入LLM）"""

    def __init__(self):
        self.llm = get_client()

    def process(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """生成回复（强制完整流程：RAG + 工具结果 综合注入 LLM）

        综合策略（AND 关系，非互斥）：
          - web_search 成功：search_context + deep_crawl + rag_context 综合注入
          - web_crawl 成功：crawl_context + rag_context 综合注入
          - 其他工具成功：工具结果格式化输出（确定性工具走快速路径）
          - 工具全失败：rag_context + LLM 常规对话
          - 无工具调用：rag_context + LLM 生成回复（闲聊也注入RAG）
        """
        intent = ctx.get("intent", "conversation")
        tool_result = ctx.get("tool_result")
        user_input = ctx.get("perceived", "")
        context = ctx.get("rag_context", "")  # RAG上下文（任务2.2提供）
        recalled = ctx.get("recalled", [])  # 召回的对话历史

        # 方案C：analytical 意图走阴阳对子思考（基类版）
        # 阴用DeepSeek（严谨），阳用Qwen（发散），双签合一
        # 不触发Pair硬约束（认知思考非高风险），软双签作质量参考
        if intent == "analytical" and not tool_result:
            try:
                yy_result = self._yin_yang_think(user_input, context)
                if yy_result:
                    ctx["response"] = yy_result["response"]
                    ctx["yin_yang"] = yy_result["state"]
                    ctx["cognition_ok"] = True
                    ctx["llm_mode"] = self.llm.mode
                    logger.info(f"阴阳对子思考完成: γ_yin={yy_result['state']['gamma_yin']}, "
                                f"γ_yang={yy_result['state']['gamma_yang']}, "
                                f"endorsed={yy_result['state']['endorsed']}")
                    return ctx
            except Exception as e:
                logger.warning(f"阴阳对子思考失败，降级到原流程: {e}")

        if tool_result and tool_result.get("success"):
            result = tool_result.get("result", {})
            tool_name = tool_result.get("tool", "")
            # 联网搜索：搜索结果 + 深度爬取 + RAG知识库 综合注入
            if tool_name == "web_search":
                search_context = self._format_search_context(result)
                # 深度爬取：对Top 2结果抓取正文（失败不影响主流程）
                deep_context = self._deep_crawl_search_results(result, max_pages=2)
                if deep_context:
                    search_context = search_context + "\n\n" + deep_context
                # 综合注入RAG知识库上下文（AND关系，非互斥）
                if context:
                    search_context += f"\n\n【本地知识库参考】\n{context}"
                    logger.info("综合注入: web_search结果 + RAG知识库")
                ctx["response"] = self._generate_llm_response(
                    user_input, search_context, "web_search", recalled
                )
            elif tool_name == "web_crawl":
                # 全网爬取结果 + RAG知识库 综合注入
                crawl_context = self._format_crawl_context(result)
                if context:
                    crawl_context += f"\n\n【本地知识库参考】\n{context}"
                    logger.info("综合注入: web_crawl结果 + RAG知识库")
                ctx["response"] = self._generate_llm_response(
                    user_input, crawl_context, "web_search", recalled
                )
            else:
                # 确定性工具（calculator/time_now/weather等）：格式化结果输出
                # 信息查询类意图也注入RAG（如 weather + 本地知识库注意事项）
                tool_response = self._format_tool_result(tool_name, result)
                if context and intent in ("knowledge_query", "conversation", "greeting", "web_search"):
                    ctx["response"] = self._generate_llm_response(
                        user_input,
                        f"{tool_response}\n\n【本地知识库参考】\n{context}",
                        intent, recalled
                    )
                else:
                    ctx["response"] = tool_response
        elif tool_result and tool_result.get("all_failed"):
            # 降级策略1：所有工具都失败 → 尝试插件市场自动获取能力
            tried_tools = tool_result.get("tried_tools", [])
            logger.warning(f"工具全部失败(tried={tried_tools}), 尝试插件市场")
            plugin_handled = self._try_plugin_market(user_input, tried_tools, ctx, recalled)
            if not plugin_handled:
                # 降级策略2：插件市场也无法解决 → RAG + LLM常规对话
                logger.warning(f"插件市场未能解决, 降级到LLM常规对话(注入RAG)")
                ctx["response"] = self._generate_llm_response(user_input, context, "conversation", recalled)
                ctx["fallback"] = True
                ctx["fallback_reason"] = "tools_all_failed"
        elif intent == "web_search" and not tool_result:
            # 兜底：感知层判为联网搜索意图，但执行层未触发工具（正则遗漏等）
            # 主动补一次 web_search，避免 LLM 凭空说"不能实时联网"
            domain = ctx.get("domain", "general")
            ctx["response"] = self._fallback_web_search(user_input, recalled, domain)
            ctx["fallback"] = True
            ctx["fallback_reason"] = "intent_web_search_no_tool"
        else:
            # 无工具调用：RAG上下文 + LLM生成回复（闲聊也注入RAG，强制完整流程）
            ctx["response"] = self._generate_llm_response(user_input, context, intent, recalled)

        ctx["cognition_ok"] = True
        ctx["llm_mode"] = self.llm.mode
        logger.info(f"认知层: intent={intent}, mode={self.llm.mode}, rag={bool(context)}, "
                    f"history={len(recalled)}, fallback={ctx.get('fallback', False)}, "
                    f"response={ctx['response'][:50]}")
        return ctx

    def _format_tool_result(self, tool_name: str, result: Dict) -> str:
        """格式化工具结果（支持13种工具）"""
        if tool_name == "calculator":
            return f"计算结果：{result.get('expression', '')} = {result.get('result', '?')}"
        if tool_name == "weather":
            return (f"{result.get('city', '')}天气：{result.get('temp', '?')}，"
                    f"{result.get('desc', '')}，湿度 {result.get('humidity', '?')}，"
                    f"风速 {result.get('wind', '?')}")
        if tool_name == "time_now":
            return f"当前时间：{result.get('time', '?')}"
        if tool_name == "text_stats":
            return (f"文本统计：{result.get('chars', 0)} 字符，"
                    f"{result.get('words', 0)} 词，{result.get('lines', 0)} 行")
        if tool_name == "datetime_calc":
            return f"日期计算：{result.get('start', '')} + {result.get('days', 0)}天 = {result.get('result', '?')}"
        if tool_name == "unit_convert":
            return f"单位换算：{result.get('value', '')} {result.get('from', '')} = {result.get('result', '')} {result.get('to', '')}"
        if tool_name == "exchange_rate":
            rates = result.get("rates", {})
            return f"汇率查询：{result.get('base', '')} → " + ", ".join(f"{k}={v}" for k, v in rates.items())
        if tool_name == "crypto_price":
            prices = result.get("prices", {})
            return "加密货币价格：" + ", ".join(f"{k}=${v}" for k, v in prices.items())
        if tool_name == "stock_price":
            return f"股票行情：{result.get('name', result.get('code', ''))} 现价 {result.get('price', '?')}"
        if tool_name == "github_search":
            repos = result.get("repos", [])
            return f"GitHub搜索：找到{len(repos)}个仓库\n" + "\n".join(f"  - {r.get('full_name', '')} ⭐{r.get('stars', 0)}" for r in repos[:5])
        if tool_name == "file_read":
            return f"文件内容（{result.get('size', 0)}字节）：\n{result.get('content', '')[:500]}"
        if tool_name == "file_write":
            return f"文件已写入：{result.get('path', '')}（{result.get('written', 0)}字符）"
        if tool_name == "code_run":
            return f"代码执行结果：\n{result.get('output', '')}"
        return f"工具结果：{result}"

    def _try_plugin_market(self, user_input: str, failed_tools: list,
                          ctx: Dict[str, Any], recalled: list) -> bool:
        """尝试从插件市场自动下载+加载插件解决当前需求

        闭环：能力匹配 → 下载安装 → 加载注册 → 重试工具 → 用完卸载

        Returns:
            True 表示插件成功解决问题，False 表示未能解决
        """
        try:
            from m_layer.plugin_market import get_marketplace
            market = get_marketplace()

            # 1. 能力匹配
            failed_tool = failed_tools[0] if failed_tools else ""
            plugin_info = market.match_capability(user_input, failed_tool)
            if plugin_info is None:
                logger.info(f"插件市场无匹配插件: input={user_input[:30]}")
                return False

            plugin_name = plugin_info["name"]
            logger.info(f"插件市场匹配到: {plugin_name}, 开始安装加载")

            # 2. 安装并加载
            load_result = market.install_and_load(plugin_name)
            if not load_result.get("success"):
                logger.warning(f"插件 {plugin_name} 加载失败: {load_result.get('error')}")
                return False

            tools = load_result.get("tools", [])
            logger.info(f"插件 {plugin_name} 加载成功，提供工具: {tools}")

            # 3. 重试原任务（用新加载的工具）
            retry_success = False
            retry_result = None
            try:
                from w1_layer.action import ActionLayer
                action = ActionLayer()
                # 重新检测工具（此时新工具已注册）
                tool_info = action.detect_tool(user_input)
                if tool_info and tool_info["tool"] in tools:
                    # 直接执行新工具
                    retry_result = action.execute(tool_info)
                    if retry_result.get("success"):
                        retry_success = True
                        logger.info(f"插件工具重试成功: {tool_info['tool']}")
            except Exception as e:
                logger.warning(f"插件工具重试异常: {e}")

            # 4. 生成回复
            if retry_success and retry_result:
                result = retry_result.get("result", {})
                tool_name = retry_result.get("tool", "")
                # 格式化插件工具结果
                ctx["response"] = self._format_plugin_result(tool_name, result, plugin_name)
                ctx["plugin_used"] = plugin_name
                ctx["plugin_tools"] = tools

                # 经验沉淀：记录成功路径，下次直接预加载
                self._sink_experience(user_input, plugin_name, tool_name, ctx.get("intent", ""))
            else:
                # 插件加载但工具重试失败 → 用 LLM 基于用户输入生成回复
                ctx["response"] = self._generate_llm_response(user_input, "", "conversation", recalled)
                ctx["plugin_used"] = plugin_name
                ctx["plugin_retry_failed"] = True

                # 记录失败经验，避免下次重复尝试
                self._sink_failure(user_input, plugin_name, tools[0] if tools else "")

            # 5. 用完自动卸载（TTL 机制由市场后台处理，此处可立即卸载）
            # 注意：保留加载状态让 TTL 检查器处理，避免高频使用时反复加载
            # 如需立即卸载，调用 market.unload_after_use(plugin_name)

            return True

        except Exception as e:
            logger.error(f"插件市场流程异常: {e}", exc_info=True)
            return False

    def _sink_experience(self, user_input: str, plugin_name: str,
                         tool_name: str, intent: str):
        """经验沉淀：记录成功的插件使用路径

        下次相同 pattern 的请求会直接预加载该插件，跳过 all_failed 流程。
        """
        try:
            from m_layer.experience_store import get_experience_store
            exp_store = get_experience_store()
            result = exp_store.record_success(user_input, plugin_name, tool_name, intent)
            logger.info(f"经验沉淀: {result.get('message', 'N/A')}")
        except Exception as e:
            logger.debug(f"经验沉淀异常（不阻塞）: {e}")

    def _sink_failure(self, user_input: str, plugin_name: str, tool_name: str):
        """记录失败经验，避免下次重复尝试

        副作用：失败经验达到阈值时触发自进化扫描（异步，不阻塞响应）
        """
        try:
            from m_layer.experience_store import get_experience_store
            exp_store = get_experience_store()
            exp_store.record_failure(user_input, plugin_name, tool_name)
            logger.info(f"失败经验已记录: {plugin_name}/{tool_name}")

            # 触发自进化扫描（异步，不阻塞用户响应）
            self._maybe_trigger_evolution()
        except Exception as e:
            logger.debug(f"失败经验记录异常: {e}")

    def _maybe_trigger_evolution(self):
        """检查是否需要触发自进化扫描（异步执行，不阻塞）"""
        try:
            import threading
            from m_layer.experience_store import get_experience_store
            from m_layer.self_evolution import TRIGGER_FAIL_COUNT

            exp_store = get_experience_store()
            # 检查是否有失败经验达到阈值
            experiences = exp_store.list_experiences()
            has_critical = any(
                e.get("fail_count", 0) >= TRIGGER_FAIL_COUNT and e.get("success_count", 0) == 0
                for e in experiences
            )
            if has_critical:
                # 异步触发，不阻塞当前响应
                def _async_scan():
                    try:
                        from m_layer.self_evolution import get_evolution_engine
                        engine = get_evolution_engine()
                        engine.run_scan()
                    except Exception as ex:
                        logger.debug(f"异步自进化扫描异常: {ex}")
                thread = threading.Thread(target=_async_scan, daemon=True)
                thread.start()
                logger.info("失败经验达到阈值，已异步触发自进化扫描")
        except Exception as e:
            logger.debug(f"自进化触发检查异常: {e}")

    def _format_plugin_result(self, tool_name: str, result: Dict, plugin_name: str) -> str:
        """格式化插件工具的执行结果"""
        if "error" in result and not result.get("content"):
            return f"（插件 {plugin_name} 执行失败: {result['error']}）"

        if tool_name == "pdf_read":
            content = result.get("content", "")
            pages = result.get("pages", 0)
            return f"已通过插件 {plugin_name} 读取PDF（共{pages}页）：\n{content[:3000]}"
        if tool_name == "docx_read":
            content = result.get("content", "")
            paragraphs = result.get("paragraphs", 0)
            return f"已通过插件 {plugin_name} 读取Word文档（共{paragraphs}段）：\n{content[:3000]}"
        if tool_name == "excel_read":
            content = result.get("content", "")
            sheets = result.get("sheets", [])
            return f"已通过插件 {plugin_name} 读取Excel（工作表: {sheets}）：\n{content[:3000]}"
        if tool_name == "qrcode_gen":
            saved = result.get("saved_to", "")
            return f"已通过插件 {plugin_name} 生成二维码，保存到: {saved}"
        if tool_name == "image_process":
            return f"已通过插件 {plugin_name} 处理图片: {result}"
        if tool_name == "translate":
            translated = result.get("translated", "")
            return f"已通过插件 {plugin_name} 翻译结果: {translated}"
        if tool_name == "md_render":
            output = result.get("output", "")
            return f"已通过插件 {plugin_name} 渲染Markdown:\n{output[:3000]}"

        return f"（插件 {plugin_name} 工具 {tool_name} 结果）: {result}"

    def _fallback_web_search(self, user_input: str, recalled: list, domain: str = "general") -> str:
        """兜底联网搜索：感知层判为 web_search 意图但执行层未触发工具时调用

        主动用 user_input 作为 query 执行一次 web_search + 深度爬取，再注入 LLM。
        搜索失败则如实告知，绝不返回"不能实时联网"。
        domain 非空时透传给 _tool_web_search 触发领域增强。
        """
        try:
            from w1_layer.action import ActionLayer
            action = ActionLayer()
            # 用用户原句作为查询词（清理助词）
            import re
            query = re.sub(r"[？?！!。，,了|的吗|呢|啊|呀]", " ", user_input).strip()[:80]
            if not query:
                query = user_input[:80]
            logger.info(f"认知层兜底联网搜索: query={query}, domain={domain}")
            tool_result = action.execute({
                "tool": "web_search",
                "params": {"query": query, "domain": domain},
                "tool_type": "read",
                "domain": domain,
            })
            if tool_result.get("success"):
                result = tool_result.get("result", {})
                search_context = self._format_search_context(result)
                deep_context = self._deep_crawl_search_results(result, max_pages=2)
                if deep_context:
                    search_context = search_context + "\n\n" + deep_context
                return self._generate_llm_response(user_input, search_context, "web_search", recalled)
            else:
                # 搜索工具本身失败 → 如实说明，但仍不说"不能联网"，而是说"此次未获取到"
                err = tool_result.get("error", "未知错误")
                logger.warning(f"认知层兜底搜索失败: {err}")
                return (f"已尝试联网搜索「{query}」，但此次未能获取到结果（{err}）。"
                        f"建议稍后重试，或换用更具体的关键词。")
        except Exception as e:
            logger.error(f"认知层兜底联网搜索异常: {e}", exc_info=True)
            return f"联网搜索过程中出现异常：{e}。建议稍后重试。"

    def _format_search_context(self, result: Dict) -> str:
        """将联网搜索结果格式化为LLM上下文

        领域增强（v2）：若搜索结果含 fields（结构化字段），优先展示字段；
        白名单源标记★，便于 LLM 区分权威源。
        """
        results = result.get("results", [])
        if not results:
            return "联网搜索未返回结果。"
        domain = result.get("domain", "general")
        lines = [f"以下是联网搜索到的最新信息（领域={domain}，请基于这些信息回答用户，并在末尾附上来源链接）："]
        for i, r in enumerate(results[:5], 1):
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            url = r.get("url", "")
            fields = r.get("fields", {})
            whitelisted = r.get("is_whitelisted", False)
            star = "★" if whitelisted else " "
            # 结构化字段优先展示
            if fields:
                field_str = " | ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
                lines.append(f"{i}.{star} {title}\n   [{field_str}]\n   {snippet}\n   来源: {url}")
            else:
                # 无领域字段时，展示百度解析出的价格/评分（避免关键信息丢失）
                extras = []
                if r.get("prices"):
                    extras.append("价格=" + "/".join(f"￥{p}" for p in r["prices"]))
                if r.get("ratings"):
                    extras.append("评分=" + "/".join(f"{rt}分" for rt in r["ratings"]))
                extra_str = f"   [{ ' | '.join(extras) }]\n" if extras else ""
                lines.append(f"{i}.{star} {title}\n{extra_str}   {snippet}\n   来源: {url}")
        return "\n".join(lines)

    def _deep_crawl_search_results(self, search_result: Dict, max_pages: int = 2) -> str:
        """深度爬取：对搜索结果的Top N页面抓取正文（失败不影响主流程）

        领域增强（v2）：白名单源优先爬取，爬取后再次提取结构化字段
        """
        results = search_result.get("results", [])
        if not results:
            return ""
        domain = search_result.get("domain", "general")
        try:
            from w1_layer.action import ActionLayer
            action = ActionLayer()
            # 领域路由器（可选）
            router = None
            if domain and domain != "general":
                try:
                    from domain_router import get_router
                    router = get_router()
                except Exception:
                    router = None

            # 白名单源优先排序（已重排过，但这里再次确保深度爬取白名单源）
            sorted_results = sorted(results, key=lambda r: 0 if r.get("is_whitelisted") else 1)

            crawled = []
            for r in sorted_results[:max_pages]:
                url = r.get("url", "")
                if not url:
                    continue  # 仅跳过空URL（mu属性解析后已是真实URL，无需跳过百度链接）
                crawl_result = action._tool_web_crawl(url, max_length=3000)
                if crawl_result.get("content"):
                    title = crawl_result.get("title", r.get("title", ""))
                    content = crawl_result["content"][:2000]
                    # 二次字段提取（从正文）
                    extra_fields = ""
                    if router:
                        fields = router.extract_fields(content, domain)
                        if fields:
                            extra_fields = " | " + " | ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
                    tag = "★权威源" if r.get("is_whitelisted") else "深度内容"
                    crawled.append(f"【{tag}{extra_fields}】{title}\n来源: {url}\n{content}")
                    logger.info(f"深度爬取成功[{domain}]: {url[:50]}")
            if crawled:
                return "\n\n".join(crawled)
        except Exception as e:
            logger.debug(f"深度爬取失败（不影响主流程）: {e}")
        return ""

    def _format_crawl_context(self, result: Dict) -> str:
        """将爬取结果格式化为LLM上下文"""
        content = result.get("content", "")
        title = result.get("title", "")
        url = result.get("url", "")
        if not content:
            return f"爬取页面失败: {result.get('error', '未知错误')}"
        lines = [f"以下是爬取到的网页内容（请基于这些内容回答用户）："]
        if title:
            lines.append(f"页面标题: {title}")
        lines.append(f"来源: {url}")
        lines.append(f"\n{content}")
        return "\n".join(lines)

    def _generate_llm_response(self, user_input: str, context: str, intent: str, history: list = None) -> str:
        """通过LLM生成回复（支持多轮对话上下文）

        隐私保护（P1修复）：调用云端LLM前对 user_input/context/history 做PII脱敏，
        防止手机号/身份证/密钥/内网IP等敏感信息出境。

        上下文强化（方案2）：followup 意图时，将最近1轮问答显式注入context，
        强化LLM对对话历史的引用，解决追问场景上下文丢失问题。
        """
        # P1修复：LLM输入侧脱敏（防止PII/密钥泄露给云端LLM）
        try:
            from guard.content_filter import ContentFilter
            _cf = ContentFilter()
            user_input, _ = _cf.filter(user_input)
            if context:
                context, _ = _cf.filter(context)
        except Exception as _e:
            logger.debug(f"输入脱敏跳过(非阻断): {_e}")

        # 方案2：followup 意图时，将最近1轮问答显式注入context，强化上下文引用
        if intent == "followup" and history:
            recent = history[-2:] if len(history) >= 2 else history
            context_reminder = "【对话上下文提醒】\n"
            for h in recent:
                if h.get("input"):
                    context_reminder += f"用户曾问：{h['input'][:200]}\n"
                if h.get("response"):
                    context_reminder += f"你曾答：{h['response'][:300]}\n"
            context = (context_reminder + "\n" + context) if context else context_reminder
            logger.info(f"followup意图: 注入最近{len(recent)}条历史作为上下文提醒")

        # 选择系统提示词
        system_prompt = "default"
        if intent == "coding":
            system_prompt = "coding"
        elif intent == "analytical":
            system_prompt = "analytical"
        elif intent == "web_search":
            system_prompt = "web_search"
        elif intent == "knowledge":
            system_prompt = "knowledge"
        elif intent == "followup":
            system_prompt = "followup"

        # 构建历史消息列表（最多取最近5轮，同步脱敏）
        history_messages = []
        if history:
            try:
                _cf_h = ContentFilter() if '_cf' not in dir() else _cf
            except Exception:
                _cf_h = None
            for h in history[-10:]:  # 最近10条（5轮对话）
                if h.get("input"):
                    h_in = h["input"]
                    if _cf_h:
                        try:
                            h_in, _ = _cf_h.filter(h_in)
                        except Exception:
                            pass
                    history_messages.append({"role": "user", "content": h_in})
                if h.get("response"):
                    h_out = h["response"]
                    if _cf_h:
                        try:
                            h_out, _ = _cf_h.filter(h_out)
                        except Exception:
                            pass
                    history_messages.append({"role": "assistant", "content": h_out})

        # 调用LLM
        result = self.llm.chat(
            prompt=user_input,
            system_prompt=system_prompt,
            context=context,
            history=history_messages,
        )
        return result.get("content", "（无回复）")

    # ─── 方案C：阴阳对子思考 ────────────────────────────────

    def _yin_yang_think(self, question: str, context: str) -> Dict[str, Any]:
        """阴阳对子思考主流程（方案C：基类版）

        流程：
        1. 阴方思考（DeepSeek-Chat，批判视角）
        2. 阳方思考（Qwen-Plus，支持视角，失败回退DeepSeek）
        3. 双签判定（CognitionEndorser.endorse）
        4. 太极合一（DeepSeek-Chat，综合阴阳）

        多路由：阴用DeepSeek（严谨），阳用Qwen（发散）
        不触发Pair硬约束（认知思考非高风险）

        Returns:
            {"response": str, "state": dict} 或 None（失败时）
        """
        from m_layer.cognition_endorser import get_cognition_endorser

        endorser = get_cognition_endorser()

        # P1修复：脱敏
        try:
            from guard.content_filter import ContentFilter
            _cf = ContentFilter()
            question, _ = _cf.filter(question)
            if context:
                context, _ = _cf.filter(context)
        except Exception:
            pass

        # 1. 阴方思考（DeepSeek-Chat，批判视角）
        yin_prompt = ("你是严谨的批判者（阴方）。对给定问题，找出漏洞、风险、反对理由。"
                      "至少给出3条具体反对意见，每条附带论证。不要附和。")
        yin_resp = self.llm.chat(
            prompt=question, system_prompt=yin_prompt, context=context
        )
        yin_view = yin_resp.get("content", "")
        logger.info(f"阴方思考完成: {len(yin_view)}字")

        # 2. 阳方思考（Qwen-Plus，支持视角，失败回退DeepSeek）
        yang_prompt = ("你是积极的支持者（阳方）。对给定问题，找出优势、机会、支持理由。"
                       "至少给出3条具体支持意见，每条附带论证。不要否定。")
        yang_view = ""
        try:
            yang_resp = self.llm.chat_multiplatform(
                prompt=question, platform="qwen",
                system_prompt=yang_prompt
            )
            yang_view = yang_resp.get("content", "")
            logger.info(f"阳方思考完成(Qwen): {len(yang_view)}字")
        except Exception as e:
            logger.warning(f"Qwen切换失败，阳方回退DeepSeek: {e}")
            yang_resp = self.llm.chat(
                prompt=question, system_prompt=yang_prompt, context=context
            )
            yang_view = yang_resp.get("content", "")
            logger.info(f"阳方思考完成(DeepSeek回退): {len(yang_view)}字")

        # 3. 双签判定（通过基类 endorse）
        proposal = {
            "yin_view": yin_view,
            "yang_view": yang_view,
            "question": question,
        }
        endorsement = endorser.endorse(proposal)

        # 4. 太极合一（融合阴阳，形成自身独立观点）
        # 核心改动：不复读阴阳观点，而是让LLM消化两方论证后形成自身判断
        synthesis_prompt = (
            f"问题：{question}\n\n"
            f"以下是内部思考过程中收集的两方论证素材（仅供你参考，不要在回复中提及阴方、阳方或复述这些素材）：\n"
            f"【素材A·批判视角】\n{yin_view}\n\n"
            f"【素材B·支持视角】\n{yang_view}\n\n"
            f"现在请作为独立思考者，给出你自己的综合判断。要求：\n"
            f"1. 不要复述素材A或素材B的内容，不要出现阴方、阳方等字眼\n"
            f"2. 形成你自己的观点，明确表态（支持/反对/有条件支持）\n"
            f"3. 用流畅的自然语言论述，像专家写给读者的分析文章\n"
            f"4. 结构清晰：核心观点 → 关键论据 → 边界条件/适用场景\n"
            f"5. 不要用过多加粗、列表等markdown符号，保持纯文本可读性\n"
            f"6. 篇幅控制在500-800字"
        )
        synthesis_resp = self.llm.chat(
            prompt=synthesis_prompt, system_prompt="你是一位深度分析专家，擅长在矛盾观点中找到平衡与真相。", context=""
        )
        synthesis = synthesis_resp.get("content", "")
        logger.info(f"太极合一完成: {len(synthesis)}字")

        # 最终输出：只输出综合结论，双签状态作为元数据由前端顶栏展示
        response = synthesis

        return {
            "response": response,
            "state": {
                "gamma_yin": endorsement["gamma_yin"],
                "gamma_yang": endorsement["gamma_yang"],
                "yin_passed": endorsement["yin_passed"],
                "yang_passed": endorsement["yang_passed"],
                "endorsed": endorsement["endorsed"],
            },
        }
