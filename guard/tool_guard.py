# -*- coding: utf-8 -*-
"""
guard/tool_guard.py — 工具守卫（修复 WARN #3）
================================================
原则四落地：无论同层与否，工具调用必须过ToolGuard，按read/write定税。

支持13种工具的完整映射（与原SCU_CUF对齐）：
  read类：calculator, weather, time_now, text_stats, file_read,
          exchange_rate, crypto_price, stock_price, github_search, datetime_calc, unit_convert
  write类：file_write, code_run
"""
import os
import sys
import logging
from typing import Tuple, Dict, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from d_layer.axioms import Operation, BASE_TAX_RATES

logger = logging.getLogger("scu2.guard.tool")

# 工具类型映射（原则四落地）
TOOL_TYPE_MAP = {
    # 只读工具（read类，低税 0.2E）
    "calculator":      "read",
    "weather":         "read",
    "time_now":        "read",
    "text_stats":      "read",
    "file_read":       "read",
    "exchange_rate":   "read",
    "crypto_price":    "read",
    "stock_price":     "read",
    "github_search":   "read",
    "datetime_calc":   "read",
    "unit_convert":    "read",
    "web_search":      "read",
    "web_crawl":       "read",
    # 插件市场工具（read类，动态注册）
    "pdf_read":        "read",
    "docx_read":       "read",
    "excel_read":      "read",
    "qrcode_gen":      "read",
    "image_process":   "read",
    "translate":       "read",
    "md_render":       "read",
    # 写操作工具（write类，高税 3.0E）
    "file_write":      "write",
    "code_run":        "write",
    # 插件市场管理操作（write类，高税 3.0E）
    "plugin_install":  "write",
    "plugin_unload":   "write",
    "plugin_uninstall":"write",
}

# 税率配置
TAX_RATES = {
    "read": 0.2,    # 只读工具低税
    "write": 3.0,   # 写操作高税
}


class ToolGuard:
    """工具守卫 — 工具调用独立审计（原则四落地）

    工具调用无论同层与否都需过守卫。
    按 read/write 动作类型决定税率。
    """

    def __init__(self, ledger=None, whitelist=None):
        self.ledger = ledger
        self.whitelist = whitelist

    def get_tool_type(self, tool_name: str) -> str:
        """获取工具类型（read/write）"""
        return TOOL_TYPE_MAP.get(tool_name, "read")  # 未知工具默认read

    def is_readonly(self, tool_name: str) -> bool:
        """判断是否只读工具"""
        return self.get_tool_type(tool_name) == "read"

    def check(self, tool_name: str, tool_type: str = "", op_id: str = "") -> Tuple[bool, str, Dict[str, Any]]:
        """审计工具调用

        Args:
            tool_name: 工具名
            tool_type: "read" 或 "write"（为空时自动从TOOL_TYPE_MAP查询）
            op_id: 操作ID

        Returns:
            (allowed, msg, detail)
        """
        # 自动推断工具类型
        if not tool_type:
            tool_type = self.get_tool_type(tool_name)

        # 未知工具告警
        if tool_name not in TOOL_TYPE_MAP:
            logger.warning(f"⚠️ 未注册工具: {tool_name}（默认按read计税）")

        # 只读工具白名单检查
        if tool_type == "read" and self.whitelist:
            pk = f"tool:{tool_name}"
            if self.whitelist.contains(pk):
                logger.debug(f"工具白名单免审: {tool_name}")
                return True, "白名单免审免税", {"tax": 0, "whitelist": True, "tool_type": tool_type}

        if not self.ledger:
            return True, "无账本，跳过", {"tax": 0, "tool_type": tool_type}

        # 按类型定税：read 低税，write 高税
        operation = "read" if tool_type == "read" else "write"
        ok, msg, detail = self.ledger.pay_tax(
            operation=operation, layer="W1",
            reason=f"tool_call:{tool_name}({tool_type})",
            op_id=op_id,
            pattern_key=f"tool:{tool_name}",
            custom_factor=TAX_RATES.get(tool_type, 1.0) / 0.2,  # 相对read基准的倍率
        )
        if not ok:
            return False, f"工具守卫拒绝: {msg}", detail
        logger.info(f"工具守卫通过: {tool_name}({tool_type}), 税 {detail.get('tax', 0)}E")
        return True, msg, detail

    def list_tools(self) -> Dict[str, str]:
        """列出所有注册工具及类型"""
        return dict(TOOL_TYPE_MAP)

    def get_status(self) -> Dict[str, Any]:
        """获取工具守卫状态"""
        return {
            "total_tools": len(TOOL_TYPE_MAP),
            "read_tools": sum(1 for v in TOOL_TYPE_MAP.values() if v == "read"),
            "write_tools": sum(1 for v in TOOL_TYPE_MAP.values() if v == "write"),
            "tax_rates": TAX_RATES,
            "whitelist_enabled": self.whitelist is not None,
        }
