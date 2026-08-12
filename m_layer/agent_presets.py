# -*- coding: utf-8 -*-
"""
预置 Agent 工作流
==================
基于 TRAE AI 助手的工作模式，抽象为 5 种 Agent 角色 + 6 个实用预置工作流。

角色映射：
  研究员 (search)    → 联网搜索、爬取、知识检索
  分析师 (analysis)  → 深度分析、批判思考、对比
  写手   (writing)   → 生成报告、文案、文档
  工程师 (coding)    → 写代码、调试、重构
  协调员 (general)   → 任务拆解、流程编排

调用方式：
  POST /multiagent/execute
  body: { "mode": "thread", "subtasks": [...] }
"""
import os
import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger("SCU3.m.agent_presets")

# ─── 预置工作流定义 ──────────────────────────────────
# 每个工作流包含: id, name, description, mode, goal_template, subtasks
# goal_template 中的 {topic} 占位符在调用时被用户输入替换

PRESETS: List[Dict[str, Any]] = [
    {
        "id": "research_report",
        "name": "深度研究报告",
        "description": "研究员搜集资料 → 分析师提炼洞察 → 写手生成结构化报告",
        "icon": "📋",
        "mode": "thread",
        "goal_template": "针对主题「{topic}」生成深度研究报告",
        "subtasks": [
            {
                "subtask": "联网搜索「{topic}」的最新资讯、权威来源、关键数据，汇总成资料卡",
                "specialty": "search"
            },
            {
                "subtask": "基于研究资料，提炼3-5个核心洞察，对比不同观点，指出争议点和共识",
                "specialty": "analysis",
                "depends_on": ["t1"]
            },
            {
                "subtask": "综合洞察生成结构化报告：背景、核心观点、数据支撑、结论与建议",
                "specialty": "writing",
                "depends_on": ["t2"]
            }
        ]
    },
    {
        "id": "code_solution",
        "name": "代码方案生成",
        "description": "研究员调研最佳实践 → 工程师实现代码 → 分析师审查质量",
        "icon": "💻",
        "mode": "thread",
        "goal_template": "针对需求「{topic}」生成完整代码方案",
        "subtasks": [
            {
                "subtask": "搜索「{topic}」的主流实现方案、开源库、最佳实践，对比优劣",
                "specialty": "search"
            },
            {
                "subtask": "基于调研结果，编写完整可运行的代码方案，包含核心实现、接口设计、错误处理",
                "specialty": "coding",
                "depends_on": ["t1"]
            },
            {
                "subtask": "审查代码方案的：安全性、性能、可维护性、边界情况，给出改进建议",
                "specialty": "analysis",
                "depends_on": ["t2"]
            }
        ]
    },
    {
        "id": "decision_analysis",
        "name": "决策分析",
        "description": "研究员搜集信息 → 分析师利弊分析 → 写手输出决策建议",
        "icon": "⚖️",
        "mode": "thread",
        "goal_template": "针对决策「{topic}」提供深度分析建议",
        "subtasks": [
            {
                "subtask": "搜集「{topic}」的背景信息、相关案例、利益相关方观点",
                "specialty": "search"
            },
            {
                "subtask": "进行利弊分析：列出3个支持理由、3个反对理由、潜在风险、机会成本",
                "specialty": "analysis",
                "depends_on": ["t1"]
            },
            {
                "subtask": "输出决策建议书：情境分析、选项对比、推荐方案、实施要点",
                "specialty": "writing",
                "depends_on": ["t2"]
            }
        ]
    },
    {
        "id": "content_creation",
        "name": "内容创作",
        "description": "研究员搜集素材 → 写手生成初稿 → 分析师优化润色",
        "icon": "✍️",
        "mode": "thread",
        "goal_template": "围绕主题「{topic}」创作高质量内容",
        "subtasks": [
            {
                "subtask": "搜集「{topic}」的热点角度、受众痛点、爆款案例、关键词",
                "specialty": "search"
            },
            {
                "subtask": "基于素材创作内容：吸引人的标题、清晰的结构、有价值的观点、行动号召",
                "specialty": "writing",
                "depends_on": ["t1"]
            },
            {
                "subtask": "优化内容：逻辑连贯性、表达感染力、可读性、传播性，提出修改建议",
                "specialty": "analysis",
                "depends_on": ["t2"]
            }
        ]
    },
    {
        "id": "bug_investigation",
        "name": "Bug 排查",
        "description": "研究员搜索类似问题 → 工程师定位根因 → 分析师输出修复方案",
        "icon": "🐛",
        "mode": "thread",
        "goal_template": "排查并解决 Bug：「{topic}」",
        "subtasks": [
            {
                "subtask": "搜索「{topic}」相关的已知问题、Stack Overflow 讨论、官方文档说明",
                "specialty": "search"
            },
            {
                "subtask": "分析可能的根因：代码逻辑、依赖冲突、环境配置、数据问题，给出排查步骤",
                "specialty": "coding",
                "depends_on": ["t1"]
            },
            {
                "subtask": "输出修复方案：根因分析、修复步骤、验证方法、预防措施",
                "specialty": "analysis",
                "depends_on": ["t2"]
            }
        ]
    },
    {
        "id": "learning_path",
        "name": "学习路径规划",
        "description": "研究员搜集学习资源 → 分析师评估路径 → 写手生成学习计划",
        "icon": "📚",
        "mode": "thread",
        "goal_template": "为主题「{topic}」规划系统学习路径",
        "subtasks": [
            {
                "subtask": "搜集「{topic}」的优质学习资源：书籍、课程、文档、社区、实战项目",
                "specialty": "search"
            },
            {
                "subtask": "评估学习路径：前置知识、核心概念、进阶方向、常见误区、时间预估",
                "specialty": "analysis",
                "depends_on": ["t1"]
            },
            {
                "subtask": "生成学习计划：分阶段目标、每周任务、推荐资源、检验标准、里程碑",
                "specialty": "writing",
                "depends_on": ["t2"]
            }
        ]
    }
]


def list_presets() -> List[Dict[str, Any]]:
    """返回所有预置工作流（脱敏，不含 subtasks 详情）"""
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "icon": p["icon"],
            "mode": p["mode"],
            "goal_template": p["goal_template"],
        }
        for p in PRESETS
    ]


def get_preset(preset_id: str) -> Dict[str, Any] | None:
    """获取指定预置工作流的完整定义"""
    for p in PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def build_request(preset_id: str, topic: str) -> Dict[str, Any] | None:
    """根据预置工作流 + 用户输入主题，构建 /multiagent/execute 请求体

    Args:
        preset_id: 预置工作流ID
        topic: 用户输入的主题（如"GPT-5"、"用户登录Bug"）

    Returns:
        可直接 POST 到 /multiagent/execute 的请求体，或 None
    """
    preset = get_preset(preset_id)
    if not preset:
        return None

    # 替换 subtask 中的 {topic} 占位符
    subtasks = []
    for i, st in enumerate(preset["subtasks"], 1):
        task = {
            "subtask": st["subtask"].replace("{topic}", topic),
            "specialty": st.get("specialty", "general"),
        }
        if "depends_on" in st:
            task["depends_on"] = st["depends_on"]
        subtasks.append(task)

    return {
        "mode": preset["mode"],
        "goal": preset["goal_template"].replace("{topic}", topic),
        "subtasks": subtasks,
    }
