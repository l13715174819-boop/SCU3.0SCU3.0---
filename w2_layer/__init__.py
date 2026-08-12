# -*- coding: utf-8 -*-
"""W2 层包初始化 — 感知层

导出 PerceptionLayer 和模块级单例便捷函数，与 m_layer/w1_layer 单例模式一致。
"""
from w2_layer.perception import PerceptionLayer

_perception_instance = None


def get_perception_layer() -> PerceptionLayer:
    """获取 PerceptionLayer 单例"""
    global _perception_instance
    if _perception_instance is None:
        _perception_instance = PerceptionLayer()
    return _perception_instance
