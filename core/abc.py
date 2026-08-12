# -*- coding: utf-8 -*-
"""
core/abc.py — 抽象基类与 Mixin（公共契约层）
================================================
消除跨层重复方法：PersistableMixin / StatusableMixin / ExecutableMixin / SearchableMixin

设计原则：
  - Mixin 只提供"接口契约 + 通用样板代码"，不侵入业务逻辑
  - 业务类多继承 Mixin 获得接口一致性，各自实现具体逻辑
  - 通用 JSON 持久化样板（_persist_json / _restore_json）消除 11+ 处重复

架构归属：core/（横切关注点，独立于 D/W1/W2/M 四层）
"""
import os
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

logger = logging.getLogger("SCU3.core.abc")


class PersistableMixin:
    """持久化 Mixin：提供通用 JSON 读写样板，消除 _load_state / _save_state 重复

    用法：
        class MyManager(PersistableMixin):
            def _state_path(self) -> str:
                return "SCU3_data/my_state.json"

            def _serialize_state(self) -> dict:
                return {"data": self._data}

            def _deserialize_state(self, state: dict) -> None:
                self._data = state.get("data", {})

            # _load_state / _save_state 由 Mixin 提供，无需自己写
    """

    def _state_path(self) -> str:
        """子类必须覆写：返回状态文件路径"""
        raise NotImplementedError("子类必须实现 _state_path")

    def _serialize_state(self) -> dict:
        """子类必须覆写：返回可 JSON 序列化的状态字典"""
        raise NotImplementedError("子类必须实现 _serialize_state")

    def _deserialize_state(self, state: dict) -> None:
        """子类必须覆写：从状态字典恢复内存状态"""
        raise NotImplementedError("子类必须实现 _deserialize_state")

    def _load_state(self) -> None:
        """通用加载逻辑（Mixin 提供，子类无需覆写）"""
        path = self._state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._deserialize_state(state)
            logger.debug(f"{self.__class__.__name__}: 状态加载成功")
        except Exception as e:
            logger.warning(f"{self.__class__.__name__}: 加载状态失败: {e}")

    def _save_state(self) -> None:
        """通用保存逻辑（Mixin 提供，子类无需覆写）"""
        path = self._state_path()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            state = self._serialize_state()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"{self.__class__.__name__}: 保存状态失败: {e}")

    # ─── 更底层的通用工具（供非标准持久化场景使用）────────────

    @staticmethod
    def _persist_json(path: str, data: dict) -> bool:
        """通用 JSON 写入（带目录创建和异常处理）"""
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"JSON 持久化失败 {path}: {e}")
            return False

    @staticmethod
    def _restore_json(path: str, default: Optional[dict] = None) -> dict:
        """通用 JSON 读取（文件不存在或异常时返回 default）"""
        if default is None:
            default = {}
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"JSON 恢复失败 {path}: {e}")
            return default


class StatusableMixin:
    """状态查询 Mixin：统一 get_status / stats / status 接口

    用法：
        class MyManager(StatusableMixin):
            def get_status(self) -> dict:
                return {"count": len(self._items)}
            # stats / status 自动别名到 get_status
    """

    def get_status(self) -> Dict[str, Any]:
        """子类必须覆写：返回状态字典"""
        raise NotImplementedError("子类必须实现 get_status")

    def stats(self) -> Dict[str, Any]:
        """别名：等价于 get_status（消除 7 处重复）"""
        return self.get_status()

    def status(self) -> Dict[str, Any]:
        """别名：等价于 get_status（消除 8 处重复）"""
        return self.get_status()

    def info(self) -> Dict[str, Any]:
        """别名：等价于 get_status"""
        return self.get_status()


class ExecutableMixin:
    """执行 Mixin：统一 execute / process / run / call 接口

    用法：
        class MyEngine(ExecutableMixin):
            def execute(self, *args, **kwargs):
                # 具体执行逻辑
                pass
            # process / run / call 自动别名到 execute
    """

    def execute(self, *args, **kwargs) -> Any:
        """子类必须覆写：核心执行逻辑"""
        raise NotImplementedError("子类必须实现 execute")

    def process(self, *args, **kwargs) -> Any:
        """别名：等价于 execute（消除 5 处重复）"""
        return self.execute(*args, **kwargs)

    def run(self, *args, **kwargs) -> Any:
        """别名：等价于 execute"""
        return self.execute(*args, **kwargs)

    def call(self, *args, **kwargs) -> Any:
        """别名：等价于 execute"""
        return self.execute(*args, **kwargs)


class SearchableMixin:
    """搜索 Mixin：统一 search / query / find 接口

    用法：
        class MyStore(SearchableMixin):
            def search(self, query: str, top_k: int = 5) -> list:
                # 具体搜索逻辑
                return results
            # query / find 自动别名到 search
    """

    def search(self, query: str, top_k: int = 5) -> List[Any]:
        """子类必须覆写：搜索并返回结果列表"""
        raise NotImplementedError("子类必须实现 search")

    def query(self, query: str, top_k: int = 5) -> List[Any]:
        """别名：等价于 search"""
        return self.search(query, top_k)

    def find(self, query: str, top_k: int = 5) -> List[Any]:
        """别名：等价于 search"""
        return self.search(query, top_k)

    def lookup(self, query: str, top_k: int = 5) -> List[Any]:
        """别名：等价于 search"""
        return self.search(query, top_k)


class PerceivableMixin:
    """感知 Mixin：统一感知层接口契约

    约定感知层 perceive() 输出的 ctx 必填字段：
      - perceived: 清洗+脱敏后的用户输入（下游层直接消费，无需重复脱敏）
      - perceived_raw: 原始用户输入（仅供本地日志，不外发给云端LLM）
      - intent: 意图标签
      - domain: 领域标签
      - language: 语言标签（zh/en/mixed）
      - sanitized: 是否已完成脱敏（True时下游跳过重复脱敏）
      - perception_ok: 感知是否成功

    用法：
        class PerceptionLayer(PerceivableMixin):
            def perceive(self, user_input, ctx, history):
                # 具体感知逻辑
                return ctx
    """

    def perceive(self, user_input: str, ctx: Dict[str, Any] = None,
                 history: List[Dict] = None) -> Dict[str, Any]:
        """子类必须覆写：统一感知入口"""
        raise NotImplementedError("子类必须实现 perceive")


class SanitizableMixin:
    """清洗 Mixin：统一输入清洗/PII脱敏接口

    用法：
        class PerceptionLayer(SanitizableMixin):
            def sanitize(self, text):
                # 调用 guard.ContentFilter 做PII脱敏
                return cleaned_text, alerts
            def detect_pii(self, text):
                # 仅检测不替换
                return pii_items
    """

    def sanitize(self, text: str) -> Tuple[str, List[str]]:
        """子类必须覆写：清洗输入并返回 (清洗后文本, 命中告警列表)"""
        raise NotImplementedError("子类必须实现 sanitize")

    def detect_pii(self, text: str) -> List[str]:
        """子类必须覆写：仅检测PII不替换，返回命中项列表"""
        raise NotImplementedError("子类必须实现 detect_pii")
