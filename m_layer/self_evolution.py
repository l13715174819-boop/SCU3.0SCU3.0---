# -*- coding: utf-8 -*-
"""
m_layer/self_evolution.py — 自进化引擎（M层）
====================================================
让程序主动发现自身不足，自动生成修改方案，提交审核后自我改进。

闭环流程：
  ① 触发（失败经验≥3 / 负面反馈≥5 / 周期24h / 手动API）
  ② DefectAnalyzer 扫描不足 → 生成缺陷报告
  ③ ProposalGenerator 调用 LLM 生成代码补丁
  ④ code_modifier.propose_modification() 提交方案
  ⑤ 走安全审查 + 阴阳双签 → pending_approval
  ⑥ 用户审批 → 自动应用 + 备份
  ⑦ 验证 + 记录经验

架构归属：M层（元认知层的最高级扩展）
安全约束：
  - 不直接修改代码，只生成提案交 code_modifier 审查
  - D层保护文件不可触碰（由 code_modifier 强制）
  - 所有提案需阴阳双签 + 人工审批
  - 失败的提案记录原因，避免重复生成
"""
import os
import re
import json
import time
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger("scu2.m.evolution")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "scu2_data")
EVOLUTION_STATE_PATH = os.path.join(DATA_DIR, "evolution_state.json")

# 触发阈值
TRIGGER_FAIL_COUNT = 3        # 失败经验≥3次触发
TRIGGER_NEGATIVE_FEEDBACK = 5 # 负面反馈≥5次触发
TRIGGER_PERIODIC_HOURS = 24   # 周期触发：24小时
TRIGGER_CHECK_INTERVAL_SEC = 600  # 后台检查间隔：10分钟


