# -*- coding: utf-8 -*-
"""
m_layer/experience_store.py — 经验存储与预加载（M层）
====================================================
闭环关键：让程序"记住"成功路径，下次直接命中，不再走 all_failed。

经验生命周期：
  1. 沉淀：插件成功执行后，记录 {pattern, plugin, tool, success_count}
  2. 召回：工具执行前，按 pattern 查询是否有成熟方案
  3. 预加载：命中经验 → 直接 install_and_load 对应插件（跳过 all_failed）
  4. 强化：每次成功 success_count+1，次数越高优先级越高
  5. 衰减：长时间未用的经验降权（>30天）

存储格式（SCU3_data/experiences.json）：
  {
    "experiences": [
      {
        "pattern": "读取*.pdf",
        "pattern_type": "extension",  // extension/keyword/regex
        "intent": "document_read",
        "plugin": "pdf_reader",
        "tool": "pdf_read",
        "success_count": 3,
        "fail_count": 0,
        "first_used": "2026-08-11T...",
        "last_used": "2026-08-11T...",
        "auto_load": true
      }
    ]
  }
"""
import os
import re
import json
import time
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from core.abc import PersistableMixin, StatusableMixin, SearchableMixin

logger = logging.getLogger("SCU3.m.experience")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "SCU3_data")
EXP_PATH = os.path.join(DATA_DIR, "experiences.json")


