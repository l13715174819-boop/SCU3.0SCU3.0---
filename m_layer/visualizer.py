# -*- coding: utf-8 -*-
"""
m_layer/visualizer.py — 执行流可视化（M层）
============================================
v5.0第三批：生成Mermaid流程图，可视化Agent执行过程

功能:
  1. 从执行计划生成Mermaid流程图
  2. 从执行报告生成状态图（成功/失败标记）
  3. 从多Agent任务生成协作图
  4. 输出Markdown/HTML格式

架构归属：M层（可视化层）
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("SCU3.m.viz")


class ExecutionVisualizer:
    """执行流可视化

    用法:
        viz = ExecutionVisualizer()
        # 从计划生成流程图
        mermaid = viz.plan_to_mermaid(plan)
        # 从执行报告生成状态图
        mermaid = viz.report_to_mermaid(report)
        # 生成HTML
        html = viz.to_html(mermaid)
    """

    def plan_to_mermaid(self, plan: Dict[str, Any]) -> str:
        """从执行计划生成Mermaid流程图"""
        steps = plan.get("steps", [])
        if not steps:
            return "graph TD\n  empty[无步骤]"

        lines = ["graph TD"]
        # 起始节点
        lines.append("  start([🎯 目标: {}])".format(self._escape(plan.get("goal", "")[:30])))

        # 步骤节点
        for step in steps:
            sid = step.get("step_id", 0)
            action = step.get("action", "")
            desc = step.get("description", "")[:20]
            node_id = f"s{sid}"
            lines.append(f'  {node_id}["{sid}. {action}\\n{desc}"]')

        # 起始→第一步
        if steps:
            lines.append(f"  start --> s{steps[0].get('step_id', 0)}")

        # 依赖关系
        for step in steps:
            sid = step.get("step_id", 0)
            deps = step.get("depends_on", [])
            for dep in deps:
                lines.append(f"  s{dep} --> s{sid}")

        # 结束节点
        if steps:
            last_id = steps[-1].get("step_id", 0)
            lines.append(f"  s{last_id} --> done([✅ 完成])")

        # 标记临时步骤
        for step in steps:
            if step.get("is_temporary"):
                sid = step.get("step_id", 0)
                lines.append(f"  s{sid} -.- temp[🗑️ 临时]")

        return "\n".join(lines)

    def report_to_mermaid(self, report: Dict[str, Any]) -> str:
        """从执行报告生成状态图"""
        steps = report.get("steps", [])
        if not steps:
            return "graph TD\n  empty[无步骤]"

        lines = ["graph TD"]
        success = report.get("success", False)
        goal = report.get("goal", "")[:30]

        # 起始节点
        lines.append("  start([🎯 {}])".format(self._escape(goal)))

        # 步骤节点（带状态颜色）
        for step in steps:
            sid = step.get("step_id", 0)
            action = step.get("action", "")
            status = step.get("status", "unknown")
            elapsed = step.get("elapsed_ms", 0)

            # 状态图标
            icon = {"done": "✅", "failed": "❌", "skipped": "⏭️", "running": "🔄"}.get(status, "❓")
            node_id = f"s{sid}"
            label = f"{sid}. {action}\\n{icon} {status} ({elapsed:.0f}ms)"
            lines.append(f'  {node_id}["{label}"]')

            # 状态着色
            if status == "done":
                lines.append(f"  style {node_id} fill:#90EE90")
            elif status == "failed":
                lines.append(f"  style {node_id} fill:#FFB6C1")
            elif status == "skipped":
                lines.append(f"  style {node_id} fill:#FFFACD")

        # 连线
        for i, step in enumerate(steps):
            sid = step.get("step_id", 0)
            if i == 0:
                lines.append(f"  start --> s{sid}")
            deps = step.get("depends_on", [])
            for dep in deps:
                lines.append(f"  s{dep} --> s{sid}")

        # 结束节点
        icon = "✅" if success else "❌"
        if steps:
            last_id = steps[-1].get("step_id", 0)
            lines.append(f"  s{last_id} --> done([{icon} {'成功' if success else '失败'}])")

        # 添加统计信息
        elapsed = report.get("elapsed_ms", 0)
        lines.append(f'  info["📊 总耗时: {elapsed:.0f}ms"]')

        return "\n".join(lines)

    def multi_agent_to_mermaid(self, report: Dict[str, Any]) -> str:
        """多Agent协作图"""
        subtasks = report.get("subtasks", [])
        if not subtasks:
            return "graph TD\n  empty[无子任务]"

        lines = ["graph TD"]
        lines.append("  main([🤖 主Agent])")

        for st in subtasks:
            sid = st["subtask_id"]
            specialty = st.get("specialty", "general")
            status = st.get("status", "unknown")
            subtask_desc = st["subtask"][:20]

            icon = {"done": "✅", "failed": "❌", "running": "🔄", "pending": "⏳"}.get(status, "❓")
            lines.append(f'  {sid}["{specialty}\\n{subtask_desc}\\n{icon}"]')
            lines.append(f"  main --> {sid}")

            # 依赖
            for dep in st.get("depends_on", []):
                lines.append(f"  {dep} --> {sid}")

        # 汇总
        lines.append("  main --> summary([📋 汇总报告])")

        return "\n".join(lines)

    def to_html(self, mermaid: str, title: str = "执行流可视化") -> str:
        """生成包含Mermaid图的HTML"""
        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
    <style>
        body {{ font-family: sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; }}
        .mermaid {{ text-align: center; margin: 20px 0; }}
        .meta {{ color: #666; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        <div class="mermaid">
{mermaid}
        </div>
        <div class="meta">生成时间: {datetime.now().isoformat()}</div>
    </div>
    <script>mermaid.initialize({{startOnLoad: true}});</script>
</body>
</html>"""

    def to_markdown(self, mermaid: str, title: str = "执行流") -> str:
        """生成Markdown格式"""
        return f"""# {title}

```mermaid
{mermaid}
```

生成时间: {datetime.now().isoformat()}
"""

    def _escape(self, text: str) -> str:
        """转义Mermaid特殊字符"""
        return text.replace('"', "'").replace("[", "(").replace("]", ")")


# ─── 单例 ────────────────────────────────────
_viz_instance: Optional[ExecutionVisualizer] = None


def get_visualizer() -> ExecutionVisualizer:
    """获取可视化器单例"""
    global _viz_instance
    if _viz_instance is None:
        _viz_instance = ExecutionVisualizer()
    return _viz_instance
