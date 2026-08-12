# -*- coding: utf-8 -*-
"""
domain_router.py — 领域路由器
==============================
识别用户查询所属领域，加载对应插件配置，注入到通用搜索管线。

职责：
  1. detect(text) — 基于关键词识别领域（hotel/product/medical/general）
  2. load(name)   — 加载领域插件 JSON 配置
  3. enhance_query(text, domain) — 按领域规则增强查询词
  4. extract_fields(content, domain) — 按 Schema 从爬取正文提取结构化字段
  5. rank_results(results, domain) — 按白名单/字段命中重排搜索结果

架构归属：W1 层辅助模块（被 action.py 调用，无跨层流动）
"""
import os
import re
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("SCU3.w1.domain_router")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(BASE_DIR, "domain_plugins")


class DomainRouter:
    """领域路由器 — 单例（配置只加载一次）"""

    _instance = None
    _plugins: Dict[str, Dict] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._plugins = {}
        self._load_all()
        logger.info(f"DomainRouter 初始化完成，已加载领域: {list(self._plugins.keys())}")

    def _load_all(self):
        """加载 domain_plugins/ 下所有 JSON 配置"""
        if not os.path.isdir(PLUGIN_DIR):
            logger.warning(f"领域插件目录不存在: {PLUGIN_DIR}")
            return
        for fname in os.listdir(PLUGIN_DIR):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(PLUGIN_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                name = cfg.get("name", fname[:-5])
                self._plugins[name] = cfg
                logger.info(f"  已加载领域插件: {name} ({cfg.get('description', '')[:30]})")
            except Exception as e:
                logger.warning(f"  加载领域插件失败 [{fname}]: {e}")

    # ─── 领域识别 ────────────────────────────────────

    def detect(self, text: str) -> str:
        """识别文本所属领域

        优先级：hotel > product > medical > general
        负向关键词可抑制误判（如"酒店多少钱"应判 hotel 而非 product）
        """
        if not text:
            return "general"

        text_lower = text.lower()

        # 按优先级检测：先检查高优先级领域
        for domain_name in ["hotel", "medical", "product"]:
            cfg = self._plugins.get(domain_name)
            if not cfg:
                continue
            triggers = cfg.get("keywords", {}).get("trigger", [])
            negatives = cfg.get("keywords", {}).get("negative", [])

            # 命中负向关键词则跳过该领域
            if any(neg in text for neg in negatives):
                continue

            # 命中任一触发词则匹配
            for kw in triggers:
                if kw.lower() in text_lower:
                    logger.info(f"领域识别: {domain_name} (命中关键词: {kw})")
                    return domain_name

        return "general"

    # ─── 查询增强 ────────────────────────────────────

    def enhance_query(self, text: str, domain: str) -> str:
        """按领域规则增强查询词"""
        cfg = self._plugins.get(domain) or self._plugins.get("general")
        if not cfg:
            return text

        rewrite = cfg.get("query_rewrite", {})
        suffix = rewrite.get("suffix", "")
        max_tokens = rewrite.get("max_original_tokens", 50)

        # 截断原查询（避免过长短语 + 后缀导致搜索引擎拒绝）
        truncated = text[:max_tokens]
        if suffix:
            return f"{truncated}{suffix}"
        return truncated

    # ─── 源白名单 ────────────────────────────────────

    def get_whitelist(self, domain: str) -> List[str]:
        """获取领域权威源白名单"""
        cfg = self._plugins.get(domain) or self._plugins.get("general", {})
        return cfg.get("source_whitelist", [])

    def is_whitelisted(self, url: str, domain: str) -> bool:
        """判断 URL 是否属于领域权威源"""
        whitelist = self.get_whitelist(domain)
        if not whitelist:
            return False
        url_lower = url.lower()
        for src in whitelist:
            if src.lower() in url_lower:
                return True
        return False

    # ─── 结构化字段提取 ────────────────────────────

    def extract_fields(self, content: str, domain: str) -> Dict[str, Any]:
        """按领域 Schema 从正文内容提取结构化字段

        Args:
            content: 网页正文（已去标签的纯文本）或 snippet
            domain: 领域名

        Returns:
            {field_name: value} 提取到的字段（空字典表示无匹配）
        """
        cfg = self._plugins.get(domain) or self._plugins.get("general", {})
        schema = cfg.get("field_schema", {})
        if not schema or not content:
            return {}

        extracted: Dict[str, Any] = {}
        for field_name, field_cfg in schema.items():
            patterns = field_cfg.get("patterns", [])
            ftype = field_cfg.get("type", "text")
            for pattern in patterns:
                try:
                    m = re.search(pattern, content, re.S)
                    if m:
                        raw = m.group(1).strip()
                        value = self._cast_field(raw, ftype)
                        if value is not None:
                            extracted[field_name] = value
                            break
                except Exception:
                    continue
        return extracted

    def _cast_field(self, raw: str, ftype: str) -> Any:
        """按字段类型转换提取的原始字符串"""
        if not raw:
            return None
        try:
            if ftype == "price":
                # 去掉逗号，转浮点
                return float(raw.replace(",", ""))
            if ftype == "int":
                return int(raw.replace(",", ""))
            if ftype == "rating":
                return float(raw)
            # text
            return raw[:80]  # 限制长度
        except (ValueError, TypeError):
            return None

    # ─── 结果重排 ────────────────────────────────────

    def rank_results(self, results: List[Dict], domain: str,
                     extracted_list: Optional[List[Dict]] = None) -> List[Dict]:
        """按领域策略重排搜索结果

        加权因素：
          - 白名单源加权
          - 已提取到关键字段加权（价格/评分等）
        """
        cfg = self._plugins.get(domain) or self._plugins.get("general", {})
        strategy = cfg.get("ranking_strategy", {})

        boost_wl = float(strategy.get("boost_whitelist_source", 1.0))
        boost_price = float(strategy.get("boost_has_price", 1.0))
        boost_rating = float(strategy.get("boost_has_rating", 1.0))
        boost_sales = float(strategy.get("boost_has_sales", 1.0))
        boost_level = float(strategy.get("boost_has_level", 1.0))

        def _score(idx: int) -> float:
            r = results[idx]
            score = 1.0
            url = r.get("url", "")
            if self.is_whitelisted(url, domain):
                score *= boost_wl
            if extracted_list and idx < len(extracted_list):
                fields = extracted_list[idx]
                if "price" in fields:
                    score *= boost_price
                if "rating" in fields:
                    score *= boost_rating
                if "sales" in fields:
                    score *= boost_sales
                if "level" in fields:
                    score *= boost_level
            return score

        # 按得分降序重排（保留原索引→新顺序）
        indexed = list(range(len(results)))
        indexed.sort(key=_score, reverse=True)
        return [results[i] for i in indexed]

    # ─── 元信息 ────────────────────────────────────

    def get_plugin_info(self, domain: str) -> Dict[str, Any]:
        """获取领域插件元信息（用于日志/调试）"""
        cfg = self._plugins.get(domain) or self._plugins.get("general", {})
        return {
            "name": cfg.get("name", domain),
            "description": cfg.get("description", ""),
            "version": cfg.get("version", ""),
            "whitelist_count": len(cfg.get("source_whitelist", [])),
            "schema_field_count": len(cfg.get("field_schema", {})),
        }


# ─── 模块级单例便捷接口 ────────────────────────────

_router: Optional[DomainRouter] = None


def get_router() -> DomainRouter:
    """获取 DomainRouter 单例"""
    global _router
    if _router is None:
        _router = DomainRouter()
    return _router


def detect_domain(text: str) -> str:
    """便捷函数：识别领域"""
    return get_router().detect(text)


def enhance_query(text: str, domain: str = "") -> str:
    """便捷函数：增强查询词（domain 为空时自动识别）"""
    router = get_router()
    if not domain:
        domain = router.detect(text)
    return router.enhance_query(text, domain)
