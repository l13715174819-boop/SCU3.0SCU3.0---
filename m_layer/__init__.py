# -*- coding: utf-8 -*-
"""M 层包初始化 — 元认知/认知层

子包结构（物理迁移后）：
  - orchestration/：任务编排（拆解/执行/反思/多Agent/并行/分布式）
  - plugins/：插件与扩展（插件系统/MCP/工具选择/权限）
  - _multimodal/：多模态（图像/音频/视频/语音）
  - evolution/：自演化（自修改/自学习/经验/元认知/认知）

根目录保留基础设施：
  - llm_client.py：LLM 客户端（被所有子包依赖）
  - module_registry.py：模块注册表

向后兼容：通过 sys.modules 别名保持 `from m_layer.xxx import` 可用。
所有原 m_layer 根目录的模块已物理迁移到子包，但导入路径不变。
"""
import sys

# ═══════════════════════════════════════════════════════════
#  导入子包模块并注册向后兼容别名
# ═══════════════════════════════════════════════════════════
# 机制：Python 导入 m_layer 包时执行本 __init__.py，
# 先导入子包模块（触发其 __init__.py），再注册 sys.modules 别名。
# 之后 `from m_layer.xxx import Y` 会在 sys.modules 中找到别名。

# ── orchestration/（任务编排，15 个模块）──
from m_layer.orchestration import (
    agent_learning,
    agent_presets,
    condition_branch,
    conversation_context,
    distributed_executor,
    multi_agent,
    parallel_executor,
    reflection,
    retry_strategy,
    task_executor,
    task_persistence,
    task_planner,
    task_template,
    tool_chain,
    visualizer,
)

# ── plugins/（插件与扩展，6 个模块）──
from m_layer.plugins import (
    mcp_protocol,
    nl_tool_selector,
    plugin_market,
    plugin_system,
    tool_permissions,
    tool_preference,
)

# ── _multimodal/（多模态，3 个模块）──
from m_layer._multimodal import (
    local_model,
    multimodal,
    voice_io,
)

# ── evolution/（自演化，8 个模块）──
from m_layer.evolution import (
    code_generator,
    code_self_modify,
    cognition,
    cognition_endorser,
    experience_store,
    metacognition,
    self_evolution,
    self_learning,
)

# ═══════════════════════════════════════════════════════════
#  注册 sys.modules 别名（向后兼容）
# ═══════════════════════════════════════════════════════════
#  注册后 `from m_layer.task_planner import X` 等价于
#  `from m_layer.orchestration.task_planner import X`
_COMPAT_ALIASES = {
    # orchestration/
    "m_layer.agent_learning": agent_learning,
    "m_layer.agent_presets": agent_presets,
    "m_layer.condition_branch": condition_branch,
    "m_layer.conversation_context": conversation_context,
    "m_layer.distributed_executor": distributed_executor,
    "m_layer.multi_agent": multi_agent,
    "m_layer.parallel_executor": parallel_executor,
    "m_layer.reflection": reflection,
    "m_layer.retry_strategy": retry_strategy,
    "m_layer.task_executor": task_executor,
    "m_layer.task_persistence": task_persistence,
    "m_layer.task_planner": task_planner,
    "m_layer.task_template": task_template,
    "m_layer.tool_chain": tool_chain,
    "m_layer.visualizer": visualizer,
    # plugins/
    "m_layer.mcp_protocol": mcp_protocol,
    "m_layer.nl_tool_selector": nl_tool_selector,
    "m_layer.plugin_market": plugin_market,
    "m_layer.plugin_system": plugin_system,
    "m_layer.tool_permissions": tool_permissions,
    "m_layer.tool_preference": tool_preference,
    # _multimodal/
    "m_layer.local_model": local_model,
    "m_layer.multimodal": multimodal,
    "m_layer.voice_io": voice_io,
    # evolution/
    "m_layer.code_generator": code_generator,
    "m_layer.code_self_modify": code_self_modify,
    "m_layer.cognition": cognition,
    "m_layer.cognition_endorser": cognition_endorser,
    "m_layer.experience_store": experience_store,
    "m_layer.metacognition": metacognition,
    "m_layer.self_evolution": self_evolution,
    "m_layer.self_learning": self_learning,
}

for _alias, _mod in _COMPAT_ALIASES.items():
    sys.modules[_alias] = _mod