class ExperienceStore(PersistableMixin, StatusableMixin, SearchableMixin):
    """经验存储 — 沉淀成功路径 + 预加载工具

    用法：
        store = get_experience_store()
        # 沉淀经验
        store.record_success("读取 report.pdf", "pdf_reader", "pdf_read", "document_read")
        # 查询经验
        exp = store.match_experience("读取 test.pdf")
        if exp:
            # 直接预加载插件，跳过 all_failed 流程
            market.install_and_load(exp["plugin"])
    """

    # 经验衰减阈值（天）
    DECAY_DAYS = 30
    # 高频经验阈值（超过此次数视为"成熟方案"）
    MATURE_THRESHOLD = 2

    def __init__(self):
        self._lock = threading.RLock()
        self._experiences: List[Dict] = []
        self._load()

    # ─── 持久化（PersistableMixin 接口实现）────────────

    def _state_path(self) -> str:
        return EXP_PATH

    def _serialize_state(self) -> dict:
        return {"experiences": self._experiences, "updated_at": datetime.now().isoformat()}

    def _deserialize_state(self, state: dict) -> None:
        self._experiences = state.get("experiences", [])
        logger.info(f"经验存储加载: {len(self._experiences)} 条经验")

    # 保留 _load / _save 作为兼容别名（Mixin 的 _load_state/_save_state 是标准入口）
    def _load(self):
        self._load_state()

    def _save(self):
        self._save_state()

    # ─── 经验沉淀 ────────────────────────────────────

    def record_success(self, user_input: str, plugin_name: str,
                       tool_name: str, intent: str = "") -> Dict[str, Any]:
        """记录一次成功的插件使用经验

        Args:
            user_input: 用户原始输入
            plugin_name: 成功使用的插件名
            tool_name: 成功使用的工具名
            intent: 意图（可选）

        Returns:
            {success, record_action, message}
        """
        with self._lock:
            # 提取模式（扩展名优先，否则用关键词）
            pattern, pattern_type = self._extract_pattern(user_input)

            # 查找是否已有相同经验的记录
            for exp in self._experiences:
                if (exp["pattern"] == pattern and
                        exp["plugin"] == plugin_name and
                        exp["tool"] == tool_name):
                    # 强化已有经验
                    exp["success_count"] += 1
                    exp["last_used"] = datetime.now().isoformat()
                    exp["auto_load"] = True
                    self._save()
                    logger.info(f"经验强化: {pattern} → {plugin_name} (成功{exp['success_count']}次)")
                    return {"success": True, "action": "reinforced",
                            "message": f"经验强化，累计成功{exp['success_count']}次"}

            # 新经验
            new_exp = {
                "pattern": pattern,
                "pattern_type": pattern_type,
                "intent": intent,
                "plugin": plugin_name,
                "tool": tool_name,
                "success_count": 1,
                "fail_count": 0,
                "first_used": datetime.now().isoformat(),
                "last_used": datetime.now().isoformat(),
                "auto_load": True,
            }
            self._experiences.append(new_exp)
            self._save()
            logger.info(f"经验沉淀: {pattern} → {plugin_name}/{tool_name}")
            return {"success": True, "action": "created",
                    "message": f"新经验已沉淀: {pattern} → {plugin_name}"}

    def record_failure(self, user_input: str, plugin_name: str, tool_name: str):
        """记录失败经验（避免反复尝试已知失败的方案）"""
        with self._lock:
            pattern, _ = self._extract_pattern(user_input)
            for exp in self._experiences:
                if (exp["pattern"] == pattern and
                        exp["plugin"] == plugin_name):
                    exp["fail_count"] += 1
                    # 连续失败3次以上，禁用 auto_load
                    if exp["fail_count"] >= 3 and exp["success_count"] == 0:
                        exp["auto_load"] = False
                    self._save()
                    return
            # 记录失败经验
            self._experiences.append({
                "pattern": pattern,
                "pattern_type": "extension" if "." in pattern else "keyword",
                "intent": "",
                "plugin": plugin_name,
                "tool": tool_name,
                "success_count": 0,
                "fail_count": 1,
                "first_used": datetime.now().isoformat(),
                "last_used": datetime.now().isoformat(),
                "auto_load": False,  # 失败经验不自动加载
            })
            self._save()

    # ─── 经验匹配（预加载） ────────────────────────────────────

    def match_experience(self, user_input: str, tool_name: str = "") -> Optional[Dict]:
        """查询是否有匹配的成熟经验

        Args:
            user_input: 用户输入
            tool_name: 待执行的工具名（可选，用于精确匹配）

        Returns:
            匹配的经验记录，或 None
        """
        with self._lock:
            now = datetime.now()
            candidates = []

            for exp in self._experiences:
                if not exp.get("auto_load", True):
                    continue
                if exp.get("success_count", 0) < 1:
                    continue

                # 工具名精确匹配（优先级最高）
                if tool_name and exp["tool"] == tool_name:
                    candidates.append((exp, 100 + exp["success_count"]))
                    continue

                # 模式匹配
                pattern = exp["pattern"]
                pattern_type = exp.get("pattern_type", "extension")

                if pattern_type == "extension" and pattern in user_input.lower():
                    # 扩展名匹配
                    score = 50 + exp["success_count"]
                    # 衰减：30天前的经验降权
                    last_used = self._parse_time(exp.get("last_used", ""))
                    if last_used and (now - last_used).days > self.DECAY_DAYS:
                        score -= 20
                    candidates.append((exp, score))

                elif pattern_type == "keyword" and pattern.lower() in user_input.lower():
                    # 关键词匹配
                    score = 30 + exp["success_count"]
                    last_used = self._parse_time(exp.get("last_used", ""))
                    if last_used and (now - last_used).days > self.DECAY_DAYS:
                        score -= 20
                    candidates.append((exp, score))

            if not candidates:
                return None

            # 按得分降序，取最高分
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_exp = candidates[0][0]
            logger.info(f"经验命中: {best_exp['pattern']} → {best_exp['plugin']} "
                        f"(成功{best_exp['success_count']}次, 得分{candidates[0][1]})")
            return best_exp

    # ─── 预加载 ────────────────────────────────────

    def preload_tool_if_needed(self, user_input: str, tool_name: str) -> Dict[str, Any]:
        """预加载工具：如果经验命中且工具未注册，自动加载插件

        在 action.execute 之前调用，避免走 all_failed 流程。

        Args:
            user_input: 用户输入
            tool_name: 检测到的工具名

        Returns:
            {preloaded: bool, plugin: str, tool: str, message: str}
        """
        try:
            from w1_layer.action import ActionLayer
            action = ActionLayer()

            # 工具已注册，无需预加载
            if tool_name in action._tools:
                return {"preloaded": False, "message": "工具已注册"}

            # 查询经验
            exp = self.match_experience(user_input, tool_name)
            if exp is None:
                return {"preloaded": False, "message": "无匹配经验"}

            # 经验命中 → 直接加载插件
            plugin_name = exp["plugin"]
            logger.info(f"经验预加载: {plugin_name} (基于 {exp['pattern']} 经验)")

            from m_layer.plugin_market import get_marketplace
            market = get_marketplace()
            load_result = market.install_and_load(plugin_name)

            if load_result.get("success"):
                return {"preloaded": True, "plugin": plugin_name,
                        "tool": tool_name,
                        "message": f"已通过经验预加载 {plugin_name}"}
            else:
                # 预加载失败，记录失败经验
                self.record_failure(user_input, plugin_name, tool_name)
                return {"preloaded": False, "plugin": plugin_name,
                        "message": f"预加载失败: {load_result.get('error')}"}

        except Exception as e:
            logger.warning(f"经验预加载异常: {e}")
            return {"preloaded": False, "message": f"异常: {e}"}

    # ─── 模式提取 ────────────────────────────────────

    def _extract_pattern(self, text: str) -> tuple:
        """从用户输入提取模式

        优先级：扩展名 > 关键词
        Returns:
            (pattern, pattern_type)
        """
        text_lower = text.lower()
        # 扩展名模式
        ext_patterns = [r"\.pdf", r"\.docx", r"\.doc", r"\.xlsx", r"\.xls",
                        r"\.png", r"\.jpg", r"\.jpeg", r"\.gif", r"\.bmp"]
        for ext in ext_patterns:
            if re.search(ext, text_lower):
                return (ext.lstrip("\\.").lower(), "extension")

        # 关键词模式
        keywords = ["翻译", "二维码", "qrcode", "markdown", "md转",
                    "读取", "解析", "处理图片"]
        for kw in keywords:
            if kw in text_lower:
                return (kw, "keyword")

        # 默认：取前20字符作为模式
        return (text[:20].strip(), "keyword")

    def _parse_time(self, time_str: str) -> Optional[datetime]:
        """解析时间字符串"""
        try:
            return datetime.fromisoformat(time_str)
        except Exception:
            return None

    # ─── 查询 ────────────────────────────────────

    def list_experiences(self, mature_only: bool = False) -> List[Dict]:
        """列出所有经验"""
        with self._lock:
            if mature_only:
                return [e for e in self._experiences
                        if e.get("success_count", 0) >= self.MATURE_THRESHOLD]
            return list(self._experiences)

    def get_status(self) -> Dict[str, Any]:
        """经验存储状态"""
        with self._lock:
            total = len(self._experiences)
            mature = sum(1 for e in self._experiences
                         if e.get("success_count", 0) >= self.MATURE_THRESHOLD)
            auto_load = sum(1 for e in self._experiences if e.get("auto_load", True))
            return {
                "total_experiences": total,
                "mature_experiences": mature,
                "auto_load_enabled": auto_load,
                "decay_days": self.DECAY_DAYS,
                "mature_threshold": self.MATURE_THRESHOLD,
            }


# ─── 全局单例 ────────────────────────────────────

_experience_instance: Optional[ExperienceStore] = None
_experience_lock = threading.Lock()


def get_experience_store() -> ExperienceStore:
    """获取经验存储全局单例"""
    global _experience_instance
    if _experience_instance is None:
        with _experience_lock:
            if _experience_instance is None:
                _experience_instance = ExperienceStore()
    return _experience_instance
