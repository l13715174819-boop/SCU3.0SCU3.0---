# -*- coding: utf-8 -*-
"""
m_layer/task_planner.py — 任务拆解器（M层）
============================================
阶段4第一批：把用户目标拆解为有序可执行步骤列表

能力对标：AI助手接到任务后的"理解→拆解→规划"环节

工作流：
  1. 接收用户自然语言目标
  2. 调用LLM分析目标，拆解为有序步骤
  3. 每步标注：动作类型、所需工具、参数、依赖关系
  4. 返回结构化执行计划

降级策略：
  - LLM不可用 → 规则模板拆解（常见任务类型）
  - 目标过于模糊 → 返回澄清请求

架构归属：M层（认知层调用LLM做规划）
依赖：m_layer/llm_client（LLM调用）
"""
import json
import logging
import re
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("scu2.m.planner")


class TaskPlanner:
    """任务拆解器：把用户目标转为结构化执行计划

    用法:
        planner = TaskPlanner()
        plan = planner.plan("分析README.md的词频并生成报告")
        # plan = {
        #     "goal": "...",
        #     "steps": [
        #         {"step_id": 1, "action": "file_read", "params": {...}, ...},
        #         {"step_id": 2, "action": "code_run", "params": {...}, ...},
        #         {"step_id": 3, "action": "file_write", "params": {...}, ...},
        #     ],
        #     "cleanup_needed": True,
        # }
    """

    # 可用工具映射（与 action.py 的13种工具对齐）
    AVAILABLE_TOOLS = [
        "calculator", "weather", "time_now", "text_stats", "file_read",
        "exchange_rate", "crypto_price", "stock_price", "github_search",
        "datetime_calc", "unit_convert", "file_write", "code_run",
    ]

    # 工具用途说明（供LLM参考选择）
    TOOL_DESCRIPTIONS = {
        "calculator": "数学计算，支持加减乘除幂模",
        "weather": "查询城市天气",
        "time_now": "获取当前时间",
        "text_stats": "统计文本字数/词数/行数",
        "file_read": "读取sandbox目录内的文件",
        "exchange_rate": "查询汇率",
        "crypto_price": "查询加密货币价格",
        "stock_price": "查询股票价格",
        "github_search": "搜索GitHub仓库",
        "datetime_calc": "日期计算",
        "unit_convert": "单位换算（温度/长度/重量）",
        "file_write": "写入文件到sandbox目录",
        "code_run": "在沙箱中执行Python代码（不能import，不能用open）",
    }

    def __init__(self):
        self._llm = None

    def _get_llm(self):
        """延迟加载LLM客户端"""
        if self._llm is None:
            try:
                from m_layer.llm_client import get_client
                self._llm = get_client()
            except Exception as e:
                logger.warning(f"LLM客户端不可用: {e}")
        return self._llm

    def plan(self, goal: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """拆解用户目标为执行计划

        Args:
            goal: 用户的自然语言目标
            context: 额外上下文（如历史对话、用户偏好）

        Returns:
            {
                "goal": "原始目标",
                "steps": [步骤列表],
                "cleanup_needed": bool,
                "estimated_steps": int,
                "created_at": "ISO时间",
                "source": "llm" | "rule_based" | "clarify",
            }
        """
        context = context or {}

        # 尝试LLM拆解
        llm = self._get_llm()
        if llm and llm.mode != "rule_based":
            plan = self._plan_with_llm(goal, context)
            if plan:
                return plan

        # 降级：规则模板拆解
        plan = self._plan_with_rules(goal, context)
        return plan

    def _plan_with_llm(self, goal: str, context: Dict) -> Optional[Dict[str, Any]]:
        """用LLM拆解任务"""
        prompt = self._build_planning_prompt(goal, context)

        try:
            result = self._llm.chat(prompt, system_prompt="coding")
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            plan = self._parse_llm_plan(goal, content)
            if plan:
                logger.info(f"LLM拆解成功: {len(plan['steps'])}步")
                return plan
        except Exception as e:
            logger.warning(f"LLM拆解失败: {e}")

        return None

    def _build_planning_prompt(self, goal: str, context: Dict) -> str:
        """构建拆解提示词"""
        tools_desc = "\n".join(
            f"  - {name}: {desc}" for name, desc in self.TOOL_DESCRIPTIONS.items()
        )
        return f"""请把以下目标拆解为可执行的步骤列表。

目标: {goal}

可用工具:
{tools_desc}

请严格按以下JSON格式输出（不要输出其他内容）:
```json
{{
  "steps": [
    {{
      "step_id": 1,
      "action": "工具名或code_run",
      "description": "这步做什么",
      "params": {{"参数名": "值"}},
      "depends_on": [],
      "is_temporary": false
    }}
  ],
  "cleanup_needed": false,
  "notes": "补充说明"
}}
```

规则:
1. 每步必须指定action（工具名）和params
2. code_run的params格式为 {{"code": "Python代码字符串"}}
3. depends_on列出必须先完成的step_id
4. is_temporary=true表示这步产生的中间结果可清理
5. 步骤数量尽量精简（通常2-5步）
6. 如果目标模糊无法拆解，返回 {{"clarify": "需要澄清的问题"}}"""

    def _parse_llm_plan(self, goal: str, content: str) -> Optional[Dict[str, Any]]:
        """解析LLM返回的计划"""
        # 提取JSON块
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.S)
        if not json_match:
            # 尝试直接解析
            json_match = re.search(r'\{.*\}', content, re.S)

        raw = json_match.group(1) if json_match else content

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

        # 澄清请求
        if "clarify" in data:
            return {
                "goal": goal,
                "steps": [],
                "cleanup_needed": False,
                "estimated_steps": 0,
                "created_at": datetime.now().isoformat(),
                "source": "clarify",
                "clarify": data["clarify"],
            }

        steps = data.get("steps", [])
        if not steps:
            return None

        # 规范化步骤
        normalized = []
        for s in steps:
            if not s.get("action"):
                continue
            normalized.append({
                "step_id": s.get("step_id", len(normalized) + 1),
                "action": s["action"],
                "description": s.get("description", ""),
                "params": s.get("params", {}),
                "depends_on": s.get("depends_on", []),
                "is_temporary": s.get("is_temporary", False),
                "status": "pending",
            })

        if not normalized:
            return None

        return {
            "goal": goal,
            "steps": normalized,
            "cleanup_needed": data.get("cleanup_needed", False),
            "estimated_steps": len(normalized),
            "created_at": datetime.now().isoformat(),
            "source": "llm",
            "notes": data.get("notes", ""),
        }

    def _plan_with_rules(self, goal: str, context: Dict) -> Dict[str, Any]:
        """规则模板拆解（LLM不可用时的降级方案）"""
        goal_lower = goal.lower().strip()

        # 模板1：分析文件类
        if any(kw in goal_lower for kw in ["分析", "统计", "词频", "analyze"]):
            if "文件" in goal or "file" in goal_lower or "readme" in goal_lower:
                filename = self._extract_filename(goal) or "readme.md"
                return self._build_file_analysis_plan(goal, filename)

        # 模板2：计算类
        if any(kw in goal_lower for kw in ["计算", "算", "calc"]):
            expr = self._extract_expression(goal)
            return {
                "goal": goal,
                "steps": [{
                    "step_id": 1,
                    "action": "calculator",
                    "description": f"计算: {expr}",
                    "params": {"expression": expr},
                    "depends_on": [],
                    "is_temporary": False,
                    "status": "pending",
                }],
                "cleanup_needed": False,
                "estimated_steps": 1,
                "created_at": datetime.now().isoformat(),
                "source": "rule_based",
            }

        # 模板3：查询类
        if any(kw in goal_lower for kw in ["天气", "汇率", "价格", "时间", "股票"]):
            return self._build_query_plan(goal)

        # 模板4：代码执行类
        if any(kw in goal_lower for kw in ["运行", "执行", "run", "exec", "代码"]):
            return {
                "goal": goal,
                "steps": [{
                    "step_id": 1,
                    "action": "code_run",
                    "description": "执行代码",
                    "params": {"code": self._extract_code(goal)},
                    "depends_on": [],
                    "is_temporary": False,
                    "status": "pending",
                }],
                "cleanup_needed": False,
                "estimated_steps": 1,
                "created_at": datetime.now().isoformat(),
                "source": "rule_based",
            }

        # 无法识别：返回澄清请求
        return {
            "goal": goal,
            "steps": [],
            "cleanup_needed": False,
            "estimated_steps": 0,
            "created_at": datetime.now().isoformat(),
            "source": "clarify",
            "clarify": f"无法自动拆解目标'{goal}'，请提供更具体的步骤描述。",
        }

    def _build_file_analysis_plan(self, goal: str, filename: str) -> Dict[str, Any]:
        """构建文件分析计划（读→分析→报告）"""
        return {
            "goal": goal,
            "steps": [
                {
                    "step_id": 1,
                    "action": "file_read",
                    "description": f"读取文件 {filename}",
                    "params": {"path": filename},
                    "depends_on": [],
                    "is_temporary": False,
                    "status": "pending",
                },
                {
                    "step_id": 2,
                    "action": "code_run",
                    "description": "分析文件内容（字数统计/词频）",
                    "params": {"code": f"""data = locals().get('step1_result', {{}})
content = data.get('content', '') if isinstance(data, dict) else str(data)
chars = len(content)
words = len(content.split())
lines = content.count(chr(10)) + 1
print(f'字符数: {{chars}}')
print(f'词数: {{words}}')
print(f'行数: {{lines}}')"""},
                    "depends_on": [1],
                    "is_temporary": True,
                    "status": "pending",
                },
                {
                    "step_id": 3,
                    "action": "file_write",
                    "description": "生成分析报告",
                    "params": {"path": "analysis_report.txt", "content": "分析报告（由步骤2结果填充）"},
                    "depends_on": [2],
                    "is_temporary": False,
                    "status": "pending",
                },
            ],
            "cleanup_needed": True,
            "estimated_steps": 3,
            "created_at": datetime.now().isoformat(),
            "source": "rule_based",
        }

    def _build_query_plan(self, goal: str) -> Dict[str, Any]:
        """构建查询计划"""
        goal_lower = goal.lower()
        action = "time_now"
        params = {}

        if "天气" in goal:
            action = "weather"
            for city in ["北京", "上海", "广州", "深圳", "成都", "杭州"]:
                if city in goal:
                    params = {"city": city}
                    break
        elif "汇率" in goal:
            action = "exchange_rate"
            m = re.search(r'([A-Za-z]{3})', goal)
            params = {"base": m.group(1).upper() if m else "USD"}
        elif "比特币" in goal or "btc" in goal_lower:
            action = "crypto_price"
            params = {"symbol": "btc"}
        elif "股票" in goal:
            action = "stock_price"
            m = re.search(r'([A-Za-z]{1,5})', goal)
            params = {"code": m.group(1).upper() if m else "AAPL"}

        return {
            "goal": goal,
            "steps": [{
                "step_id": 1,
                "action": action,
                "description": f"查询: {goal}",
                "params": params,
                "depends_on": [],
                "is_temporary": False,
                "status": "pending",
            }],
            "cleanup_needed": False,
            "estimated_steps": 1,
            "created_at": datetime.now().isoformat(),
            "source": "rule_based",
        }

    def _extract_filename(self, text: str) -> Optional[str]:
        """从文本中提取文件名"""
        m = re.search(r'([\w.-]+\.(?:md|txt|py|json|csv|html|js))', text, re.I)
        return m.group(1) if m else None

    def _extract_expression(self, text: str) -> str:
        """从文本中提取数学表达式"""
        m = re.search(r'[\d\s+\-*/().%^×÷]+', text)
        return m.group(0).strip() if m else "0"

    def _extract_code(self, text: str) -> str:
        """从文本中提取代码"""
        m = re.search(r'```python\s*(.*?)\s*```', text, re.S)
        return m.group(1) if m else "print('hello')"


# ─── 单例 ────────────────────────────────────
_planner_instance: Optional[TaskPlanner] = None


def get_planner() -> TaskPlanner:
    """获取任务拆解器单例"""
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = TaskPlanner()
    return _planner_instance
