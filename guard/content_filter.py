# -*- coding: utf-8 -*-
"""
guard/content_filter.py — 内容过滤（原则五落地）
====================================================
输出回流的内容脱敏检查点。
非 A4 审计，是数据安全检查，防止 D 层数据通过输出泄漏。

规则库覆盖：
  1. API密钥/Token（OpenAI/DeepSeek/AWS/通用）
  2. 密码/凭证
  3. 长base64（可能是密钥）
  4. 余额/账本数据
  5. 内网IP/私有地址
  6. 员工ID/内部标识
  7. 内部API路径
  8. 哈希链/私钥变量名
  9. D层字段白名单（只允许明确列出的字段输出）
"""
import re
import logging
from typing import Tuple, List, Set

logger = logging.getLogger("SCU3.guard.filter")


class ContentFilter:
    """内容过滤 — 输出脱敏（原则五落地）"""

    # D层可输出字段白名单（只允许这些D层概念出现在输出中）
    D_LAYER_ALLOWED_FIELDS: Set[str] = {
        "Axiom", "Contract", "VALID_LAYERS", "LAYER_ORDER",
        "D", "M", "W1", "W2",  # 层级名
        "A1", "A2", "A3", "A4",  # 公理名
        "observation", "sampling", "signing", "synthesis",  # 契约名
    }

    # D层禁止输出的字段（运行时状态，绝不能出现在输出中）
    D_LAYER_FORBIDDEN_FIELDS: Set[str] = {
        "_balance", "_history", "_hash_chain", "_total_in", "_total_out",
        "_system_state", "_pattern_discounts", "_feedback_counts",
        "_tax_factor_overrides", "_pending_refunds", "_pending_refund",
        "AUTH_TOKEN_ENV", "CUF_LEDGER_AUTH", "_private_key",
        "store_path", "_lock",
    }

    # 敏感模式（正则）— 50+条规则
    SENSITIVE_PATTERNS = [
        # ─── API密钥/Token ───
        (r'sk-[A-Za-z0-9]{20,}', 'sk-[REDACTED]'),                          # OpenAI API key
        (r'deepseek-[A-Za-z0-9]{20,}', 'deepseek-[REDACTED]'),              # DeepSeek API key
        (r'api[_-]?key\s*[:=]\s*[\'"]?[A-Za-z0-9]{16,}[\'"]?', 'api_key=[REDACTED]'),
        (r'access[_-]?token\s*[:=]\s*[\'"]?[A-Za-z0-9]{16,}[\'"]?', 'access_token=[REDACTED]'),
        (r'auth[_-]?token\s*[:=]\s*[\'"]?[A-Za-z0-9]{16,}[\'"]?', 'auth_token=[REDACTED]'),
        (r'bearer\s+[A-Za-z0-9._-]{20,}', 'bearer [REDACTED]'),
        (r'AKIA[0-9A-Z]{16}', 'AKIA[REDACTED]'),                           # AWS Access Key
        (r'aws[_-]?secret[_-]?key\s*[:=]\s*\S+', 'aws_secret_key=[REDACTED]'),
        (r'ghp_[A-Za-z0-9]{36}', 'ghp_[REDACTED]'),                        # GitHub PAT
        (r'gho_[A-Za-z0-9]{36}', 'gho_[REDACTED]'),                        # GitHub OAuth
        (r'glpat-[A-Za-z0-9]{20}', 'glpat-[REDACTED]'),                    # GitLab PAT
        (r'xox[baprs]-[A-Za-z0-9]{10,}', 'xox-[REDACTED]'),                # Slack Token
        # ─── 密码/凭证 ───
        (r'password\s*[:=]\s*\S+', 'password=[REDACTED]'),
        (r'passwd\s*[:=]\s*\S+', 'passwd=[REDACTED]'),
        (r'pwd\s*[:=]\s*\S+', 'pwd=[REDACTED]'),
        (r'secret\s*[:=]\s*[\'"]?[A-Za-z0-9]{8,}[\'"]?', 'secret=[REDACTED]'),
        (r'credential\s*[:=]\s*\S+', 'credential=[REDACTED]'),
        # ─── 长base64/密钥 ───
        (r'\b[A-Za-z0-9+/]{40,}={0,2}\b', '[REDACTED_TOKEN]'),
        (r'-----BEGIN [A-Z ]+PRIVATE KEY-----', '[REDACTED_KEY_BLOCK]'),
        (r'-----BEGIN CERTIFICATE-----', '[REDACTED_CERT]'),
        # ─── 余额/账本数据 ───
        (r'(?:余额|balance)\s*[:=]\s*\d+\.?\d*', 'balance=[REDACTED]'),
        (r'(?:总充值|total_in)\s*[:=]\s*\d+\.?\d*', 'total_in=[REDACTED]'),
        (r'(?:总支出|total_out)\s*[:=]\s*\d+\.?\d*', 'total_out=[REDACTED]'),
        (r'hash_chain\s*[:=]\s*\w+', 'hash_chain=[REDACTED]'),
        # ─── 内网IP/私有地址 ───
        (r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[REDACTED_INTERNAL_IP]'),
        (r'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b', '[REDACTED_INTERNAL_IP]'),
        (r'\b192\.168\.\d{1,3}\.\d{1,3}\b', '[REDACTED_INTERNAL_IP]'),
        (r'\b127\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[REDACTED_LOOPBACK]'),
        (r'\b::1\b', '[REDACTED_LOOPBACK]'),
        # ─── 员工ID/内部标识 ───
        (r'\bEMP\d{6,}\b', '[REDACTED_EMP_ID]'),
        (r'\b员工[ _]?(?:ID|编号)\s*[:=]?\s*\d+', '员工ID=[REDACTED]'),
        (r'\binternal[_-]?id\s*[:=]\s*\S+', 'internal_id=[REDACTED]'),
        # ─── 内部API路径 ───
        (r'/internal/api/\S+', '/internal/api/[REDACTED]'),
        (r'/admin/secret/\S+', '/admin/secret/[REDACTED]'),
        (r'/_private/\S+', '/_private/[REDACTED]'),
        # ─── 数据库连接串 ───
        (r'mongodb://\S+', 'mongodb://[REDACTED]'),
        (r'postgresql?://\S+', 'postgresql://[REDACTED]'),
        (r'mysql://\S+', 'mysql://[REDACTED]'),
        (r'redis://\S+', 'redis://[REDACTED]'),
        # ─── JWT ───
        (r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b', '[REDACTED_JWT]'),
        # ─── 手机号/身份证（中国） ───
        (r'\b1[3-9]\d{9}\b', '[REDACTED_PHONE]'),
        (r'\b\d{17}[\dXx]\b', '[REDACTED_ID_CARD]'),
        # ─── 邮箱 ───
        (r'\b[A-Za-z0-9._%+-]+@(?:internal|corp|intra)\.', '[REDACTED_INTERNAL_EMAIL]'),
        # ─── P0增强：银行卡号（16-19位连续数字） ───
        (r'\b(?:4\d{15,18}|5[1-5]\d{14,17}|62\d{13,16}|37\d{13,16})\b', '[REDACTED_BANK_CARD]'),
        # ─── P0增强：IPv6地址 ───
        (r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b', '[REDACTED_IPV6]'),
        # ─── P0增强：Google/Azure/腾讯云密钥 ───
        (r'AIza[0-9A-Za-z_\-]{35}', 'AIza[REDACTED]'),                        # Google API Key
        (r'ya29\.[0-9A-Za-z_\-]+', 'ya29.[REDACTED]'),                         # Google OAuth Token
        (r'AKIA[0-9A-Z]{16}', 'AKIA[REDACTED]'),                               # AWS Access Key (重复但更精确)
        (r'腾讯云AppId\s*[:=]\s*\d{10,}', '腾讯云AppId=[REDACTED]'),            # 腾讯云AppId
        # ─── P0增强：Windows/Unix敏感文件路径 ───
        (r'[Cc]:\\(?:Windows|Users|Program Files|System32)[\\\S]+', '[REDACTED_PATH]'),
        (r'/(?:etc|root|home|var|opt|usr)/(?:passwd|shadow|ssh|config)[\S]*', '[REDACTED_PATH]'),
        # ─── P0增强：环境变量泄露 ───
        (r'(?:SECRET_KEY|PRIVATE_KEY|DB_PASSWORD|REDIS_PASSWORD|JWT_SECRET)\s*[:=]\s*\S+',
         '[REDACTED_ENV]'),
        # ─── P0增强：Shell命令注入特征 ───
        (r'(?:rm\s+-rf|chmod\s+777|curl\s+.*\|\s*sh|wget\s+.*\|\s*bash)', '[REDACTED_CMD]'),
        # ─── P0增强：Swagger/OpenAPI内部端点 ───
        (r'/(?:swagger|openapi|api-docs|graphql)(?:\?|\s|$)', '/[REDACTED_API]'),
    ]

    # 禁止输出的关键词（D层运行时变量名）
    FORBIDDEN_KEYWORDS = [
        "__hash_chain__", "_private_key", "CUF_LEDGER_AUTH",
        "_balance", "_history", "_pattern_discounts",
        "_feedback_counts", "_tax_factor_overrides",
        "_pending_refunds", "AUTH_TOKEN_ENV",
    ]

    # 统计
    _filter_count = 0
    _warning_count = 0

    def filter(self, text: str) -> Tuple[str, List[str]]:
        """过滤输出内容（原则五落地）

        Args:
            text: 待过滤文本

        Returns:
            (filtered_text, warnings)
        """
        if not text:
            return text, []

        warnings = []
        filtered = text
        self._filter_count += 1

        # 1. 关键词检查（D层禁止字段）
        for kw in self.FORBIDDEN_KEYWORDS:
            if kw in filtered:
                filtered = filtered.replace(kw, "[REDACTED]")
                warnings.append(f"包含D层敏感关键词: {kw}")

        # 2. D层字段名检查（禁止_fields中的内容）
        for field in self.D_LAYER_FORBIDDEN_FIELDS:
            if field in filtered:
                filtered = filtered.replace(field, "[REDACTED]")
                warnings.append(f"泄漏D层字段: {field}")

        # 3. 正则替换（50+规则）
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            new_filtered, count = re.subn(pattern, replacement, filtered, flags=re.IGNORECASE)
            if count > 0:
                filtered = new_filtered
                warnings.append(f"脱敏 {count} 处敏感数据 ({pattern[:30]}...)")

        if warnings:
            self._warning_count += 1
            logger.warning(f"内容过滤 #{self._filter_count}: {warnings}")

        return filtered, warnings

    def is_d_field_allowed(self, field_name: str) -> bool:
        """检查D层字段是否允许输出"""
        return field_name in self.D_LAYER_ALLOWED_FIELDS

    def get_status(self) -> dict:
        """获取过滤状态"""
        return {
            "total_filters": self._filter_count,
            "total_warnings": self._warning_count,
            "patterns_count": len(self.SENSITIVE_PATTERNS),
            "forbidden_keywords": len(self.FORBIDDEN_KEYWORDS),
            "d_layer_forbidden_fields": len(self.D_LAYER_FORBIDDEN_FIELDS),
            "d_layer_allowed_fields": len(self.D_LAYER_ALLOWED_FIELDS),
        }


# 全局单例
_filter: ContentFilter = None


def get_filter() -> ContentFilter:
    """获取内容过滤器单例"""
    global _filter
    if _filter is None:
        _filter = ContentFilter()
    return _filter


def filter_output(text: str) -> Tuple[str, List[str]]:
    """便捷函数：过滤输出（原则五强制调用入口）"""
    return get_filter().filter(text)
