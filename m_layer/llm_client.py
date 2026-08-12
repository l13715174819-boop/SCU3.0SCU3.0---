# -*- coding: utf-8 -*-
"""
m_layer/llm_client.py — 多平台 LLM 客户端（M层）
====================================================
阶段1增强：本地大模型接入 + 多平台切换

支持平台：
  云端：DeepSeek / 通义千问 / Kimi / 智谱GLM / 文心一言
  本地：LM Studio / Ollama（OpenAI兼容接口）

特性：
  - 自动检测可用平台（按优先级）
  - 无任何API Key时降级为规则回复
  - 支持流式和非流式调用
  - 平台间切换无需重启
  - 本地模型健康检查
  - 调用统计（token/延迟/成功率）

架构归属：M层（认知层调用LLM生成回复）
依赖方向：M层→D层（只读axioms），W1层（账本计税）
"""
import os
import time
import logging
import threading
import urllib.request
import json as _json
from typing import Optional, List, Dict, Any, Generator

logger = logging.getLogger("SCU3.m.llm")


class LLMClient:
    """多平台 LLM 客户端

    用法:
        client = LLMClient()
        # 自动选择可用平台
        reply = client.chat("你好")
        # 指定平台
        reply = client.chat_multiplatform("你好", platform="lmstudio")
        # 列出可用平台
        platforms = client.list_available_platforms()
    """

    # ─── 预设模型 ────────────────────────────────
    MODEL_CHAT = "deepseek-chat"
    MODEL_REASONER = "deepseek-reasoner"

    # ─── 系统提示词 ────────────────────────────────
    SYSTEM_PROMPTS = {
        "default": ("你是SCU3智能助手，具备联网搜索、网页爬取、工具调用等能力。"
                    "自然、友好地回答用户问题，像朋友一样交流。"
                    "当用户询问实时信息（新闻、天气、价格、最新进展等）时，系统会自动联网搜索并将结果提供给你，"
                    "请基于提供的搜索结果回答；若未提供搜索结果，说明未获取到实时信息，但不要声称自己「不能联网」。"
                    "技术问题给出专业解答，闲聊时轻松自然。"),
        "analytical": "你是一个严谨的分析师，善于逻辑推理和结构化思考。给出条理清晰、论据充分的分析。",
        "creative": "你是一个富有创意的写作者，回答生动有趣、富有想象力。",
        "coding": "你是一个资深程序员，提供高质量代码和清晰注释。先理解需求，再给出简洁可靠的实现。",
        "web_search": ("你是一个联网搜索助手。系统已将联网搜索到的结果作为上下文提供给你，"
                       "请基于这些搜索结果回答用户问题，回答要准确、简洁、有条理，并在末尾附上信息来源链接。"
                       "若上下文中没有搜索结果或结果不足以回答，请如实说明「此次未获取到相关搜索结果」，"
                       "不要声称自己「不能联网」——联网能力由系统提供，你只需基于结果作答。"),
        "knowledge": ("你是 SCU3 系统的知识助手。系统已从本地知识库检索相关文档作为上下文提供给你，"
                      "请基于这些上下文回答用户关于 SCU3 架构、CUF 安全内核、三级记忆、工具链等本地主题的问题。"
                      "回答要准确、有条理，引用上下文中的具体内容。"
                      "若上下文不足以回答，请如实说明，不要编造。"),
        "followup": ("你是SCU3智能助手。用户正在进行多轮对话追问或修正前轮回答。"
                     "请务必参考对话历史中的上下文，理解用户指代词（刚才/那个/这个方案等）的具体指向，"
                     "基于前轮回答内容进行深入、修正或转换视角，不要当作独立问题处理。"
                     "如果对话历史中确实没有相关上下文，请礼貌说明并请用户重新描述。"),
    }

    # ─── 多平台配置表（按优先级排列）────────────────────
    PLATFORM_CONFIGS = {
        "deepseek": {
            "env_key": "DEEPSEEK_API_KEY",
            "env_url": "DEEPSEEK_BASE_URL",
            "default_url": "https://api.deepseek.com/v1",
            "default_model": "deepseek-chat",
            "label": "DeepSeek",
            "local": False,
        },
        "qwen": {
            "env_key": "DASHSCOPE_API_KEY",  # 百炼官方变量名（兼容 QWEN_API_KEY）
            "env_key_alt": "QWEN_API_KEY",   # 备选变量名
            "env_url": "DASHSCOPE_BASE_URL",
            "env_url_alt": "QWEN_BASE_URL",
            "default_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "default_model": "qwen-plus",
            "label": "通义千问（百炼）",
            "local": False,
        },
        "moonshot": {
            "env_key": "MOONSHOT_API_KEY",
            "env_url": "MOONSHOT_BASE_URL",
            "default_url": "https://api.moonshot.cn/v1",
            "default_model": "moonshot-v1-8k",
            "label": "Kimi",
            "local": False,
        },
        "zhipu": {
            "env_key": "ZHIPU_API_KEY",
            "env_url": "ZHIPU_BASE_URL",
            "default_url": "https://open.bigmodel.cn/api/paas/v4",
            "default_model": "glm-4",
            "label": "智谱GLM",
            "local": False,
        },
        "lmstudio": {
            "env_key": "LMSTUDIO_ENABLED",  # 本地服务用开关判断
            "env_url": "LMSTUDIO_BASE_URL",
            "default_url": "http://localhost:1234/v1",
            "default_model": "",  # 自动获取已加载模型
            "label": "LM Studio（本地）",
            "local": True,
        },
        "ollama": {
            "env_key": "OLLAMA_ENABLED",
            "env_url": "OLLAMA_BASE_URL",
            "default_url": "http://localhost:11434/v1",
            "default_model": "llama3",
            "label": "Ollama（本地）",
            "local": True,
        },
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com/v1",
        default_model: str = "deepseek-chat",
        timeout: float = 60.0,
    ):
        # 加载.env（如果存在）
        self._load_env()

        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        self.default_model = default_model or os.getenv("DEEPSEEK_DEFAULT_MODEL", "deepseek-chat")
        self.timeout = timeout

        # 运行模式：有key用deepseek，无key降级规则
        self.mode = "deepseek" if self.api_key else "rule_based"
        self._client = None
        self._lock = threading.Lock()

        # 当前激活平台
        self.active_platform = "deepseek" if self.mode == "deepseek" else "rule_based"

        # 调用统计
        self.call_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.total_tokens = 0
        self.total_latency = 0.0

        # 初始化客户端
        self._init_client()

    def _load_env(self):
        """加载.env文件"""
        env_paths = [
            os.path.join(os.getcwd(), ".env"),
            os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
            os.path.join(os.path.dirname(__file__), "..", ".env"),
        ]
        for env_path in env_paths:
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip().strip('"\''))
                break

    def _init_client(self):
        """初始化OpenAI兼容客户端"""
        if self.mode == "deepseek":
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self.timeout,
                )
                logger.info(f"DeepSeek已连接 (model={self.default_model})")
            except ImportError:
                logger.warning("openai包未安装 → 降级为规则模式")
                self.mode = "rule_based"
            except Exception as e:
                logger.warning(f"DeepSeek初始化失败: {e} → 降级为规则模式")
                self.mode = "rule_based"
        else:
            # 阶段1增强：无DeepSeek Key时自动检测本地模型
            local_platform = self._detect_local_platform()
            if local_platform:
                self.mode = "local"
                self.active_platform = local_platform["id"]
                self._client = local_platform["client"]
                self.default_model = local_platform["model"]
                logger.info(f"本地模型已连接: {local_platform['label']} (model={local_platform['model']})")
            else:
                logger.info("无DEEPSEEK_API_KEY且无本地模型 → 规则模式")

    # ─── 阶段1：本地模型检测 ────────────────────────────────

    def _detect_local_platform(self) -> Optional[Dict[str, Any]]:
        """自动检测可用的本地LLM服务

        检测顺序：本地Transformers模型（Qwen/GLM） → LM Studio → Ollama

        Returns:
            {id, label, client, model, base_url} 或 None
        """
        # 优先检测：本地Transformers模型（Qwen-7B/GLM-4-9B）
        local_torch = self._check_local_torch_model()
        if local_torch:
            return local_torch

        # 检测 LM Studio
        lmstudio = self._check_lmstudio()
        if lmstudio:
            return lmstudio

        # 检测 Ollama
        ollama = self._check_ollama()
        if ollama:
            return ollama

        return None

    def _check_local_torch_model(self) -> Optional[Dict[str, Any]]:
        """检测本地Transformers模型（Qwen-7B/GLM-4-9B直接加载）

        当配置了 LOCAL_MODEL_NAME 环境变量时，使用transformers直接加载
        """
        model_name = os.getenv("LOCAL_MODEL_NAME", "")
        if not model_name:
            return None

        try:
            from m_layer.local_model import get_local_model
            client = get_local_model()

            # 检查是否已加载
            if not client._model_loaded:
                # 尝试加载
                quantization = os.getenv("LOCAL_MODEL_QUANT", "auto")
                device = os.getenv("LOCAL_MODEL_DEVICE", "auto")
                result = client.load_model(model_name, quantization=quantization, device=device)
                if not result.get("success"):
                    logger.info(f"本地模型 {model_name} 加载失败: {result.get('error', '')}")
                    return None

            logger.info(f"本地Transformers模型已加载: {model_name}")
            return {
                "id": "local_torch",
                "label": f"本地模型（{model_name}）",
                "client": client,
                "model": model_name,
                "base_url": "local://transformers",
            }
        except ImportError:
            logger.info("local_model模块不可用，跳过Transformers本地模型")
            return None
        except Exception as e:
            logger.warning(f"本地Transformers模型检测失败: {e}")
            return None

    def _check_lmstudio(self) -> Optional[Dict[str, Any]]:
        """检测LM Studio服务"""
        enabled = os.getenv("LMSTUDIO_ENABLED", "").lower() in ("1", "true", "yes", "on")
        base_url = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")

        # 即使未显式启用，也尝试探测（便于自动发现）
        try:
            with urllib.request.urlopen(f"{base_url}/models", timeout=2) as r:
                data = _json.loads(r.read().decode("utf-8"))
                models = data.get("data", [])
            if not models:
                return None
            loaded_model = models[0].get("id", "")
            # 获取显示名（去扩展名）
            display_model = loaded_model.replace("\\", "/").split("/")[-1]
            for ext in (".gguf", ".bin", ".safetensors"):
                if display_model.lower().endswith(ext):
                    display_model = display_model[:-len(ext)]

            try:
                from openai import OpenAI
                client = OpenAI(api_key="lm-studio", base_url=base_url, timeout=self.timeout)
            except ImportError:
                logger.warning("openai包未安装，无法接入LM Studio")
                return None

            return {
                "id": "lmstudio",
                "label": "LM Studio（本地）",
                "client": client,
                "model": display_model or loaded_model,
                "base_url": base_url,
            }
        except Exception:
            # LM Studio 未运行
            if enabled:
                logger.info("LM Studio已启用但未检测到服务")
            return None

    def _check_ollama(self) -> Optional[Dict[str, Any]]:
        """检测Ollama服务"""
        enabled = os.getenv("OLLAMA_ENABLED", "").lower() in ("1", "true", "yes", "on")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        default_model = os.getenv("OLLAMA_MODEL", "llama3")

        try:
            with urllib.request.urlopen(f"{base_url}/models", timeout=2) as r:
                data = _json.loads(r.read().decode("utf-8"))
                models = data.get("data", [])
            if not models:
                # 服务在但无模型，用默认模型名
                use_model = default_model
            else:
                use_model = models[0].get("id", default_model)

            try:
                from openai import OpenAI
                client = OpenAI(api_key="ollama", base_url=base_url, timeout=self.timeout)
            except ImportError:
                logger.warning("openai包未安装，无法接入Ollama")
                return None

            return {
                "id": "ollama",
                "label": "Ollama（本地）",
                "client": client,
                "model": use_model,
                "base_url": base_url,
            }
        except Exception:
            if enabled:
                logger.info("Ollama已启用但未检测到服务")
            return None

    # ─── 多平台管理 ────────────────────────────────

    def list_available_platforms(self) -> List[Dict[str, Any]]:
        """列出所有可用平台（含云端已配置Key + 本地已运行）"""
        available = []

        # 云端平台
        for pid, cfg in self.PLATFORM_CONFIGS.items():
            if cfg["local"]:
                continue  # 本地平台单独处理
            # 读取 API Key（支持主备变量名）
            key = os.getenv(cfg["env_key"], "")
            if not key and cfg.get("env_key_alt"):
                key = os.getenv(cfg["env_key_alt"], "")
            if key:
                # 读取 base_url（支持主备变量名）
                base_url = os.getenv(cfg["env_url"], "")
                if not base_url and cfg.get("env_url_alt"):
                    base_url = os.getenv(cfg["env_url_alt"], cfg["default_url"])
                available.append({
                    "id": pid,
                    "label": cfg["label"],
                    "model": os.getenv(pid.upper() + "_MODEL", cfg["default_model"]),
                    "base_url": base_url or cfg["default_url"],
                    "local": False,
                    "active": pid == self.active_platform,
                })

        # 本地平台（实时探测）
        for pid in ("lmstudio", "ollama"):
            cfg = self.PLATFORM_CONFIGS[pid]
            base_url = os.getenv(cfg["env_url"], cfg["default_url"])
            try:
                with urllib.request.urlopen(f"{base_url}/models", timeout=2) as r:
                    data = _json.loads(r.read().decode("utf-8"))
                    models = data.get("data", [])
                if models:
                    available.append({
                        "id": pid,
                        "label": cfg["label"],
                        "model": models[0].get("id", cfg["default_model"]),
                        "base_url": base_url,
                        "local": True,
                        "loaded_models": [m.get("id", "") for m in models],
                        "active": pid == self.active_platform,
                    })
            except Exception:
                pass

        return available

    def switch_platform(self, platform: str, model: str = "") -> Dict[str, Any]:
        """切换激活平台

        Args:
            platform: 平台ID (deepseek/qwen/lmstudio/ollama/local_torch)
            model: 模型名（可选，用平台默认）

        Returns:
            {success, platform, model, error}
        """
        # 特殊处理：本地Transformers模型（local_torch）
        if platform == "local_torch":
            try:
                from m_layer.local_model import get_local_model
                local_client = get_local_model()
                if not local_client._model_loaded:
                    return {"success": False, "error": "本地模型未加载，请先调用 /local-model/load"}
                with self._lock:
                    self._client = local_client
                    self.active_platform = "local_torch"
                    self.default_model = local_client._model_name or "local_torch"
                    self.mode = "local"
                logger.info(f"平台已切换: 本地Transformers模型 (model={self.default_model})")
                return {
                    "success": True,
                    "platform": "local_torch",
                    "label": f"本地模型（{self.default_model}）",
                    "model": self.default_model,
                    "local": True,
                    "error": None,
                }
            except Exception as e:
                return {"success": False, "platform": platform, "error": str(e)}

        cfg = self.PLATFORM_CONFIGS.get(platform)
        if not cfg:
            return {"success": False, "error": f"未知平台: {platform}"}

        try:
            if cfg["local"]:
                # 本地平台
                base_url = os.getenv(cfg["env_url"], cfg["default_url"])
                # 探测服务
                with urllib.request.urlopen(f"{base_url}/models", timeout=2) as r:
                    data = _json.loads(r.read().decode("utf-8"))
                    models = data.get("data", [])
                if not models and not model:
                    return {"success": False, "error": f"{cfg['label']}无已加载模型"}
                use_model = model or (models[0].get("id", "") if models else cfg["default_model"])
                api_key = "local"  # 本地服务不校验
            else:
                # 云端平台：读取 API Key（支持主备变量名）
                api_key = os.getenv(cfg["env_key"], "")
                if not api_key and cfg.get("env_key_alt"):
                    api_key = os.getenv(cfg["env_key_alt"], "")
                if not api_key:
                    return {"success": False, "error": f"{cfg['label']} API Key未配置"}
                # 读取 base_url（支持主备变量名）
                base_url = os.getenv(cfg["env_url"], "")
                if not base_url and cfg.get("env_url_alt"):
                    base_url = os.getenv(cfg["env_url_alt"], cfg["default_url"])
                base_url = base_url or cfg["default_url"]
                use_model = model or os.getenv(platform.upper() + "_MODEL", cfg["default_model"])

            from openai import OpenAI
            new_client = OpenAI(api_key=api_key, base_url=base_url, timeout=self.timeout)

            # 切换
            with self._lock:
                self._client = new_client
                self.active_platform = platform
                self.default_model = use_model
                self.mode = "local" if cfg["local"] else "deepseek"

            logger.info(f"平台已切换: {cfg['label']} (model={use_model})")
            return {
                "success": True,
                "platform": platform,
                "label": cfg["label"],
                "model": use_model,
                "local": cfg["local"],
                "error": None,
            }
        except Exception as e:
            return {"success": False, "platform": platform, "error": str(e)}

    def get_active_platform(self) -> Dict[str, Any]:
        """获取当前激活平台信息"""
        cfg = self.PLATFORM_CONFIGS.get(self.active_platform, {})
        return {
            "id": self.active_platform,
            "label": cfg.get("label", "规则模式"),
            "model": self.default_model,
            "mode": self.mode,
            "local": cfg.get("local", False),
        }

    # ─── 核心调用方法 ────────────────────────────────

    def chat(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        context: str = "",
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """发送对话请求（自动使用当前激活平台）

        Args:
            prompt: 用户输入
            model: 模型名（为空用默认）
            system_prompt: 系统提示词预设名
            temperature: 温度
            max_tokens: 最大token
            context: RAG上下文（可选）
            history: 历史消息列表（多轮对话，可选）

        Returns:
            {content, model, tokens, latency, mode, platform, error}
        """
        with self._lock:
            self.call_count += 1

        # 本地Transformers模型走特殊调用路径
        if self.active_platform == "local_torch" and self._client:
            return self._call_local_torch(prompt, system_prompt,
                                          temperature, max_tokens, context, history)

        if self.mode in ("deepseek", "local") and self._client:
            return self._call_llm(prompt, model, system_prompt,
                                  temperature, max_tokens, context, history)
        else:
            return self._rule_based_reply(prompt, system_prompt, context)

    def chat_stream(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_prompt: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        context: str = "",
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Generator[str, None, None]:
        """流式调用，逐块yield文本（支持多轮对话历史）"""
        # 本地Transformers模型流式
        if self.active_platform == "local_torch" and self._client:
            sys_msg = self.SYSTEM_PROMPTS.get(system_prompt, system_prompt)
            full_prompt = self._build_prompt_with_history(prompt, context, history)
            try:
                for chunk in self._client.chat_stream(
                    full_prompt, system_prompt=sys_msg,
                    temperature=temperature, max_tokens=max_tokens
                ):
                    yield chunk
            except Exception as e:
                logger.error(f"本地模型流式错误: {e}")
                yield f"[LLM错误: {e}]"
            return

        if self.mode not in ("deepseek", "local") or not self._client:
            reply = self._rule_based_reply(prompt, system_prompt, context)["content"]
            for ch in reply:
                yield ch
            return

        model = model or self.default_model
        sys_msg = self.SYSTEM_PROMPTS.get(system_prompt, system_prompt)
        full_prompt = f"{context}\n\n用户问题：{prompt}" if context else prompt

        # 构建messages：system + history + 当前user
        messages = [{"role": "system", "content": sys_msg}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": full_prompt})

        try:
            stream = self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except Exception as e:
            logger.error(f"流式错误({self.active_platform}): {e}")
            yield f"[LLM错误: {e}]"

    def _build_prompt_with_history(self, prompt: str, context: str, history: Optional[List[Dict[str, str]]]) -> str:
        """将历史对话拼接为文本（供本地Transformers模型使用）"""
        parts = []
        if history:
            for h in history[-10:]:
                if h.get("role") == "user" and h.get("content"):
                    parts.append(f"用户：{h['content']}")
                elif h.get("role") == "assistant" and h.get("content"):
                    parts.append(f"助手：{h['content']}")
        if context:
            parts.append(context)
        parts.append(f"用户问题：{prompt}")
        return "\n\n".join(parts)

    def _call_llm(
        self, prompt, model, system_prompt, temperature, max_tokens, context,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """真实调用LLM API（支持多平台 + 多轮对话）"""
        model = model or self.default_model
        sys_msg = self.SYSTEM_PROMPTS.get(system_prompt, system_prompt)
        full_prompt = f"{context}\n\n用户问题：{prompt}" if context else prompt

        # 构建messages：system + history + 当前user
        messages = [{"role": "system", "content": sys_msg}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": full_prompt})

        start = time.time()
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency = time.time() - start
            content = resp.choices[0].message.content or ""
            usage = resp.usage
            tokens = ((usage.prompt_tokens or 0) + (usage.completion_tokens or 0)) if usage else 0

            with self._lock:
                self.success_count += 1
                self.total_tokens += tokens
                self.total_latency += latency

            return {
                "content": content,
                "model": model,
                "tokens": tokens,
                "latency": round(latency, 3),
                "mode": self.mode,
                "platform": self.active_platform,
                "error": None,
            }
        except Exception as e:
            with self._lock:
                self.fail_count += 1
            latency = time.time() - start
            logger.error(f"LLM调用失败({self.active_platform}): {e}")
            # 降级为规则回复
            return self._rule_based_reply(prompt, system_prompt, context, error=str(e))

    def _call_local_torch(
        self, prompt, system_prompt, temperature, max_tokens, context,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """调用本地Transformers模型（Qwen/GLM，支持多轮对话）"""
        sys_msg = self.SYSTEM_PROMPTS.get(system_prompt, system_prompt)
        full_prompt = self._build_prompt_with_history(prompt, context, history)

        start = time.time()
        try:
            result = self._client.chat(
                full_prompt,
                system_prompt=sys_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency = time.time() - start
            content = result.get("content", "")
            tokens = result.get("tokens", 0)

            with self._lock:
                self.success_count += 1
                self.total_tokens += tokens
                self.total_latency += latency

            return {
                "content": content,
                "model": self.default_model,
                "model_type": result.get("model_type", "text"),
                "tokens": tokens,
                "latency": round(latency, 3),
                "mode": "local",
                "platform": "local_torch",
                "error": None,
            }
        except Exception as e:
            with self._lock:
                self.fail_count += 1
            latency = time.time() - start
            logger.error(f"本地模型调用失败: {e}")
            return self._rule_based_reply(prompt, system_prompt, context, error=str(e))

    def chat_with_image(
        self,
        prompt: str,
        image,
        system_prompt: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 1024,
        auto_switch: bool = True,
    ) -> Dict[str, Any]:
        """视觉对话：调用本地 VL 模型对图像+提示词进行推理

        按方案 A：若当前加载的是文本模型，会自动切换到 VL 模型（不同时加载）。
        若未启用 auto_switch 且当前不是 VL 模型，则返回错误。

        Args:
            prompt: 文本提示词
            image: 图像输入（路径/URL/base64/bytes/dict）
            system_prompt: 系统提示词预设名
            temperature: 采样温度
            max_tokens: 最大生成 token 数
            auto_switch: 是否自动从 text 模型切换到 vl 模型

        Returns:
            {content, model, model_type, tokens, latency, mode, platform, switched, error}
        """
        with self._lock:
            self.call_count += 1

        sys_msg = self.SYSTEM_PROMPTS.get(system_prompt, system_prompt)

        # 当前未激活 local_torch，尝试切换
        if self.active_platform != "local_torch":
            switch_result = self.switch_platform("local_torch")
            if not switch_result.get("success"):
                return {
                    "content": f"[切换到本地模型失败: {switch_result.get('error')}]",
                    "model": "none",
                    "model_type": "vl",
                    "tokens": 0,
                    "latency": 0.0,
                    "mode": "local",
                    "platform": self.active_platform,
                    "switched": False,
                    "error": switch_result.get("error"),
                }

        # 确保加载的是 VL 模型
        switched = False
        try:
            local_client = self._client
            if local_client is None or not getattr(local_client, "_model_loaded", False):
                return {
                    "content": "[本地模型未加载，请先调用 /local-model/load 加载 VL 模型]",
                    "model": "none",
                    "model_type": "vl",
                    "tokens": 0,
                    "latency": 0.0,
                    "mode": "local",
                    "platform": "local_torch",
                    "switched": False,
                    "error": "model_not_loaded",
                }

            current_type = getattr(local_client, "_model_type", "text")
            if current_type != "vl":
                if not auto_switch:
                    return {
                        "content": "[当前为文本模型，未启用 auto_switch，请手动切换到 VL 模型]",
                        "model": local_client._model_name,
                        "model_type": current_type,
                        "tokens": 0,
                        "latency": 0.0,
                        "mode": "local",
                        "platform": "local_torch",
                        "switched": False,
                        "error": "model_not_vl",
                    }
                # 自动切换到 VL 模型
                logger.info("当前为文本模型，自动切换到 VL 模型")
                switch_result = local_client.switch_model_type("vl")
                if not switch_result.get("success"):
                    return {
                        "content": f"[切换到 VL 模型失败: {switch_result.get('error')}]",
                        "model": local_client._model_name,
                        "model_type": current_type,
                        "tokens": 0,
                        "latency": 0.0,
                        "mode": "local",
                        "platform": "local_torch",
                        "switched": False,
                        "error": switch_result.get("error"),
                    }
                switched = True
                self.default_model = local_client._model_name

            # 调用 VL 模型
            start = time.time()
            result = local_client.chat_with_image(
                prompt=prompt,
                image=image,
                system_prompt=sys_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency = time.time() - start

            with self._lock:
                self.success_count += 1
                self.total_tokens += result.get("tokens", 0)
                self.total_latency += latency

            return {
                "content": result.get("content", ""),
                "model": result.get("model", self.default_model),
                "model_type": "vl",
                "tokens": result.get("tokens", 0),
                "latency": round(latency, 3),
                "mode": "local_vl",
                "platform": "local_torch",
                "switched": switched,
                "error": result.get("error"),
            }
        except Exception as e:
            with self._lock:
                self.fail_count += 1
            logger.error(f"视觉对话失败: {e}")
            return {
                "content": "",
                "model": self.default_model,
                "model_type": "vl",
                "tokens": 0,
                "latency": 0.0,
                "mode": "local_vl",
                "platform": "local_torch",
                "switched": switched,
                "error": str(e),
            }

    def switch_model_type(
        self, target_type: str, model_name: str = "",
        quantization: str = "auto", device: str = "auto"
    ) -> Dict[str, Any]:
        """切换本地模型类型（text ↔ vl）

        Args:
            target_type: "text" / "vl"
            model_name: 指定模型短名（为空自动选择）
            quantization: 量化方式
            device: 设备

        Returns:
            {success, prev_model, new_model, model_type, error}
        """
        try:
            from m_layer.local_model import get_local_model
            local_client = get_local_model()
            result = local_client.switch_model_type(
                target_type,
                model_name=(model_name or None),
                quantization=quantization,
                device=device,
            )
            # 同步激活平台状态
            if result.get("success"):
                with self._lock:
                    self._client = local_client
                    self.active_platform = "local_torch"
                    self.default_model = result.get("model_name") or result.get("new_model") or local_client._model_name
                    self.mode = "local"
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def chat_multiplatform(
        self,
        prompt: str,
        platform: str = "deepseek",
        model: str = "",
        system_prompt: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> Dict[str, Any]:
        """多平台调用：按指定平台调用对应大模型（不切换当前激活平台）"""
        cfg = self.PLATFORM_CONFIGS.get(platform)
        if not cfg:
            return {"content": f"[错误] 未知平台: {platform}", "error": "unknown_platform"}

        try:
            if cfg["local"]:
                base_url = os.getenv(cfg["env_url"], cfg["default_url"])
                # 探测模型
                with urllib.request.urlopen(f"{base_url}/models", timeout=2) as r:
                    data = _json.loads(r.read().decode("utf-8"))
                    models = data.get("data", [])
                use_model = model or (models[0].get("id", "") if models else cfg["default_model"])
                api_key = "local"
            else:
                # 读取 API Key（支持主备变量名）
                api_key = os.getenv(cfg["env_key"], "")
                if not api_key and cfg.get("env_key_alt"):
                    api_key = os.getenv(cfg["env_key_alt"], "")
                if not api_key:
                    return {"content": f"[错误] {cfg['label']} API Key未配置", "error": "no_key"}
                # 读取 base_url（支持主备变量名）
                base_url = os.getenv(cfg["env_url"], "")
                if not base_url and cfg.get("env_url_alt"):
                    base_url = os.getenv(cfg["env_url_alt"], cfg["default_url"])
                base_url = base_url or cfg["default_url"]
                use_model = model or os.getenv(platform.upper() + "_MODEL", cfg["default_model"])

            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=self.timeout)
            sys_msg = self.SYSTEM_PROMPTS.get(system_prompt, system_prompt)
            start = time.time()
            resp = client.chat.completions.create(
                model=use_model,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency = time.time() - start
            content = resp.choices[0].message.content or ""
            usage = resp.usage
            pt = (usage.prompt_tokens or 0) if usage else 0
            ct = (usage.completion_tokens or 0) if usage else 0

            with self._lock:
                self.call_count += 1
                self.success_count += 1
                self.total_tokens += pt + ct
                self.total_latency += latency

            return {
                "content": content,
                "model": use_model,
                "platform": cfg["label"],
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": pt + ct,
                "latency": round(latency, 3),
                "mode": "multiplatform",
                "error": None,
            }
        except Exception as e:
            with self._lock:
                self.call_count += 1
                self.fail_count += 1
            return {
                "content": "",
                "model": use_model if 'use_model' in dir() else "",
                "platform": cfg["label"],
                "error": str(e),
                "latency": 0,
                "mode": "multiplatform",
            }

    def _rule_based_reply(
        self, prompt: str, system_prompt: str = "default",
        context: str = "", error: str = ""
    ) -> Dict[str, Any]:
        """规则模式回复（无API Key且无本地模型时的降级）"""
        with self._lock:
            self.success_count += 1
        latency = 0.001

        # 基于上下文和输入生成规则回复
        if context:
            reply = f"基于知识库的回复：\n\n{context[:500]}\n\n（注：当前为规则模式，配置DEEPSEEK_API_KEY或启动本地LLM可启用智能回复）"
        elif any(w in prompt for w in ["你好", "hello", "hi", "嗨"]):
            reply = "你好！我是标准计算单元2（SCU3），带CUF熵税守卫的智能助手。当前为规则模式，配置DEEPSEEK_API_KEY或启动本地LLM（LM Studio/Ollama）可启用智能回复。"
        elif any(w in prompt for w in ["你是谁", "介绍", "who"]):
            reply = "我是SCU3标准计算单元2，采用v3三维度分离架构：数据流×权限层×守卫横切。所有操作经CUF守卫审计，按五维熵税计费。"
        elif any(w in prompt for w in ["谢谢", "感谢", "thanks"]):
            reply = "不客气！很高兴能帮到你。"
        elif "?" in prompt or "？" in prompt:
            reply = f"关于「{prompt}」的问题，当前规则模式无法深入解答。配置DEEPSEEK_API_KEY或启动本地LLM可启用智能回复。"
        else:
            reply = f"收到你的消息：「{prompt}」。当前为规则模式，配置DEEPSEEK_API_KEY或启动本地LLM可启用智能回复。"

        if error:
            reply += f"\n\n（LLM调用失败已降级: {error[:50]}）"

        return {
            "content": reply,
            "model": "rule-based",
            "tokens": len(prompt) + len(reply),
            "latency": latency,
            "mode": "rule_based",
            "platform": "rule_based",
            "error": error or None,
        }

    def get_status(self) -> Dict[str, Any]:
        """获取客户端状态"""
        success_rate = (self.success_count / self.call_count * 100) if self.call_count > 0 else 0
        avg_latency = (self.total_latency / self.success_count) if self.success_count > 0 else 0
        return {
            "mode": self.mode,
            "platform": self.active_platform,
            "model": self.default_model,
            "call_count": self.call_count,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "success_rate": round(success_rate, 1),
            "total_tokens": self.total_tokens,
            "avg_latency": round(avg_latency, 3),
            "api_key_configured": bool(self.api_key),
            "available_platforms": len(self.list_available_platforms()),
        }


# 全局单例
_client: LLMClient = None


def get_client() -> LLMClient:
    """获取LLM客户端单例"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
