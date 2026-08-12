# -*- coding: utf-8 -*-
"""
m_layer/cognition_endorser.py — 认知层阴阳双签实现
=====================================================
继承 YinYangEndorser 基类，实现认知层的阴阳双签评分。

架构归属：M 层（认知层）
依赖方向：M 层 → guard/yin_yang_base（继承）

评分维度：
  阴方审查：论证深度、批判性、风险识别
  阳方审查：论据充分性、可行性、机会识别
"""
import re
import logging
from typing import Dict, Any

from guard.yin_yang_base import YinYangEndorser

logger = logging.getLogger("SCU3.m.cognition_endorser")


class CognitionEndorser(YinYangEndorser):
    """认知层阴阳双签实现

    阴方审查：批判性词汇 + 分点论证 + 因果论证 + 字数充分
    阳方审查：支持性词汇 + 分点论证 + 因果论证 + 字数充分

    评分范围：0.3（基础分）~ 1.0（满分）
    """

    def yin_review(self, proposal: Dict[str, Any]) -> float:
        """阴方评分：批判视角的质量

        评分维度：
        - 基础分：0.3
        - 批判性词汇（风险/漏洞/局限/问题/不足/隐患）：+0.2
        - 分点论证（1./第一/首先）：+0.15
        - 因果论证（因为/所以/导致/因此）：+0.15
        - 字数充分（>200字 +0.2, >100字 +0.1）
        """
        view = proposal.get("yin_view", "")
        if not view:
            return 0.0

        score = 0.3  # 基础分

        # 批判性词汇
        if re.search(r"风险|漏洞|局限|问题|不足|隐患|缺陷|弊端|挑战", view):
            score += 0.2
            logger.debug("阴方评分: 批判性词汇 +0.2")

        # 分点论证
        if re.search(r"[1一][\.、]|第一|首先", view):
            score += 0.15
            logger.debug("阴方评分: 分点论证1 +0.15")
        if re.search(r"[2二][\.、]|第二|其次", view):
            score += 0.05  # 额外奖励
        if re.search(r"[3三][\.、]|第三|最后", view):
            score += 0.05  # 额外奖励

        # 因果论证
        if re.search(r"因为|所以|导致|因此|从而|使得|引起", view):
            score += 0.15
            logger.debug("阴方评分: 因果论证 +0.15")

        # 字数充分
        if len(view) > 200:
            score += 0.2
            logger.debug("阴方评分: 字数充分(>200) +0.2")
        elif len(view) > 100:
            score += 0.1
            logger.debug("阴方评分: 字数中等(>100) +0.1")

        return min(score, 1.0)

    def yang_review(self, proposal: Dict[str, Any]) -> float:
        """阳方评分：支持视角的质量

        评分维度：
        - 基础分：0.3
        - 支持性词汇（优势/机会/可行/提升/促进/利好）：+0.2
        - 分点论证（1./第一/首先）：+0.15
        - 因果论证（因为/所以/因此/从而）：+0.15
        - 字数充分（>200字 +0.2, >100字 +0.1）
        """
        view = proposal.get("yang_view", "")
        if not view:
            return 0.0

        score = 0.3  # 基础分

        # 支持性词汇
        if re.search(r"优势|机会|可行|提升|促进|利好|效益|价值|效率", view):
            score += 0.2
            logger.debug("阳方评分: 支持性词汇 +0.2")

        # 分点论证
        if re.search(r"[1一][\.、]|第一|首先", view):
            score += 0.15
            logger.debug("阳方评分: 分点论证1 +0.15")
        if re.search(r"[2二][\.、]|第二|其次", view):
            score += 0.05
        if re.search(r"[3三][\.、]|第三|最后", view):
            score += 0.05

        # 因果论证
        if re.search(r"因为|所以|因此|从而|使得|促进|推动", view):
            score += 0.15
            logger.debug("阳方评分: 因果论证 +0.15")

        # 字数充分
        if len(view) > 200:
            score += 0.2
            logger.debug("阳方评分: 字数充分(>200) +0.2")
        elif len(view) > 100:
            score += 0.1
            logger.debug("阳方评分: 字数中等(>100) +0.1")

        return min(score, 1.0)


# 全局单例
_endorser_instance = None


def get_cognition_endorser() -> CognitionEndorser:
    """获取认知双签器单例"""
    global _endorser_instance
    if _endorser_instance is None:
        _endorser_instance = CognitionEndorser()
    return _endorser_instance