class DefectAnalyzer:
    """缺陷分析器 — 扫描经验存储、反馈、日志，识别程序不足

    数据源：
      1. experience_store: 失败经验（fail_count≥3 且 success_count=0）
      2. feedback_collector: 负面反馈模式
      3. 错误日志：异常模式（可选）
    """

    def __init__(self):
        pass

    def scan(self) -> List[Dict[str, Any]]:
        """扫描所有数据源，返回缺陷列表

        Returns:
            [{defect_id, source, pattern, description, severity, evidence, suggested_files}]
        """
        defects = []

        # 来源1：失败经验
        defects.extend(self._scan_failed_experiences())

        # 来源2：负面反馈
        defects.extend(self._scan_negative_feedback())

        return defects

    def _scan_failed_experiences(self) -> List[Dict]:
        """扫描经验存储中的失败模式"""
        defects = []
        try:
            from m_layer.experience_store import get_experience_store
            store = get_experience_store()
            experiences = store.list_experiences()

            for exp in experiences:
                fail_count = exp.get("fail_count", 0)
                success_count = exp.get("success_count", 0)
                # 失败次数≥3且无成功记录 → 缺陷
                if fail_count >= TRIGGER_FAIL_COUNT and success_count == 0:
                    defect = {
                        "defect_id": f"defect_fail_{exp.get('pattern', 'unknown')[:20]}",
                        "source": "failed_experience",
                        "pattern": exp.get("pattern", ""),
                        "pattern_type": exp.get("pattern_type", ""),
                        "intent": exp.get("intent", ""),
                        "description": (f"模式 '{exp.get('pattern', '')}' 连续失败 {fail_count} 次，"
                                        f"插件 {exp.get('plugin', '')} 无成功记录"),
                        "severity": "high" if fail_count >= 5 else "medium",
                        "evidence": {
                            "fail_count": fail_count,
                            "success_count": success_count,
                            "plugin": exp.get("plugin", ""),
                            "tool": exp.get("tool", ""),
                        },
                        "suggested_files": self._locate_related_files(exp),
                    }
                    defects.append(defect)
                    logger.info(f"缺陷识别[失败经验]: {defect['defect_id']} ({fail_count}次失败)")
        except Exception as e:
            logger.warning(f"扫描失败经验异常: {e}")
        return defects

    def _scan_negative_feedback(self) -> List[Dict]:
        """扫描负面反馈"""
        defects = []
        try:
            from feedback.collector import FeedbackCollector
            # 反馈收集器是全局单例，通过 server.py 初始化
            # 这里尝试从已初始化的实例获取
            import feedback.collector as fc_module
            if not hasattr(fc_module, '_global_collector'):
                return defects
            collector = fc_module._global_collector
            if collector is None:
                return defects

            # 获取所有反馈
            all_feedback = collector.get_all_feedback() if hasattr(collector, 'get_all_feedback') else []
            # 按 pattern_key 聚合负面反馈
            negative_by_pattern = {}
            for fb in all_feedback:
                if fb.get("kind") in ("thumbs_down", "negative", "bad"):
                    pk = fb.get("pattern_key", "unknown")
                    negative_by_pattern.setdefault(pk, []).append(fb)

            for pattern_key, items in negative_by_pattern.items():
                if len(items) >= TRIGGER_NEGATIVE_FEEDBACK:
                    defect = {
                        "defect_id": f"defect_feedback_{pattern_key[:20]}",
                        "source": "negative_feedback",
                        "pattern": pattern_key,
                        "description": (f"模式 '{pattern_key}' 收到 {len(items)} 次负面反馈，"
                                        f"用户满意度低"),
                        "severity": "high" if len(items) >= 10 else "medium",
                        "evidence": {
                            "negative_count": len(items),
                            "sample_feedback": items[-3:],
                        },
                        "suggested_files": self._locate_files_by_pattern(pattern_key),
                    }
                    defects.append(defect)
                    logger.info(f"缺陷识别[负面反馈]: {defect['defect_id']} ({len(items)}次差评)")
        except Exception as e:
            logger.debug(f"扫描负面反馈异常（不阻塞）: {e}")
        return defects

    def _locate_related_files(self, exp: Dict) -> List[str]:
        """根据经验记录定位相关源码文件"""
        plugin = exp.get("plugin", "")
        tool = exp.get("tool", "")
        intent = exp.get("intent", "")

        # 工具→文件映射
        file_map = {
            "pdf_read": ["w1_layer/action.py"],
            "docx_read": ["w1_layer/action.py"],
            "excel_read": ["w1_layer/action.py"],
            "qrcode_gen": ["w1_layer/action.py", "m_layer/plugin_market.py"],
            "translate": ["w1_layer/action.py", "m_layer/plugin_market.py"],
            "web_search": ["w1_layer/action.py"],
            "web_crawl": ["w1_layer/action.py"],
        }
        files = file_map.get(tool, [])

        # 意图→文件映射
        intent_map = {
            "document_read": ["w1_layer/action.py", "m_layer/plugin_market.py"],
            "translate": ["w1_layer/action.py", "m_layer/plugin_market.py"],
            "qrcode": ["w1_layer/action.py", "m_layer/plugin_market.py"],
            "image_process": ["w1_layer/action.py", "m_layer/plugin_market.py"],
            "web_search": ["w1_layer/action.py", "m_layer/cognition.py"],
        }
        files.extend(intent_map.get(intent, []))

        # 去重
        return list(set(files))

    def _locate_files_by_pattern(self, pattern_key: str) -> List[str]:
        """根据 pattern_key 定位相关文件"""
        if "tool" in pattern_key or "chat" in pattern_key:
            return ["w1_layer/action.py", "m_layer/cognition.py"]
        if "memory" in pattern_key:
            return ["w1_layer/memory.py"]
        if "perception" in pattern_key:
            return ["w2_layer/perception.py"]
        return ["m_layer/cognition.py"]


