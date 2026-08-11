# -*- coding: utf-8 -*-
"""
guard/yin_yang_base.py — 阴阳双签基类
========================================
从 code_self_modify._yin_yang_endorse 的双签机制中抽象出通用基类。
与 code_self_modify 并存，不替换现有代码自修改流程。

架构归属：guard/（安全基础设施）
依赖方向：被 M 层继承使用

CUF 合规：
  - 阈值符合硬约束：γ_yin≥0.75, γ_yang≥0.65
  - 不触发 Pair 硬约束（认知思考非高风险操作）
  - 不跨层，不修改 D 层
"""
import logging
from typing import Dict, Any

logger = logging.getLogger("scu2.guard.yin_yang_base")


class YinYangEndorser:
    """阴阳双签基类（抽象自 code_self_modify 的双签机制）

    子类需实现 yin_review / yang_review。
    阈值符合 CUF 硬约束：γ_yin≥0.75, γ_yang≥0.65。

    与 code_self_modify._yin_yang_endorse 的关系：
    - code_self_modify：代码自修改专用双签（硬约束，必触发）
    - YinYangEndorser：通用双签基类（软约束，可复用）
    - 两者并存，未来 v6.0 可统一
    """

    # CUF 硬约束阈值
    YIN_THRESHOLD = 0.75
    YANG_THRESHOLD = 0.65

    def yin_review(self, proposal: Dict[str, Any]) -> float:
        """阴方审查（子类实现）

        Args:
            proposal: 待审查的提议，包含阴方观点等内容

        Returns:
            γ_yin 质量分（0.0-1.0）
        """
        raise NotImplementedError("子类必须实现 yin_review")

    def yang_review(self, proposal: Dict[str, Any]) -> float:
        """阳方审查（子类实现）

        Args:
            proposal: 待审查的提议，包含阳方观点等内容

        Returns:
            γ_yang 质量分（0.0-1.0）
        """
        raise NotImplementedError("子类必须实现 yang_review")

    def endorse(self, proposal: Dict[str, Any]) -> Dict[str, Any]:
        """执行双签判定

        Args:
            proposal: 待审查的提议

        Returns:
            {
                "gamma_yin": float,      # 阴方质量分
                "gamma_yang": float,     # 阳方质量分
                "yin_passed": bool,      # 阴方是否通过
                "yang_passed": bool,     # 阳方是否通过
                "endorsed": bool,        # 双签是否通过
            }
        """
        gamma_yin = self.yin_review(proposal)
        gamma_yang = self.yang_review(proposal)

        yin_passed = gamma_yin >= self.YIN_THRESHOLD
        yang_passed = gamma_yang >= self.YANG_THRESHOLD
        endorsed = yin_passed and yang_passed

        logger.info(f"阴阳双签判定: γ_yin={gamma_yin:.3f}({'通过' if yin_passed else '未通过'}), "
                    f"γ_yang={gamma_yang:.3f}({'通过' if yang_passed else '未通过'}), "
                    f"双签={'通过' if endorsed else '未通过'}")

        return {
            "gamma_yin": round(gamma_yin, 3),
            "gamma_yang": round(gamma_yang, 3),
            "yin_passed": yin_passed,
            "yang_passed": yang_passed,
            "endorsed": endorsed,
        }
