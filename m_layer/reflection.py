# -*- coding: utf-8 -*-
"""
m_layer/reflection.py — 执行后反思引擎（M层）
================================================
阶段4第一批：任务执行完成后，对过程和结果进行反思总结

能力对标：AI助手完成任务后的"检查结果→总结经验→沉淀知识"环节

功能：
  1. 分析任务执行报告（成功/失败步骤、耗时、错误）
  2. 生成反思总结（成功点、失败原因、改进建议）
  3. 将成功经验沉淀到RAG知识库（供下次复用）
  4. 将失败教训沉淀到RAG知识库（供避免重复错误）

降级策略：
  - LLM不可用 → 规则总结（基于执行统计）

架构归属：M层（元认知层的延伸）
依赖：m_layer/llm_client, w1_layer/knowledge_store
"""
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger("SCU3.m.reflection")


class ReflectionEngine:
    """执行后反思引擎

    用法:
        engine = ReflectionEngine()
        reflection = engine.reflect(execution_report)
        # reflection = {
        #     "summary": "任务整体评价",
        #     "successes": ["成功点1", ...],
        #     "failures": ["失败原因1", ...],
        #     "improvements": ["改进建议1", ...],
        #     "knowledge_sunk": bool,  # 是否沉淀到知识库
        # }
    """

    def __init__(self):
        self._llm = None
        self._store = None

    def _get_llm(self):
        if self._llm is None:
            try:
                from m_layer.llm_client import get_client
                self._llm = get_client()
            except Exception:
                pass
        return self._llm

    def _get_store(self):
        if self._store is None:
            try:
                from w1_layer.knowledge_store import get_store
                self._store = get_store()
            except Exception:
                pass
        return self._store

    def reflect(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """对任务执行报告进行反思

        Args:
            report: 任务执行报告（来自TaskExecutor）
                {
                    "goal": "原始目标",
                    "steps": [{step_id, action, status, result, ...}],
                    "success": bool,
                    "elapsed_ms": float,
                    "errors": [...],
                }

        Returns:
            反思结果
        """
        reflection = {
            "goal": report.get("goal", ""),
            "summary": "",
            "successes": [],
            "failures": [],
            "improvements": [],
            "knowledge_sunk": False,
            "reflected_at": datetime.now().isoformat(),
        }

        # 统计执行情况
        steps = report.get("steps", [])
        total = len(steps)
        succeeded = sum(1 for s in steps if s.get("status") == "done")
        failed = sum(1 for s in steps if s.get("status") == "failed")
        elapsed = report.get("elapsed_ms", 0)

        # 尝试LLM反思，失败则降级到规则反思
        llm = self._get_llm()
        llm_reflection = None
        if llm and llm.mode != "rule_based":
            llm_reflection = self._reflect_with_llm(report, total, succeeded, failed, elapsed)
        if llm_reflection:
            reflection.update(llm_reflection)
        else:
            # 降级：规则反思
            reflection.update(self._reflect_with_rules(report, total, succeeded, failed, elapsed))

        # 沉淀知识到RAG
        reflection["knowledge_sunk"] = self._sink_knowledge(report, reflection)

        logger.info(f"反思完成: 成功{succeeded}/{total}, 失败{failed}, "
                    f"知识沉淀={reflection['knowledge_sunk']}")
        return reflection

    def _reflect_with_llm(self, report: Dict, total: int, succeeded: int,
                          failed: int, elapsed: float) -> Optional[Dict]:
        """用LLM进行反思"""
        steps_summary = []
        for s in report.get("steps", []):
            status = s.get("status", "unknown")
            action = s.get("action", "")
            desc = s.get("description", "")
            error = s.get("error", "")
            line = f"  步骤{s.get('step_id')}: [{status}] {action} - {desc}"
            if error:
                line += f" | 错误: {error}"
            steps_summary.append(line)

        prompt = f"""请对以下任务执行结果进行反思总结。

目标: {report.get('goal', '')}
执行结果: 成功{succeeded}/{total}步, 失败{failed}步, 耗时{elapsed:.0f}ms

步骤详情:
{chr(10).join(steps_summary)}

请严格按JSON格式输出:
```json
{{
  "summary": "一句话总结",
  "successes": ["成功点1", "成功点2"],
  "failures": ["失败原因1"],
  "improvements": ["改进建议1"]
}}
```"""

        try:
            result = self._llm.chat(prompt, system_prompt="analytical")
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            return self._parse_llm_reflection(content)
        except Exception as e:
            logger.warning(f"LLM反思失败: {e}")
            return None

    def _parse_llm_reflection(self, content: str) -> Optional[Dict]:
        """解析LLM反思结果"""
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.S)
        if json_match:
            raw = json_match.group(1)
        else:
            # 尝试匹配裸JSON
            json_match = re.search(r'(\{.*\})', content, re.S)
            raw = json_match.group(1) if json_match else content

        try:
            data = json.loads(raw)
            return {
                "summary": data.get("summary", ""),
                "successes": data.get("successes", []),
                "failures": data.get("failures", []),
                "improvements": data.get("improvements", []),
            }
        except (json.JSONDecodeError, TypeError):
            return None

    def _reflect_with_rules(self, report: Dict, total: int, succeeded: int,
                            failed: int, elapsed: float) -> Dict:
        """规则反思（LLM不可用降级）"""
        successes = []
        failures = []
        improvements = []

        # 分析成功点
        if report.get("success"):
            successes.append(f"任务成功完成，{succeeded}个步骤全部通过")
        if elapsed < 1000:
            successes.append(f"执行高效，耗时仅{elapsed:.0f}ms")

        # 分析失败点
        if failed > 0:
            failures.append(f"{failed}个步骤失败")
            for s in report.get("steps", []):
                if s.get("status") == "failed":
                    err = s.get("error", "未知错误")
                    failures.append(f"步骤{s.get('step_id')}({s.get('action')}): {err}")

        # 改进建议
        if failed > 0:
            improvements.append("考虑增加错误重试机制")
        if elapsed > 5000:
            improvements.append("执行耗时较长，考虑优化关键步骤")
        if total > 5:
            improvements.append("步骤较多，考虑合并相似步骤")

        summary = f"任务{'成功' if report.get('success') else '失败'}, "
        summary += f"完成{succeeded}/{total}步, 耗时{elapsed:.0f}ms"

        return {
            "summary": summary,
            "successes": successes,
            "failures": failures,
            "improvements": improvements,
        }

    def _sink_knowledge(self, report: Dict, reflection: Dict) -> bool:
        """将执行经验沉淀到RAG知识库"""
        store = self._get_store()
        if not store:
            return False

        try:
            # 构建知识文档
            goal = report.get("goal", "")
            success = report.get("success", False)
            tag = "成功经验" if success else "失败教训"

            doc = f"""[任务执行{tag}]
目标: {goal}
结果: {'成功' if success else '失败'}
耗时: {report.get('elapsed_ms', 0):.0f}ms

执行步骤:
"""
            for s in report.get("steps", []):
                doc += f"  {s.get('step_id')}. [{s.get('status')}] {s.get('action')}: {s.get('description', '')}\n"

            doc += f"\n反思总结: {reflection.get('summary', '')}\n"
            if reflection.get("successes"):
                doc += "成功点: " + "; ".join(reflection["successes"]) + "\n"
            if reflection.get("failures"):
                doc += "失败原因: " + "; ".join(reflection["failures"]) + "\n"
            if reflection.get("improvements"):
                doc += "改进建议: " + "; ".join(reflection["improvements"]) + "\n"

            doc_id = store.add_document(doc, metadata={
                "source": "agent_reflection",
                "type": "execution_experience",
                "success": success,
                "goal": goal[:100],
            })

            logger.info(f"执行经验已沉淀到知识库 (doc_id={doc_id})")
            return doc_id > 0
        except Exception as e:
            logger.warning(f"知识沉淀失败: {e}")
            return False


# ─── 单例 ────────────────────────────────────
_reflection_instance: Optional[ReflectionEngine] = None


def get_reflection_engine() -> ReflectionEngine:
    """获取反思引擎单例"""
    global _reflection_instance
    if _reflection_instance is None:
        _reflection_instance = ReflectionEngine()
    return _reflection_instance
