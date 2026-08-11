# -*- coding: utf-8 -*-
"""
m_layer/code_generator.py — 代码生成器（M层）
==============================================
阶段4第二批：让Agent能自己生成Python代码并安全执行

能力对标：AI助手"根据需求写脚本→安全检查→执行→验证"环节

功能:
  1. 接收自然语言需求，调用LLM生成Python代码
  2. AST安全审查（复用沙箱安全规则）
  3. 沙箱执行生成代码
  4. 验证执行结果
  5. 失败时自动反馈错误给LLM重新生成

降级策略:
  - LLM不可用 → 返回模板代码
  - 安全审查失败 → 拒绝执行
  - 执行失败 → 反馈错误，重试生成（最多3次）

架构归属：M层（认知层代码生成）
依赖：m_layer/llm_client, w1_layer/action（沙箱）
"""
import ast
import json
import logging
import re
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger("scu2.m.codegen")


class CodeGenerator:
    """代码生成器：需求→生成→审查→执行→验证

    用法:
        gen = CodeGenerator()
        result = gen.generate_and_run("计算1到100的和")
        # result = {"code": "...", "output": "...", "success": True}
    """

    # 危险函数/属性（与 action.py 的沙箱规则对齐）
    FORBIDDEN_FUNCS = {
        'eval', 'exec', 'compile', '__import__', 'getattr', 'setattr',
        'delattr', 'globals', 'locals', 'vars', 'dir', 'type', 'input',
        'open', 'breakpoint',
    }
    FORBIDDEN_ATTRS = {
        '__class__', '__bases__', '__subclasses__', '__mro__', '__globals__',
        '__builtins__', '__dict__', '__code__', '__module__', '__init__',
        '__import__', '__getattribute__', '__setattr__', '__delattr__',
        'f_globals', 'f_locals', 'f_builtins', 'f_code',
    }

    MAX_RETRIES = 3

    def __init__(self):
        self._llm = None
        self._action = None

    def _get_llm(self):
        if self._llm is None:
            try:
                from m_layer.llm_client import get_client
                self._llm = get_client()
            except Exception:
                pass
        return self._llm

    def _get_action(self):
        if self._action is None:
            from w1_layer.action import ActionLayer
            self._action = ActionLayer()
        return self._action

    def generate_and_run(self, requirement: str,
                         context: Optional[Dict] = None) -> Dict[str, Any]:
        """生成代码并执行

        Args:
            requirement: 自然语言需求描述
            context: 额外上下文（变量/前序结果）

        Returns:
            {
                "requirement": "原始需求",
                "code": "生成的代码",
                "output": "执行输出",
                "result": Any,
                "success": bool,
                "error": str|None,
                "attempts": int,
                "safe": bool,
            }
        """
        context = context or {}

        for attempt in range(1, self.MAX_RETRIES + 1):
            # ① 生成代码
            code = self._generate_code(requirement, context, attempt)

            # ② 安全审查
            is_safe, safety_msg = self._audit_code(code)
            if not is_safe:
                logger.warning(f"代码安全审查失败(尝试{attempt}): {safety_msg}")
                if attempt == self.MAX_RETRIES:
                    return {
                        "requirement": requirement,
                        "code": code,
                        "output": "",
                        "result": None,
                        "success": False,
                        "error": f"安全审查失败: {safety_msg}",
                        "attempts": attempt,
                        "safe": False,
                    }
                context["last_error"] = f"安全审查失败: {safety_msg}"
                continue

            # ③ 执行
            exec_result = self._get_action()._sandbox_exec(code)

            if exec_result.get("error"):
                # 执行失败，反馈错误给LLM重试
                error = exec_result["error"]
                logger.info(f"代码执行失败(尝试{attempt}): {error}")
                if attempt == self.MAX_RETRIES:
                    return {
                        "requirement": requirement,
                        "code": code,
                        "output": exec_result.get("output", ""),
                        "result": exec_result.get("result"),
                        "success": False,
                        "error": error,
                        "attempts": attempt,
                        "safe": True,
                    }
                context["last_error"] = error
                context["last_code"] = code
                continue

            # 成功
            return {
                "requirement": requirement,
                "code": code,
                "output": exec_result.get("output", ""),
                "result": exec_result.get("result"),
                "success": True,
                "error": None,
                "attempts": attempt,
                "safe": True,
                "generated_at": datetime.now().isoformat(),
            }

        # 不应到达
        return {
            "requirement": requirement,
            "code": "",
            "output": "",
            "result": None,
            "success": False,
            "error": "生成失败",
            "attempts": self.MAX_RETRIES,
            "safe": False,
        }

    def generate_only(self, requirement: str,
                      context: Optional[Dict] = None) -> Dict[str, Any]:
        """仅生成代码（不执行）"""
        code = self._generate_code(requirement, context or {}, 1)
        is_safe, msg = self._audit_code(code)
        return {
            "requirement": requirement,
            "code": code,
            "safe": is_safe,
            "safety_msg": msg,
        }

    def _generate_code(self, requirement: str, context: Dict, attempt: int) -> str:
        """调用LLM生成代码"""
        llm = self._get_llm()

        if llm and llm.mode != "rule_based":
            prompt = self._build_prompt(requirement, context, attempt)
            try:
                result = llm.chat(prompt, system_prompt="coding")
                content = result.get("content", "") if isinstance(result, dict) else str(result)
                code = self._extract_code(content)
                if code:
                    return code
            except Exception as e:
                logger.warning(f"LLM生成代码失败: {e}")

        # 降级：模板代码
        return self._template_code(requirement, context)

    def _build_prompt(self, requirement: str, context: Dict, attempt: int) -> str:
        """构建代码生成提示词"""
        prompt = f"""请生成Python代码完成以下任务。

需求: {requirement}

约束:
1. 不能使用import语句
2. 不能使用open/eval/exec/__import__/getattr等危险函数
3. 不能访问下划线属性（如__class__）
4. 可用内置函数: print, len, range, sum, max, min, sorted, str, int, float, list, dict, set, tuple, enumerate, zip, map, filter
5. 可用模块: math, json, time, re, datetime
6. 用print输出结果
7. 只输出代码，不要解释

```python
# 你的代码
```"""

        if context.get("last_error"):
            prompt += f"\n\n上次执行失败，错误: {context['last_error']}\n请修复并重新生成。"

        if context.get("last_code"):
            prompt += f"\n\n上次代码:\n```python\n{context['last_code']}\n```"

        return prompt

    def _extract_code(self, content: str) -> str:
        """从LLM回复中提取代码块"""
        m = re.search(r'```python\s*(.*?)\s*```', content, re.S)
        if m:
            return m.group(1).strip()
        m = re.search(r'```\s*(.*?)\s*```', content, re.S)
        if m:
            return m.group(1).strip()
        # 无代码块标记，直接返回
        return content.strip()

    def _template_code(self, requirement: str, context: Dict) -> str:
        """降级模板代码"""
        return f"""# 模板代码（LLM不可用）
print("需求: {requirement}")
print("提示: 请配置LLM以获得智能代码生成能力")
result = "template"
print(f"结果: {{result}}")"""

    def _audit_code(self, code: str) -> tuple:
        """AST安全审查

        Returns:
            (is_safe, error_msg)
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            # 尝试表达式模式
            try:
                tree = ast.parse(code, mode='eval')
            except SyntaxError as e2:
                return False, f"语法错误: {e2}"

        for node in ast.walk(tree):
            # 禁止属性访问（dunder）
            if isinstance(node, ast.Attribute):
                if node.attr.startswith('_'):
                    return False, f"禁止访问下划线属性: {node.attr}"
                if node.attr in self.FORBIDDEN_ATTRS:
                    return False, f"禁止访问危险属性: {node.attr}"
            # 禁止危险函数调用
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in self.FORBIDDEN_FUNCS:
                    return False, f"禁止调用危险函数: {node.func.id}"
            # 禁止import
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return False, "禁止import语句"

        return True, ""


# ─── 单例 ────────────────────────────────────
_codegen_instance: Optional[CodeGenerator] = None


def get_code_generator() -> CodeGenerator:
    """获取代码生成器单例"""
    global _codegen_instance
    if _codegen_instance is None:
        _codegen_instance = CodeGenerator()
    return _codegen_instance