class ProposalGenerator:
    """方案生成器 — 基于缺陷调用 LLM 生成代码补丁

    流程：
      1. 读取缺陷相关源码
      2. 构建提示词（缺陷描述 + 源码上下文）
      3. 调用 LLM 生成代码补丁
      4. 解析 LLM 输出，提取 target_file / new_code / description
    """

    # LLM 提示词模板
    PROMPT_TEMPLATE = """你是一个代码自修改助手。请基于以下缺陷分析，生成代码修改方案。

## 缺陷描述
{defect_description}

## 缺陷证据
{defect_evidence}

## 相关源码文件
{related_files}

## 当前源码内容
{source_code}

## 任务
请分析缺陷根因，生成具体的代码修改方案。输出格式必须严格如下：

```json
{{
  "target_file": "需要修改的文件相对路径",
  "description": "修改描述（一句话）",
  "reasoning": "修改理由（分析根因+解决方案）",
  "mode": "append",
  "new_code": "新的代码内容（Python代码，完整可执行）"
}}
```

约束：
1. 只能修改 .py 文件
2. 不可使用 eval/exec/compile/__import__ 等危险函数
3. 不可导入 subprocess/ctypes 等危险模块
4. 不可修改 d_layer/ 下的文件
5. new_code 必须是语法正确的 Python 代码
6. mode 优先用 append（追加），避免全量替换破坏现有代码
"""

    def __init__(self):
        pass

    def generate(self, defect: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """基于缺陷生成代码修改方案

        Args:
            defect: 缺陷报告

        Returns:
            {target_file, description, reasoning, mode, new_code} 或 None
        """
        try:
            # 1. 读取相关源码
            related_files = defect.get("suggested_files", [])
            source_code = self._read_related_sources(related_files)

            # 2. 构建提示词
            prompt = self.PROMPT_TEMPLATE.format(
                defect_description=defect.get("description", ""),
                defect_evidence=json.dumps(defect.get("evidence", {}), ensure_ascii=False, indent=2),
                related_files="\n".join(related_files),
                source_code=source_code[:4000],  # 截断防止过长
            )

            # 3. 调用 LLM
            from m_layer.llm_client import get_client
            client = get_client()
            result = client.chat(
                prompt=prompt,
                system_prompt="analytical",
                context="",
            )
            llm_output = result.get("content", "")

            # 4. 解析 LLM 输出
            proposal = self._parse_llm_output(llm_output)
            if proposal is None:
                logger.warning(f"LLM 输出解析失败: {llm_output[:200]}")
                return None

            # 5. 补全字段
            proposal["proposer"] = "self_evolution"
            proposal["source_defect_id"] = defect.get("defect_id", "")
            logger.info(f"方案生成成功: {proposal.get('target_file')} ({proposal.get('description', '')[:50]})")
            return proposal

        except Exception as e:
            logger.error(f"方案生成异常: {e}", exc_info=True)
            return None

    def _read_related_sources(self, files: List[str]) -> str:
        """读取相关源码文件内容"""
        contents = []
        for rel_path in files[:3]:  # 最多读3个文件
            full_path = os.path.join(BASE_DIR, rel_path)
            if os.path.exists(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    # 截断每个文件最多1500字符
                    contents.append(f"### {rel_path}\n```python\n{content[:1500]}\n```")
                except Exception as e:
                    logger.debug(f"读取 {rel_path} 失败: {e}")
        return "\n\n".join(contents) if contents else "（无相关源码）"

    def _parse_llm_output(self, output: str) -> Optional[Dict]:
        """解析 LLM 输出的 JSON 方案（支持被截断的 JSON）"""
        # 1. 尝试标准 ```json ... ``` 代码块
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", output, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 2. 尝试直接解析整个输出为 JSON
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            pass

        # 3. 提取第一个 { 到最后一个 } 之间的内容
        first_brace = output.find("{")
        last_brace = output.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            candidate = output[first_brace:last_brace + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # 4. 处理被截断的 JSON（LLM 输出长度限制）
        # 尝试逐步修复 JSON：找到第一个 {，然后尝试解析到不同的 } 位置
        if first_brace >= 0:
            # 从后往前找最后一个完整的键值对结尾
            truncated = output[first_brace:]
            # 尝试在截断处添加 } 闭合
            for close_pos in range(len(truncated) - 1, 0, -1):
                if truncated[close_pos] == "}":
                    candidate = truncated[:close_pos + 1]
                    try:
                        result = json.loads(candidate)
                        # 检查必要字段
                        if "target_file" in result and "new_code" in result:
                            logger.info(f"解析截断JSON成功（位置{close_pos}）")
                            return result
                    except json.JSONDecodeError:
                        continue

            # 5. 暴力修复：从第一个 { 开始，补全引号和括号
            truncated = output[first_brace:]
            # 找最后一个完整的字符串值
            # 策略：找最后一个 `"new_code": "` 后的内容到截断处
            new_code_match = re.search(r'"new_code"\s*:\s*"((?:[^"\\]|\\.)*)"?', truncated, re.DOTALL)
            target_match = re.search(r'"target_file"\s*:\s*"([^"]+)"', truncated)
            desc_match = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', truncated)
            reasoning_match = re.search(r'"reasoning"\s*:\s*"((?:[^"\\]|\\.)*)"', truncated, re.DOTALL)
            mode_match = re.search(r'"mode"\s*:\s*"(\w+)"', truncated)

            if target_match and new_code_match:
                # 重新构建完整 JSON
                reconstructed = {
                    "target_file": target_match.group(1),
                    "description": desc_match.group(1) if desc_match else "",
                    "reasoning": reasoning_match.group(1) if reasoning_match else "",
                    "mode": mode_match.group(1) if mode_match else "append",
                    "new_code": new_code_match.group(1),
                }
                logger.info(f"暴力修复JSON成功: {reconstructed['target_file']}")
                return reconstructed

        return None


class SelfEvolutionEngine:
    """自进化引擎 — 主动发现不足 + 生成方案 + 提交审核

    用法：
        engine = get_evolution_engine()
        # 手动触发扫描
        report = engine.run_scan()
        # 或等待后台自动触发
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.analyzer = DefectAnalyzer()
        self.generator = ProposalGenerator()
        self._last_scan_time: Optional[datetime] = None
        self._scan_history: List[Dict] = []
        self._triggered_defects: set = set()  # 已触发过的缺陷ID，避免重复
        self._load_state()

        # 启动后台触发线程
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._background_loop, daemon=True)
        self._thread.start()

    # ─── 状态持久化 ────────────────────────────────────

    def _load_state(self):
        try:
            if os.path.exists(EVOLUTION_STATE_PATH):
                with open(EVOLUTION_STATE_PATH, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._last_scan_time = self._parse_time(state.get("last_scan_time"))
                self._scan_history = state.get("scan_history", [])[-50:]
                self._triggered_defects = set(state.get("triggered_defects", []))
                logger.info(f"自进化状态加载: 历史{len(self._scan_history)}条, 已触发{len(self._triggered_defects)}个缺陷")
        except Exception as e:
            logger.warning(f"加载自进化状态失败: {e}")

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(EVOLUTION_STATE_PATH), exist_ok=True)
            state = {
                "last_scan_time": self._last_scan_time.isoformat() if self._last_scan_time else None,
                "scan_history": self._scan_history[-50:],
                "triggered_defects": list(self._triggered_defects),
                "updated_at": datetime.now().isoformat(),
            }
            with open(EVOLUTION_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存自进化状态失败: {e}")

    def _parse_time(self, time_str: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(time_str) if time_str else None
        except Exception:
            return None

    # ─── 核心扫描流程 ────────────────────────────────────

    def run_scan(self, force: bool = False) -> Dict[str, Any]:
        """执行一次完整的自进化扫描

        流程：扫描缺陷 → 生成方案 → 提交审核

        Args:
            force: 强制扫描（忽略去重）

        Returns:
            {scan_id, defects_found, proposals_generated, proposals_submitted, details}
        """
        scan_id = f"scan_{int(time.time())}"
        scan_start = datetime.now()

        with self._lock:
            self._last_scan_time = scan_start

        logger.info(f"=== 自进化扫描开始 {scan_id} ===")

        # ① 扫描缺陷
        defects = self.analyzer.scan()
        logger.info(f"扫描发现 {len(defects)} 个缺陷")

        # 过滤已触发的缺陷（避免重复处理）
        new_defects = []
        for d in defects:
            if force or d["defect_id"] not in self._triggered_defects:
                new_defects.append(d)
                self._triggered_defects.add(d["defect_id"])

        if not new_defects:
            logger.info("无新缺陷需要处理")
            self._record_scan(scan_id, scan_start, defects, [], [])
            return {"scan_id": scan_id, "defects_found": 0, "proposals_generated": 0,
                    "proposals_submitted": 0, "message": "无新缺陷"}

        # ② 生成方案
        proposals_generated = []
        for defect in new_defects:
            proposal = self.generator.generate(defect)
            if proposal:
                proposal["defect"] = defect
                proposals_generated.append(proposal)

        logger.info(f"生成 {len(proposals_generated)} 个修改方案")

        # ③ 提交审核
        proposals_submitted = []
        for proposal in proposals_generated:
            submit_result = self._submit_proposal(proposal)
            proposals_submitted.append(submit_result)

        # ④ 记录扫描结果
        self._record_scan(scan_id, scan_start, new_defects, proposals_generated, proposals_submitted)

        logger.info(f"=== 自进化扫描完成: 缺陷{len(new_defects)} 方案{len(proposals_generated)} 提交{len(proposals_submitted)} ===")

        return {
            "scan_id": scan_id,
            "defects_found": len(new_defects),
            "proposals_generated": len(proposals_generated),
            "proposals_submitted": len(proposals_submitted),
            "defects": [{"defect_id": d["defect_id"], "description": d["description"],
                         "severity": d["severity"]} for d in new_defects],
            "proposals": [{"target_file": p.get("target_file"),
                           "description": p.get("description"),
                           "submit_result": r} for p, r in zip(proposals_generated, proposals_submitted)],
        }

    def _submit_proposal(self, proposal: Dict) -> Dict[str, Any]:
        """提交方案到 code_modifier 走审查流程"""
        try:
            from m_layer.code_self_modify import get_modifier
            modifier = get_modifier()

            result = modifier.propose_modification(
                target_file=proposal.get("target_file", ""),
                description=proposal.get("description", ""),
                new_code=proposal.get("new_code", ""),
                proposer="self_evolution",
                reasoning=proposal.get("reasoning", ""),
                mode=proposal.get("mode", "append"),
            )

            status = result.get("status", "unknown")
            logger.info(f"方案提交: {result.get('id', 'N/A')} 状态={status}")

            # 如果被拒绝，记录原因
            if status == "rejected":
                logger.warning(f"方案被拒绝: {result.get('reject_reason', '')}")
                # 从已触发集合中移除，允许下次重试
                defect_id = proposal.get("defect", {}).get("defect_id", "")
                if defect_id:
                    self._triggered_defects.discard(defect_id)

            return {
                "success": status in ("pending_approval", "endorsed", "approved", "applied"),
                "modification_id": result.get("id", ""),
                "status": status,
                "reject_reason": result.get("reject_reason", ""),
                "target_file": proposal.get("target_file", ""),
            }
        except Exception as e:
            logger.error(f"方案提交异常: {e}")
            return {"success": False, "error": str(e)}

    def _record_scan(self, scan_id: str, start: datetime,
                     defects: List, proposals: List, submissions: List):
        """记录扫描历史"""
        record = {
            "scan_id": scan_id,
            "started_at": start.isoformat(),
            "completed_at": datetime.now().isoformat(),
            "defects_found": len(defects),
            "proposals_generated": len(proposals),
            "proposals_submitted": len(submissions),
            "defect_ids": [d.get("defect_id", "") for d in defects],
        }
        with self._lock:
            self._scan_history.append(record)
        self._save_state()

    # ─── 后台自动触发 ────────────────────────────────────

    def _background_loop(self):
        """后台线程：定期检查触发条件"""
        while not self._stop_event.is_set():
            try:
                time.sleep(TRIGGER_CHECK_INTERVAL_SEC)
                self._check_triggers()
            except Exception as e:
                logger.debug(f"后台扫描异常: {e}")

    def _check_triggers(self):
        """检查所有触发条件"""
        # 1. 周期触发
        if self._last_scan_time:
            elapsed = datetime.now() - self._last_scan_time
            if elapsed.total_seconds() >= TRIGGER_PERIODIC_HOURS * 3600:
                logger.info(f"周期触发（距上次扫描{elapsed}）")
                self.run_scan()
                return

        # 2. 阈值触发：检查失败经验和负面反馈是否达到阈值
        defects = self.analyzer.scan()
        new_defects = [d for d in defects if d["defect_id"] not in self._triggered_defects]
        if new_defects:
            logger.info(f"阈值触发：发现 {len(new_defects)} 个新缺陷")
            self.run_scan()

    def trigger_now(self) -> Dict[str, Any]:
        """手动触发扫描（API调用）"""
        return self.run_scan(force=True)

    # ─── 查询 ────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """自进化引擎状态"""
        with self._lock:
            return {
                "last_scan_time": self._last_scan_time.isoformat() if self._last_scan_time else None,
                "total_scans": len(self._scan_history),
                "triggered_defects": len(self._triggered_defects),
                "trigger_thresholds": {
                    "fail_count": TRIGGER_FAIL_COUNT,
                    "negative_feedback": TRIGGER_NEGATIVE_FEEDBACK,
                    "periodic_hours": TRIGGER_PERIODIC_HOURS,
                },
                "recent_scans": self._scan_history[-5:],
                "background_thread_alive": self._thread.is_alive(),
            }

    def list_scan_history(self, limit: int = 20) -> List[Dict]:
        """扫描历史"""
        with self._lock:
            return list(self._scan_history[-limit:])

    def stop(self):
        """停止后台线程"""
        self._stop_event.set()


# ─── 全局单例 ────────────────────────────────────

_evolution_instance: Optional[SelfEvolutionEngine] = None
_evolution_lock = threading.Lock()


def get_evolution_engine() -> SelfEvolutionEngine:
    """获取自进化引擎全局单例"""
    global _evolution_instance
    if _evolution_instance is None:
        with _evolution_lock:
            if _evolution_instance is None:
                _evolution_instance = SelfEvolutionEngine()
    return _evolution_instance
