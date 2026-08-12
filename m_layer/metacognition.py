# -*- coding: utf-8 -*-
"""
M 层：m_layer/metacognition.py — 元认知层（汇合 + 周期审计）
=============================================================
v3 核心组件：业务路径与 CUF 路径的汇合点。
- 汇合：整合认知层的回复 + CUF 审计结果
- 周期审计：每日汇总反馈权重 → 写 W1 层覆写表（同层免审）
- 补偿：业务失败时触发退款

注意：周期审计 M→W1 是同层内写操作（W1 账本实例），
      不触发 A4（A4 只管依赖方向 D←M←W1←W2，不管运行时访问）。
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger("SCU3.m.metacog")


class MetacognitionLayer:
    """元认知层 — 汇合 + 周期审计 + 补偿 + 自学习联动"""

    def __init__(self, ledger=None, guard=None, whitelist=None):
        self.ledger = ledger          # W1 层账本实例
        self.guard = guard            # 守卫层
        self.whitelist = whitelist    # 白名单管理器
        self._last_audit_time: Optional[datetime] = None
        self._audit_interval = timedelta(hours=24)
        # 阶段2：自学习引擎（延迟注入，避免循环依赖）
        self._learning_engine = None

    def attach_learning_engine(self, engine) -> None:
        """注入自学习引擎（由 server.py 在启动时调用）"""
        self._learning_engine = engine
        logger.info("自学习引擎已挂载到元认知层")

    # ─── 汇合业务路径与 CUF 路径 ─────────────────────

    def merge(self, business_ctx: Dict[str, Any],
              cuf_traces: List[Dict[str, Any]],
              op_id: str = "") -> Dict[str, Any]:
        """汇合业务结果与 CUF 审计轨迹"""
        merged = {
            "op_id": op_id,
            "response": business_ctx.get("response", ""),
            "business_ok": business_ctx.get("cognition_ok", False),
            "cuf_traces": cuf_traces,
            "cuf_failed": any(not t.get("passed", False) for t in cuf_traces),
            "refunds": [],
            "plugin_traces": business_ctx.get("plugin_traces", []),
        }

        # 业务侧主动拦截（如插件blocked）→ 设置blocked
        if business_ctx.get("blocked"):
            merged["blocked"] = True
        # CUF 审计失败 → 覆盖响应 + 补偿退款
        elif merged["cuf_failed"]:
            # 补偿退款
            if self.guard:
                for trace in cuf_traces:
                    if trace.get("tax", 0) > 0 and trace.get("op_id"):
                        refund = self.guard.refund_on_failure(
                            trace["op_id"],
                            reason=business_ctx.get("error", "业务失败")
                        )
                        if refund > 0:
                            merged["refunds"].append({
                                "op_id": trace["op_id"], "amount": refund
                            })
                merged["compensated"] = len(merged["refunds"]) > 0
            # 覆盖响应
            failed = [t for t in cuf_traces if not t.get("passed", False)]
            merged["response"] = f"🛡️ CUF 审计拦截: {failed[0].get('msg', '')}"
            merged["blocked"] = True
        else:
            merged["blocked"] = False

        return merged

    # ─── 周期审计（每日汇总权重 → 写 W1 覆写表）────────

    def daily_audit(self, force: bool = False) -> Dict[str, Any]:
        """周期审计：汇总反馈 → 写 W1 层 tax_factor_overrides

        v3 修复：覆写表在 W1 层，M 层写入是运行时访问，不触发 A4。
        """
        now = datetime.now()
        if not force and self._last_audit_time and \
           (now - self._last_audit_time) < self._audit_interval:
            return {"skipped": True, "reason": "未到审计周期",
                    "last_audit": self._last_audit_time.isoformat()}

        if not self.ledger:
            return {"error": "无账本"}

        audit_results = []
        pattern_keys = self.ledger.get_all_feedback_patterns()

        for pk in pattern_keys:
            agg = self.ledger.get_feedback_aggregate(pk)
            suggested = agg.get("suggested_factor", 1.0)
            # 写 W1 层覆写表（同层运行时访问，免审）
            self.ledger.set_tax_factor_override(
                pattern_key=pk, factor=suggested,
                expiry_hours=24.0, source="daily_audit",
            )
            audit_results.append({
                "pattern_key": pk,
                "up": agg["up"], "down": agg["down"], "net": agg["net"],
                "factor_applied": suggested,
                "user_count": agg.get("user_count", 0),
            })

        # 清理过期白名单
        whitelist_cleaned = 0
        if self.whitelist:
            whitelist_cleaned = self.whitelist.cleanup_expired()

        # 阶段2：触发自学习闭环（反馈→沉淀→提示词优化）
        learning_report = None
        if self._learning_engine is not None:
            try:
                learning_report = self._learning_engine.learn(force=force)
                logger.info(f"✅ 自学习闭环已触发: 优化{learning_report.get('prompts_adjusted', 0)}项, "
                            f"沉淀{learning_report.get('knowledge_sunk', 0)}条")
            except Exception as e:
                logger.error(f"自学习闭环失败: {e}")
                learning_report = {"error": str(e)}

        # 阶段4：触发Agent经验学习（从执行历史中积累经验）
        agent_learning_report = None
        try:
            from m_layer.task_executor import get_executor
            from m_layer.agent_learning import get_agent_learning
            history = get_executor().get_history(50)
            if history:
                agent_learning_report = get_agent_learning().learn_from_history(history)
                logger.info(f"✅ Agent经验学习: 处理{agent_learning_report.get('records_processed', 0)}条记录")
        except Exception as e:
            logger.warning(f"Agent经验学习失败: {e}")
            agent_learning_report = {"error": str(e)}

        self._last_audit_time = now
        result = {
            "audit_time": now.isoformat(),
            "patterns_audited": len(audit_results),
            "results": audit_results,
            "whitelist_cleaned": whitelist_cleaned,
            "learning_report": learning_report,
            "agent_learning_report": agent_learning_report,
            "next_audit": (now + self._audit_interval).isoformat(),
        }
        logger.info(f"✅ 周期审计: {len(audit_results)} 个 pattern, "
                     f"清理白名单 {whitelist_cleaned} 条")
        return result

    def force_audit(self) -> Dict[str, Any]:
        """强制触发周期审计"""
        return self.daily_audit(force=True)
