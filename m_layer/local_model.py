# -*- coding: utf-8 -*-
"""
m_layer/local_model.py — 本地大模型客户端（M层）
====================================================
阶段1增强：通过 transformers 库直接加载本地大模型（Qwen-7B / GLM-4-9B 等）

支持模型：
  - Qwen-7B-Chat / Qwen2-7B-Instruct / Qwen2.5-7B-Instruct
  - GLM-4-9B-Chat

特性：
  1. 多种量化加载（4bit NF4 / 8bit / FP16/BF16 / 自动）
  2. 推理优化（KV Cache、流式生成、批处理、GPU自动检测）
  3. 降级策略（transformers未安装/模型未下载/显存不足/加载失败 → Ollama）
  4. 模型管理（状态跟踪、显存监控、闲置自动卸载、模型预热）
  5. 对话格式（Qwen ChatML / GLM 特殊token / 通用 OpenAI 消息格式）
  6. 配置持久化到 SCU3_data/local_model_config.json
  7. 单例模式 get_local_model()
  8. 与 LLMClient 集成（to_llm_compatible / is_available）

架构归属：M层（认知层调用本地大模型生成回复）
依赖方向：M层→D层（只读axioms），W1层（账本计税）
依赖：可选 transformers / torch / accelerate / bitsandbytes
"""
import os
import time
import json
import logging
import threading
from typing import Optional, List, Dict, Any, Generator, Union

logger = logging.getLogger("SCU3.m.local_model")

# ─── 项目路径 ────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "SCU3_data")
CONFIG_PATH = os.path.join(DATA_DIR, "local_model_config.json")
os.makedirs(DATA_DIR, exist_ok=True)


# ─── 外部依赖可选导入 ────────────────────────────────────
# transformers / torch 为可选依赖：未安装时本模块仍可实例化，仅 model_loaded=False
_TORCH_AVAILABLE = False
_TRANSFORMERS_AVAILABLE = False
_BITSANDBYTES_AVAILABLE = False
_ACCELERATE_AVAILABLE = False

try:
    import torch
    _TORCH_AVAILABLE = True
except ImportError:
    logger.debug("torch 不可用，本地大模型加载功能将受限")

try:
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        TextIteratorStreamer,
        GenerationConfig,
    )
    _TRANSFORMERS_AVAILABLE = True
except ImportError:
    logger.debug("transformers 不可用，本地大模型加载功能不可用")

# Qwen2.5-VL 专用类（可选导入，未安装时 VL 功能不可用）
_QWEN_VL_AVAILABLE = False
try:
    from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
    _QWEN_VL_AVAILABLE = True
except ImportError:
    try:
        # 某些 transformers 版本用不同路径
        from transformers import AutoModelForVision2Seq
        _QWEN_VL_AVAILABLE = True
    except ImportError:
        logger.debug("Qwen2.5-VL 专用类不可用，视觉模型将使用 AutoModelForCausalLM 兜底")

try:
    import bitsandbytes as _bnb  # noqa: F401
    _BITSANDBYTES_AVAILABLE = True
except ImportError:
    logger.debug("bitsandbytes 不可用，4bit/8bit 量化加载将受限")

try:
    import accelerate as _accel  # noqa: F401
    _ACCELERATE_AVAILABLE = True
except ImportError:
    logger.debug("accelerate 不可用，device_map='auto' 可能受限")


# ─── 支持的模型预设配置 ────────────────────────────────────
# model_type: "text" 文本模型 / "vl" 视觉语言模型
SUPPORTED_MODELS: Dict[str, Dict[str, Any]] = {
    "qwen-7b": {
        "model_id": "Qwen/Qwen-7B-Chat",
        "quantized_id": "Qwen/Qwen-7B-Chat-Int4",
        "context_length": 32768,
        "min_memory_gb": 8,
        "quantized_memory_gb": 5,
        "family": "qwen",
        "model_type": "text",
        "label": "Qwen-7B-Chat",
    },
    "qwen2-7b": {
        "model_id": "Qwen/Qwen2-7B-Instruct",
        "quantized_id": "Qwen/Qwen2-7B-Instruct-GPTQ-Int4",
        "context_length": 32768,
        "min_memory_gb": 8,
        "quantized_memory_gb": 5,
        "family": "qwen",
        "model_type": "text",
        "label": "Qwen2-7B-Instruct",
    },
    "glm4-9b": {
        "model_id": "THUDM/glm-4-9b-chat",
        "quantized_id": "THUDM/glm-4-9b-chat-1-8b-int4",  # 1.8B量化版
        "context_length": 8192,
        "min_memory_gb": 10,
        "quantized_memory_gb": 6,
        "family": "glm",
        "model_type": "text",
        "label": "GLM-4-9B-Chat",
    },
    "qwen2-5-7b": {
        "model_id": "Qwen/Qwen2.5-7B-Instruct",
        "quantized_id": "Qwen/Qwen2.5-7B-Instruct-Int4",
        "context_length": 32768,
        "min_memory_gb": 8,
        "quantized_memory_gb": 5,
        "family": "qwen",
        "model_type": "text",
        "label": "Qwen2.5-7B-Instruct",
    },
    "qwen2-5-vl-7b": {
        "model_id": "Qwen/Qwen2.5-VL-7B-Instruct",
        "quantized_id": "Qwen/Qwen2.5-VL-7B-Instruct",
        "context_length": 32768,
        "min_memory_gb": 12,
        "quantized_memory_gb": 9,
        "family": "qwen-vl",
        "model_type": "vl",
        "label": "Qwen2.5-VL-7B-Instruct",
    },
    "qwen2-5-vl-3b": {
        "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
        "quantized_id": "Qwen/Qwen2.5-VL-3B-Instruct",
        "context_length": 32768,
        "min_memory_gb": 6,
        "quantized_memory_gb": 3,
        "family": "qwen-vl",
        "model_type": "vl",
        "label": "Qwen2.5-VL-3B-Instruct",
    },
}

# 量化方式枚举
QUANTIZATION_NONE = "none"
QUANTIZATION_4BIT = "4bit"
QUANTIZATION_8BIT = "8bit"
QUANTIZATION_AUTO = "auto"
_VALID_QUANTIZATIONS = (QUANTIZATION_NONE, QUANTIZATION_4BIT, QUANTIZATION_8BIT, QUANTIZATION_AUTO)


def _detect_device() -> str:
    """自动检测最优推理设备（CUDA > MPS > CPU）"""
    if _TORCH_AVAILABLE:
        try:
            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except Exception:
            pass
    return "cpu"


def _get_gpu_memory_gb() -> Optional[float]:
    """获取可用 GPU 显存（GB），无 GPU 返回 None"""
    if not _TORCH_AVAILABLE:
        return None
    try:
        if torch.cuda.is_available():
            # free 显存（字节）→ GB
            free, _total = torch.cuda.mem_get_info()
            return round(free / (1024 ** 3), 2)
    except Exception as e:
        logger.debug(f"获取GPU显存失败: {e}")
    return None


class LocalModelClient:
    """本地大模型客户端

    用法:
        client = LocalModelClient()
        # 列出支持的模型
        client.list_supported_models()
        # 加载模型
        client.load_model("qwen2-7b", quantization="auto", device="cuda")
        # 对话
        reply = client.chat("你好")
        # 流式对话
        for chunk in client.chat_stream("讲个故事"):
            print(chunk, end="")
        # 卸载
        client.unload_model()
    """

    # 默认空闲超时（秒）：30分钟
    DEFAULT_IDLE_TIMEOUT = 30 * 60
    # 默认系统提示词
    DEFAULT_SYSTEM_PROMPT = "你是标准计算单元2（SCU3），一个带CUF熵税守卫的智能助手。回答简洁准确。"

    def __init__(
        self,
        idle_timeout: int = DEFAULT_IDLE_TIMEOUT,
        auto_unload: bool = True,
        config_path: str = CONFIG_PATH,
    ):
        """初始化本地模型客户端

        Args:
            idle_timeout: 闲置自动卸载超时（秒），默认 30 分钟
            auto_unload: 是否启用闲置自动卸载
            config_path: 配置持久化路径
        """
        self.config_path = config_path
        self.idle_timeout = idle_timeout
        self.auto_unload = auto_unload

        # 运行时状态
        self._model = None
        self._tokenizer = None
        self._processor = None  # VL 模型的 AutoProcessor
        self._model_name: Optional[str] = None
        self._quantization: Optional[str] = None
        self._device: str = "cpu"
        self._model_config: Optional[Dict[str, Any]] = None
        self._model_type: str = "text"  # "text" / "vl"

        # 状态跟踪
        self._lock = threading.RLock()
        self._model_loaded = False
        self._last_used_ts: float = 0.0
        self._loaded_at: float = 0.0
        self._call_count = 0
        self._success_count = 0
        self._fail_count = 0
        self._total_tokens = 0
        self._total_latency = 0.0
        self._last_error: Optional[str] = None

        # 后台清理线程（守护）
        self._cleaner_stop = threading.Event()
        self._cleaner_thread: Optional[threading.Thread] = None
        if self.auto_unload:
            self._start_cleaner()

        # 从配置文件恢复状态（不自动加载模型，仅恢复配置项）
        self._load_config()

        logger.info(
            f"LocalModelClient 已初始化 "
            f"(transformers={_TRANSFORMERS_AVAILABLE}, torch={_TORCH_AVAILABLE}, "
            f"bitsandbytes={_BITSANDBYTES_AVAILABLE})"
        )

    # ─── 模型加载/卸载 ────────────────────────────────────

    def load_model(
        self,
        model_name: str,
        quantization: str = QUANTIZATION_AUTO,
        device: str = "auto",
    ) -> Dict[str, Any]:
        """加载本地大模型

        Args:
            model_name: 模型短名（qwen-7b/qwen2-7b/glm4-9b/qwen2-5-7b）或完整 HF model_id
            quantization: 量化方式 (none/4bit/8bit/auto)
            device: 设备 (auto/cuda/mps/cpu)

        Returns:
            {success, model_name, quantization, device, error}
        """
        # 1) 依赖检查
        if not _TRANSFORMERS_AVAILABLE or not _TORCH_AVAILABLE:
            msg = "transformers/torch 未安装，无法加载本地大模型。请执行: pip install transformers torch accelerate"
            logger.error(msg)
            self._last_error = msg
            return {"success": False, "model_name": model_name, "quantization": quantization,
                    "device": device, "error": msg}

        # 2) 解析模型配置
        model_cfg = SUPPORTED_MODELS.get(model_name)
        if model_cfg is None:
            # 视为完整 model_id，构造临时配置
            # 自动识别 VL 模型
            is_vl = "vl" in model_name.lower() or "vision" in model_name.lower()
            model_cfg = {
                "model_id": model_name,
                "quantized_id": model_name,
                "context_length": 8192,
                "min_memory_gb": 12 if is_vl else 8,
                "quantized_memory_gb": 9 if is_vl else 5,
                "family": "qwen-vl" if is_vl else "qwen",
                "model_type": "vl" if is_vl else "text",
                "label": model_name,
            }
            logger.info(f"未在预设列表中找到 {model_name}，按完整 model_id 加载 (type={model_cfg['model_type']})")

        # 3) 解析量化方式
        if quantization not in _VALID_QUANTIZATIONS:
            return {"success": False, "model_name": model_name, "quantization": quantization,
                    "device": device, "error": f"未知量化方式: {quantization}"}

        # 4) 解析设备
        if device == "auto":
            device = _detect_device()
        if device == "cuda" and not (_TORCH_AVAILABLE and torch.cuda.is_available()):
            logger.warning("CUDA 不可用，降级到 CPU")
            device = "cpu"

        # 5) 自动选择量化方式
        actual_quant = quantization
        if quantization == QUANTIZATION_AUTO:
            actual_quant = self._auto_select_quantization(model_cfg, device)
            logger.info(f"自动选择量化方式: {actual_quant} (device={device})")

        # 6) 卸载已有模型（避免显存占用）
        if self._model_loaded:
            logger.info("切换模型，先卸载当前模型")
            self.unload_model()

        # 7) 选择 model_id
        # 4bit策略：优先用bnb量化原始模型（最稳健），GPTQ预量化模型需额外依赖
        # 仅当quantized_id以"-Int4"/"-GPTQ"/"-AWQ"结尾且bnb不可用时才用预量化版
        use_prequantized = False
        if actual_quant == QUANTIZATION_4BIT:
            if _BITSANDBYTES_AVAILABLE:
                # bnb可用：用原始模型 + bnb 4bit量化（最稳健，无需额外依赖）
                model_id = model_cfg["model_id"]
                use_prequantized = False
            elif model_cfg.get("quantized_id"):
                # bnb不可用：尝试用GPTQ/AWQ预量化模型
                model_id = model_cfg["quantized_id"]
                use_prequantized = True
            else:
                model_id = model_cfg["model_id"]
        else:
            model_id = model_cfg["model_id"]

        # 8) 构造加载参数
        # 注意：预量化模型（GPTQ/AWQ）自带量化配置，不能再传 BitsAndBytesConfig
        if use_prequantized:
            load_kwargs = self._build_load_kwargs(QUANTIZATION_NONE, device)
        else:
            load_kwargs = self._build_load_kwargs(actual_quant, device)

        start = time.time()
        try:
            logger.info(f"开始加载模型: {model_id} (quantization={actual_quant}, device={device}, type={model_cfg.get('model_type', 'text')})")

            is_vl_model = model_cfg.get("model_type") == "vl"

            # tokenizer / processor
            if is_vl_model:
                # VL 模型使用 AutoProcessor（包含 tokenizer + image processor）
                if _QWEN_VL_AVAILABLE:
                    try:
                        self._processor = AutoProcessor.from_pretrained(
                            model_id, trust_remote_code=True
                        )
                        self._tokenizer = self._processor.tokenizer
                        logger.info("VL 模型使用 AutoProcessor 加载")
                    except Exception as e:
                        logger.warning(f"AutoProcessor 加载失败，回退到 AutoTokenizer: {e}")
                        self._tokenizer = AutoTokenizer.from_pretrained(
                            model_id, trust_remote_code=True
                        )
                        self._processor = None
                else:
                    self._tokenizer = AutoTokenizer.from_pretrained(
                        model_id, trust_remote_code=True
                    )
                    self._processor = None
                if self._tokenizer is not None and self._tokenizer.pad_token is None:
                    self._tokenizer.pad_token = self._tokenizer.eos_token
            else:
                self._processor = None
                self._tokenizer = AutoTokenizer.from_pretrained(
                    model_id, trust_remote_code=True, **({} if actual_quant == QUANTIZATION_4BIT else {"padding_side": "left"})
                )
                if self._tokenizer.pad_token is None:
                    self._tokenizer.pad_token = self._tokenizer.eos_token

            # model
            if is_vl_model and _QWEN_VL_AVAILABLE:
                try:
                    from transformers import Qwen2_5_VLForConditionalGeneration
                    self._model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                        model_id, trust_remote_code=True, **load_kwargs
                    )
                    logger.info("使用 Qwen2_5_VLForConditionalGeneration 加载")
                except ImportError:
                    # 回退到 AutoModelForVision2Seq
                    try:
                        from transformers import AutoModelForVision2Seq
                        self._model = AutoModelForVision2Seq.from_pretrained(
                            model_id, trust_remote_code=True, **load_kwargs
                        )
                        logger.info("使用 AutoModelForVision2Seq 加载")
                    except Exception:
                        self._model = AutoModelForCausalLM.from_pretrained(
                            model_id, trust_remote_code=True, **load_kwargs
                        )
                        logger.info("使用 AutoModelForCausalLM 加载（兜底）")
            else:
                self._model = AutoModelForCausalLM.from_pretrained(
                    model_id, trust_remote_code=True, **load_kwargs
                )

            # 切换到评估模式 + 启用 KV Cache
            self._model.eval()
            try:
                self._model.config.use_cache = True
            except Exception:
                pass

            # device 映射后无需手动 to(device)；显式 device 时迁移
            if "device_map" not in load_kwargs and device != "cpu":
                try:
                    self._model = self._model.to(device)
                except Exception as e:
                    logger.warning(f"模型迁移到 {device} 失败，保持原设备: {e}")

            # 状态更新
            with self._lock:
                self._model_name = model_name
                self._quantization = actual_quant
                self._device = device
                self._model_config = model_cfg
                self._model_type = model_cfg.get("model_type", "text")
                self._model_loaded = True
                self._loaded_at = time.time()
                self._last_used_ts = self._loaded_at
                self._last_error = None

            latency = round(time.time() - start, 2)
            logger.info(f"模型加载成功: {model_id} (耗时 {latency}s)")

            # 模型预热（空推理）
            self._warmup()

            # 持久化配置
            self._save_config()

            return {
                "success": True,
                "model_name": model_name,
                "model_id": model_id,
                "quantization": actual_quant,
                "device": device,
                "load_latency": latency,
                "error": None,
            }
        except Exception as e:
            err_msg = str(e)
            self._last_error = err_msg
            logger.error(f"模型加载失败: {err_msg}")

            # 模型未下载提示
            if "not a valid model identifier" in err_msg or "Repository Not Found" in err_msg or "Connection" in err_msg:
                hint = f"模型可能未下载。请执行: huggingface-cli download {model_id}"
                logger.error(hint)
                err_msg = f"{err_msg} | {hint}"

            # 显存不足降级
            if self._is_oom_error(err_msg):
                logger.warning("显存不足，尝试降级到 CPU 或更大量化")
                if device != "cpu":
                    logger.info("降级重试: device=cpu")
                    return self.load_model(model_name, quantization=actual_quant, device="cpu")
                if actual_quant != QUANTIZATION_4BIT:
                    logger.info("降级重试: quantization=4bit")
                    return self.load_model(model_name, quantization=QUANTIZATION_4BIT, device=device)

            return {
                "success": False,
                "model_name": model_name,
                "quantization": actual_quant,
                "device": device,
                "error": err_msg,
                "fallback_hint": "可尝试启动 Ollama 作为降级方案",
            }

    def unload_model(self) -> Dict[str, Any]:
        """卸载当前模型，释放显存"""
        with self._lock:
            prev_name = self._model_name
            prev_quant = self._quantization
            prev_device = self._device

            self._model = None
            self._tokenizer = None
            self._processor = None
            self._model_name = None
            self._quantization = None
            self._model_config = None
            self._model_type = "text"
            self._model_loaded = False

        # 释放显存（在锁外执行，避免长时间持锁）
        if _TORCH_AVAILABLE:
            try:
                import gc
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.ipc_collect()
            except Exception as e:
                logger.debug(f"释放显存失败: {e}")

        if prev_name:
            logger.info(f"模型已卸载: {prev_name} (quantization={prev_quant}, device={prev_device})")
        self._save_config()
        return {"success": True, "unloaded": prev_name}

    # ─── 对话接口 ────────────────────────────────────

    def chat(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 1024,
        context: str = "",
    ) -> Dict[str, Any]:
        """同步对话

        Args:
            prompt: 用户输入
            system_prompt: 系统提示词（为空用默认）
            temperature: 采样温度
            max_tokens: 最大生成 token 数
            context: RAG 上下文（拼接在用户输入前）

        Returns:
            {content, model, tokens, latency, mode, error}
        """
        if not self._model_loaded or self._model is None or self._tokenizer is None:
            return self._unavailable_reply(prompt)

        with self._lock:
            self._call_count += 1
            self._last_used_ts = time.time()

        sys_msg = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        full_prompt = f"{context}\n\n用户问题：{prompt}" if context else prompt

        # 构造输入
        input_text = self._build_prompt(full_prompt, sys_msg)
        start = time.time()
        try:
            inputs = self._tokenizer(input_text, return_tensors="pt")
            # 迁移到模型设备
            inputs = self._move_inputs_to_device(inputs)

            # 生成配置
            gen_config = self._make_gen_config(temperature, max_tokens)

            with self._lock:  # 生成期间锁定模型
                output_ids = self._model.generate(
                    **inputs,
                    generation_config=gen_config,
                )

            # 仅取新生成的部分
            input_len = inputs["input_ids"].shape[-1]
            new_ids = output_ids[0, input_len:]
            content = self._tokenizer.decode(new_ids, skip_special_tokens=True).strip()

            latency = round(time.time() - start, 3)
            tokens = int(new_ids.shape[-1])

            with self._lock:
                self._success_count += 1
                self._total_tokens += tokens
                self._total_latency += latency

            return {
                "content": content,
                "model": self._model_name,
                "quantization": self._quantization,
                "device": self._device,
                "tokens": tokens,
                "latency": latency,
                "mode": "local_transformers",
                "platform": "local",
                "error": None,
            }
        except Exception as e:
            latency = round(time.time() - start, 3)
            err_msg = str(e)
            self._last_error = err_msg
            with self._lock:
                self._fail_count += 1
            logger.error(f"本地模型推理失败: {err_msg}")

            # OOM 降级
            if self._is_oom_error(err_msg):
                logger.warning("推理时显存不足，卸载模型并提示降级")
                self.unload_model()

            return {
                "content": "",
                "model": self._model_name,
                "tokens": 0,
                "latency": latency,
                "mode": "local_transformers",
                "platform": "local",
                "error": err_msg,
            }

    def chat_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 1024,
        context: str = "",
    ) -> Generator[str, None, None]:
        """流式对话，逐块 yield 文本

        Args: 同 chat()
        Yields: 文本片段
        """
        if not self._model_loaded or self._model is None or self._tokenizer is None:
            yield "[本地模型未加载]"
            return

        with self._lock:
            self._call_count += 1
            self._last_used_ts = time.time()

        sys_msg = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        full_prompt = f"{context}\n\n用户问题：{prompt}" if context else prompt
        input_text = self._build_prompt(full_prompt, sys_msg)

        if not _TRANSFORMERS_AVAILABLE:
            yield "[transformers 未安装]"
            return

        try:
            inputs = self._tokenizer(input_text, return_tensors="pt")
            inputs = self._move_inputs_to_device(inputs)

            # TextIteratorStreamer 流式输出
            streamer = TextIteratorStreamer(
                self._tokenizer,
                skip_prompt=True,
                skip_special_tokens=True,
            )
            gen_config = self._make_gen_config(temperature, max_tokens)

            # 后台线程执行生成
            gen_thread = threading.Thread(
                target=self._model.generate,
                kwargs={
                    **inputs,
                    "generation_config": gen_config,
                    "streamer": streamer,
                },
                daemon=True,
            )
            with self._lock:
                gen_thread.start()

            total_tokens = 0
            for chunk in streamer:
                if chunk:
                    total_tokens += 1
                    yield chunk

            gen_thread.join(timeout=5.0)

            with self._lock:
                self._success_count += 1
                self._total_tokens += total_tokens
        except Exception as e:
            err_msg = str(e)
            self._last_error = err_msg
            with self._lock:
                self._fail_count += 1
            logger.error(f"流式推理失败: {err_msg}")
            yield f"[LLM错误: {err_msg}]"

    def chat_batch(
        self,
        prompts: List[str],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> List[Dict[str, Any]]:
        """批处理对话（一次推理多个 prompt）

        Args:
            prompts: 用户输入列表
            其余同 chat()

        Returns:
            结果列表（与 prompts 等长）
        """
        if not self._model_loaded or self._model is None or self._tokenizer is None:
            return [self._unavailable_reply(p) for p in prompts]

        sys_msg = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        results: List[Dict[str, Any]] = []
        for p in prompts:
            results.append(self.chat(p, system_prompt=sys_msg, temperature=temperature, max_tokens=max_tokens))
        return results

    # ─── 视觉对话接口（VL 模型专用）────────────────────────

    def chat_with_image(
        self,
        prompt: str,
        image: Union[str, bytes, Dict[str, Any]],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """视觉对话（仅 VL 模型可用）

        Args:
            prompt: 文本提示词
            image: 图像输入。支持：
                - str: 本地文件路径 / HTTP(S) URL / base64 字符串
                - bytes: 原始图像字节
                - dict: {"path": "..."} / {"url": "..."} / {"base64": "..."}
            system_prompt: 系统提示词
            temperature: 采样温度
            max_tokens: 最大生成 token 数

        Returns:
            {content, model, tokens, latency, mode, platform, error}
        """
        if not self._model_loaded or self._model is None:
            return self._unavailable_reply(prompt)
        if self._model_type != "vl":
            return {
                "content": "[当前模型不支持视觉理解，请切换到 VL 模型]",
                "model": self._model_name,
                "tokens": 0,
                "latency": 0.0,
                "mode": "local_transformers",
                "platform": "local",
                "error": "model_not_vl",
            }
        if not _QWEN_VL_AVAILABLE or self._processor is None:
            return {
                "content": "[VL 处理器不可用，请检查 transformers 版本]",
                "model": self._model_name,
                "tokens": 0,
                "latency": 0.0,
                "mode": "local_transformers",
                "platform": "local",
                "error": "processor_unavailable",
            }

        with self._lock:
            self._call_count += 1
            self._last_used_ts = time.time()

        sys_msg = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        start = time.time()
        try:
            # 构造消息内容（Qwen2.5-VL ChatML 多模态格式）
            image_obj = self._load_image(image)
            messages = [
                {"role": "system", "content": [{"type": "text", "text": sys_msg}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image_obj},
                        {"type": "text", "text": prompt},
                    ],
                },
            ]

            # 使用 processor 应用 chat template
            text = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

            # 构造 inputs（含 image pixel_values）
            inputs = self._processor(
                text=[text],
                images=[image_obj],
                return_tensors="pt",
                padding=True,
            )
            inputs = self._move_inputs_to_device(inputs)

            gen_config = self._make_gen_config(temperature, max_tokens)

            with self._lock:
                output_ids = self._model.generate(**inputs, generation_config=gen_config)

            # 截取新生成部分（剔除输入）
            input_len = inputs["input_ids"].shape[-1]
            new_ids = output_ids[0, input_len:]
            content = self._tokenizer.decode(new_ids, skip_special_tokens=True).strip()

            latency = round(time.time() - start, 3)
            tokens = int(new_ids.shape[-1])

            with self._lock:
                self._success_count += 1
                self._total_tokens += tokens
                self._total_latency += latency

            return {
                "content": content,
                "model": self._model_name,
                "model_type": "vl",
                "quantization": self._quantization,
                "device": self._device,
                "tokens": tokens,
                "latency": latency,
                "mode": "local_transformers_vl",
                "platform": "local",
                "error": None,
            }
        except Exception as e:
            latency = round(time.time() - start, 3)
            err_msg = str(e)
            self._last_error = err_msg
            with self._lock:
                self._fail_count += 1
            logger.error(f"VL 模型推理失败: {err_msg}")

            if self._is_oom_error(err_msg):
                logger.warning("VL 推理时显存不足，卸载模型")
                self.unload_model()

            return {
                "content": "",
                "model": self._model_name,
                "model_type": "vl",
                "tokens": 0,
                "latency": latency,
                "mode": "local_transformers_vl",
                "platform": "local",
                "error": err_msg,
            }

    def _load_image(self, image: Union[str, bytes, Dict[str, Any]]):
        """将多种图像输入格式加载为 PIL.Image 对象"""
        try:
            from PIL import Image
        except ImportError as e:
            raise RuntimeError("Pillow 未安装，请执行: pip install pillow") from e

        # 解析输入
        if isinstance(image, dict):
            if "path" in image:
                source = image["path"]
            elif "url" in image:
                source = image["url"]
            elif "base64" in image:
                source = image["base64"]
            else:
                raise ValueError(f"图像 dict 缺少 path/url/base64 字段: {list(image.keys())}")
        else:
            source = image

        # str：路径 / URL / base64
        if isinstance(source, str):
            # URL
            if source.startswith(("http://", "https://")):
                import urllib.request
                with urllib.request.urlopen(source, timeout=10) as r:
                    import io
                    return Image.open(io.BytesIO(r.read())).convert("RGB")
            # base64（含或不含 data:前缀）
            if source.startswith("data:image"):
                import base64, io
                b64_data = source.split(",", 1)[-1]
                return Image.open(io.BytesIO(base64.b64decode(b64_data))).convert("RGB")
            # 纯 base64（无前缀，长度异常）
            if len(source) > 256 and not os.path.exists(source):
                try:
                    import base64, io
                    return Image.open(io.BytesIO(base64.b64decode(source))).convert("RGB")
                except Exception:
                    pass
            # 本地路径
            return Image.open(source).convert("RGB")

        # bytes：原始图像字节
        if isinstance(source, (bytes, bytearray)):
            import io
            return Image.open(io.BytesIO(bytes(source))).convert("RGB")

        # 已经是 PIL.Image
        if hasattr(source, "convert"):
            return source.convert("RGB")

        raise ValueError(f"不支持的图像输入类型: {type(source)}")

    # ─── 模型类型切换 ────────────────────────────────────

    def switch_model_type(
        self,
        target_type: str,
        model_name: Optional[str] = None,
        quantization: str = QUANTIZATION_AUTO,
        device: str = "auto",
    ) -> Dict[str, Any]:
        """切换模型类型（text ↔ vl）

        按方案 A：文本模型与视觉模型不同时加载，切换时卸载当前模型并加载目标模型。

        Args:
            target_type: 目标类型 ("text" / "vl")
            model_name: 目标模型短名。为空则按类型自动选择：
                text → qwen2-5-7b；vl → qwen2-5-vl-7b
            quantization: 量化方式
            device: 设备

        Returns:
            {success, prev_model, new_model, model_type, error}
        """
        target_type = (target_type or "").lower().strip()
        if target_type not in ("text", "vl"):
            return {"success": False, "error": f"未知模型类型: {target_type}（应为 text/vl）"}

        # 自动选择模型
        if model_name is None:
            model_name = "qwen2-5-7b" if target_type == "text" else "qwen2-5-vl-7b"

        # 校验目标模型类型匹配
        target_cfg = SUPPORTED_MODELS.get(model_name)
        if target_cfg is None:
            return {"success": False, "error": f"未知模型: {model_name}"}
        if target_cfg.get("model_type") != target_type:
            return {
                "success": False,
                "error": f"模型 {model_name} 类型为 {target_cfg.get('model_type')}，与目标类型 {target_type} 不匹配",
            }

        # 已经是目标模型
        if self._model_loaded and self._model_name == model_name and self._model_type == target_type:
            return {
                "success": True,
                "prev_model": self._model_name,
                "new_model": model_name,
                "model_type": target_type,
                "message": "目标模型已加载，无需切换",
                "error": None,
            }

        prev_model = self._model_name
        logger.info(f"切换模型类型: {prev_model}({self._model_type}) → {model_name}({target_type})")

        # 卸载当前模型（释放显存）
        if self._model_loaded:
            self.unload_model()

        # 加载目标模型
        result = self.load_model(model_name, quantization=quantization, device=device)
        result["prev_model"] = prev_model
        result["model_type"] = target_type
        return result

    def ensure_model_type(
        self,
        target_type: str,
        quantization: str = QUANTIZATION_AUTO,
        device: str = "auto",
    ) -> Dict[str, Any]:
        """确保当前加载的模型类型与目标一致，不一致则切换

        用于按任务类型自动路由：文本任务→text 模型，视觉任务→vl 模型

        Args:
            target_type: 目标类型 ("text" / "vl")
            quantization: 切换时的量化方式
            device: 切换时的设备

        Returns:
            {switched, model_name, model_type, error}
        """
        target_type = (target_type or "").lower().strip()
        if target_type not in ("text", "vl"):
            return {"switched": False, "error": f"未知模型类型: {target_type}"}

        # 类型已匹配，无需切换
        if self._model_loaded and self._model_type == target_type:
            return {
                "switched": False,
                "model_name": self._model_name,
                "model_type": self._model_type,
                "error": None,
            }

        # 模型未加载或类型不匹配，执行切换
        result = self.switch_model_type(target_type, quantization=quantization, device=device)
        return {
            "switched": result.get("success", False),
            "model_name": result.get("model_name") or result.get("new_model"),
            "model_type": target_type if result.get("success") else self._model_type,
            "error": result.get("error"),
        }

    # ─── 状态/健康/列表 ────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """获取客户端状态"""
        with self._lock:
            success_rate = (self._success_count / self._call_count * 100) if self._call_count > 0 else 0
            avg_latency = (self._total_latency / self._success_count) if self._success_count > 0 else 0
            idle_seconds = (time.time() - self._last_used_ts) if self._last_used_ts else 0

            return {
                "model_loaded": self._model_loaded,
                "model_name": self._model_name,
                "model_type": self._model_type,
                "is_vl_model": self._model_type == "vl",
                "quantization": self._quantization,
                "device": self._device,
                "model_config": self._model_config,
                "loaded_at": self._loaded_at,
                "last_used_ts": self._last_used_ts,
                "idle_seconds": round(idle_seconds, 1),
                "auto_unload": self.auto_unload,
                "idle_timeout": self.idle_timeout,
                "call_count": self._call_count,
                "success_count": self._success_count,
                "fail_count": self._fail_count,
                "success_rate": round(success_rate, 1),
                "total_tokens": self._total_tokens,
                "avg_latency": round(avg_latency, 3),
                "last_error": self._last_error,
                "gpu_memory_free_gb": _get_gpu_memory_gb(),
                "dependencies": {
                    "torch": _TORCH_AVAILABLE,
                    "transformers": _TRANSFORMERS_AVAILABLE,
                    "bitsandbytes": _BITSANDBYTES_AVAILABLE,
                    "accelerate": _ACCELERATE_AVAILABLE,
                    "qwen_vl": _QWEN_VL_AVAILABLE,
                },
            }

    def health_check(self) -> bool:
        """健康检查：模型已加载且能成功做一次空推理"""
        if not self._model_loaded or self._model is None or self._tokenizer is None:
            return False
        try:
            # 1 token 推理
            inputs = self._tokenizer("hi", return_tensors="pt")
            inputs = self._move_inputs_to_device(inputs)
            with self._lock:
                _ = self._model.generate(**inputs, max_new_tokens=1)
            self._last_used_ts = time.time()
            return True
        except Exception as e:
            logger.warning(f"健康检查失败: {e}")
            self._last_error = str(e)
            return False

    def list_supported_models(self) -> List[Dict[str, Any]]:
        """列出支持的模型（含预设配置）"""
        result = []
        for name, cfg in SUPPORTED_MODELS.items():
            result.append({
                "name": name,
                "model_id": cfg["model_id"],
                "quantized_id": cfg.get("quantized_id"),
                "label": cfg.get("label", name),
                "family": cfg.get("family"),
                "model_type": cfg.get("model_type", "text"),
                "context_length": cfg["context_length"],
                "min_memory_gb": cfg["min_memory_gb"],
                "quantized_memory_gb": cfg["quantized_memory_gb"],
                "loaded": (self._model_name == name) and self._model_loaded,
            })
        return result

    # ─── LLMClient 集成 ────────────────────────────────────

    def is_available(self) -> bool:
        """供 LLMClient 检测本地模型客户端是否可用"""
        return self._model_loaded and self._model is not None

    def is_vl_available(self) -> bool:
        """检测当前是否为 VL 模型且可用"""
        return (
            self._model_loaded
            and self._model is not None
            and self._model_type == "vl"
            and self._processor is not None
        )

    def to_llm_compatible(self) -> Dict[str, Any]:
        """返回可被 LLMClient 使用的兼容接口描述

        LLMClient 可据此将本地模型包装为 OpenAI 兼容接口调用。
        """
        return {
            "type": "local_transformers",
            "available": self.is_available(),
            "vl_available": self.is_vl_available(),
            "model_name": self._model_name,
            "model_type": self._model_type,
            "quantization": self._quantization,
            "device": self._device,
            "endpoint": None,  # 无 HTTP 端点，直接进程内调用
            "methods": {
                "chat": "chat(prompt, system_prompt, temperature, max_tokens, context) -> Dict",
                "chat_stream": "chat_stream(prompt, ...) -> Generator[str]",
                "chat_with_image": "chat_with_image(prompt, image, ...) -> Dict (仅 VL 模型)",
                "switch_model_type": "switch_model_type(target_type, model_name, ...) -> Dict",
                "ensure_model_type": "ensure_model_type(target_type, ...) -> Dict",
                "unload": "unload_model() -> Dict",
            },
        }

    # ─── 内部辅助方法 ────────────────────────────────────

    def _auto_select_quantization(self, model_cfg: Dict[str, Any], device: str) -> str:
        """根据可用 GPU 显存自动选择量化方式"""
        if device == "cpu":
            # CPU 上 4bit 量化收益有限，但可减少内存占用
            return QUANTIZATION_4BIT if _BITSANDBYTES_AVAILABLE else QUANTIZATION_NONE

        free_gb = _get_gpu_memory_gb()
        if free_gb is None:
            # 无法获取显存，保守选择 4bit
            return QUANTIZATION_4BIT if _BITSANDBYTES_AVAILABLE else QUANTIZATION_NONE

        min_mem = model_cfg.get("min_memory_gb", 8)
        quant_mem = model_cfg.get("quantized_memory_gb", 5)

        if free_gb >= min_mem:
            return QUANTIZATION_NONE
        if free_gb >= quant_mem and _BITSANDBYTES_AVAILABLE:
            return QUANTIZATION_4BIT
        if _BITSANDBYTES_AVAILABLE:
            return QUANTIZATION_4BIT
        return QUANTIZATION_NONE

    def _build_load_kwargs(self, quantization: str, device: str) -> Dict[str, Any]:
        """构造 from_pretrained 加载参数"""
        kwargs: Dict[str, Any] = {
            "low_cpu_mem_usage": True,
        }

        if quantization == QUANTIZATION_4BIT and _BITSANDBYTES_AVAILABLE:
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16 if _TORCH_AVAILABLE else None,
                bnb_4bit_use_double_quant=True,
            )
        elif quantization == QUANTIZATION_8BIT and _BITSANDBYTES_AVAILABLE:
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        else:
            # 无量化，使用 bf16/fp16
            if _TORCH_AVAILABLE and device != "cpu":
                # 优先 bf16，回退 fp16
                try:
                    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
                        kwargs["torch_dtype"] = torch.bfloat16
                    else:
                        kwargs["torch_dtype"] = torch.float16
                except Exception:
                    kwargs["torch_dtype"] = torch.float16
            elif _TORCH_AVAILABLE:
                kwargs["torch_dtype"] = torch.float32

        # device_map='auto' 需要 accelerate
        if device != "cpu" and _ACCELERATE_AVAILABLE:
            kwargs["device_map"] = "auto"

        return kwargs

    def _build_prompt(self, user_input: str, system_prompt: str) -> str:
        """根据模型家族构造对话输入

        - Qwen: ChatML 格式
        - GLM: 特殊 token 格式
        - 通用: 使用 tokenizer.apply_chat_template（如果可用）
        """
        family = (self._model_config or {}).get("family", "qwen")

        # 优先使用 tokenizer 自带的 chat_template（最稳健）
        if self._tokenizer is not None and hasattr(self._tokenizer, "apply_chat_template"):
            try:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input},
                ]
                text = self._tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                if text:
                    return text
            except Exception as e:
                logger.debug(f"apply_chat_template 失败，回退到手动构造: {e}")

        # 手动构造（兜底）
        if family == "qwen":
            # ChatML 格式
            return (
                f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
                f"<|im_start|>user\n{user_input}<|im_end|>\n"
                f"<|im_start|>assistant\n"
            )
        elif family == "glm":
            # GLM 特殊 token 格式
            return (
                f"[gMASK]<sop><|system|>\n{system_prompt}\n"
                f"<|user|>\n{user_input}\n"
                f"<|assistant|>\n"
            )
        else:
            # 通用
            return f"System: {system_prompt}\nUser: {user_input}\nAssistant:"

    def _make_gen_config(self, temperature: float, max_tokens: int):
        """构造 GenerationConfig"""
        if not _TRANSFORMERS_AVAILABLE:
            return None
        try:
            cfg = GenerationConfig(
                max_new_tokens=max_tokens,
                do_sample=temperature > 0,
                temperature=max(temperature, 0.01),
                top_p=0.9,
                top_k=50,
                repetition_penalty=1.05,
                pad_token_id=self._tokenizer.pad_token_id if self._tokenizer else None,
                eos_token_id=self._tokenizer.eos_token_id if self._tokenizer else None,
                use_cache=True,
            )
            return cfg
        except Exception as e:
            logger.debug(f"构造 GenerationConfig 失败，使用简单参数: {e}")
            return None

    def _move_inputs_to_device(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """将 inputs 迁移到当前设备"""
        if not _TORCH_AVAILABLE or self._device == "cpu":
            return inputs
        try:
            target = torch.device(self._device)
            return {k: v.to(target) for k, v in inputs.items() if hasattr(v, "to")}
        except Exception as e:
            logger.debug(f"迁移 inputs 到 {self._device} 失败: {e}")
            return inputs

    def _warmup(self) -> None:
        """模型预热：加载后做一次空推理，避免首次推理延迟过高"""
        if not self._model_loaded or self._model is None or self._tokenizer is None:
            return
        try:
            logger.info("模型预热中...")
            start = time.time()
            inputs = self._tokenizer("hello", return_tensors="pt")
            inputs = self._move_inputs_to_device(inputs)
            with self._lock:
                _ = self._model.generate(**inputs, max_new_tokens=1)
            self._last_used_ts = time.time()
            logger.info(f"模型预热完成 (耗时 {round(time.time()-start, 2)}s)")
        except Exception as e:
            logger.warning(f"模型预热失败（不影响后续使用）: {e}")

    def _is_oom_error(self, err_msg: str) -> bool:
        """判断是否为显存/内存不足错误"""
        oom_keywords = (
            "out of memory", "CUDA out of memory", "OutOfMemoryError",
            "CUDA error", "MallocAsync", "HIP out of memory",
            "RuntimeError: CUDA", "MemoryError",
        )
        lower = err_msg.lower()
        return any(kw.lower() in lower for kw in oom_keywords)

    def _unavailable_reply(self, prompt: str) -> Dict[str, Any]:
        """模型未加载时的降级回复"""
        return {
            "content": "[本地模型未加载，请先调用 load_model()]",
            "model": self._model_name or "none",
            "tokens": 0,
            "latency": 0.0,
            "mode": "local_transformers",
            "platform": "local",
            "error": "model_not_loaded",
        }

    # ─── 配置持久化 ────────────────────────────────────

    def _load_config(self) -> None:
        """从配置文件恢复状态（仅恢复配置项，不自动加载模型）"""
        try:
            if not os.path.exists(self.config_path):
                return
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # 仅恢复非模型实例相关配置
            self.idle_timeout = cfg.get("idle_timeout", self.idle_timeout)
            self.auto_unload = cfg.get("auto_unload", self.auto_unload)
            # 记录上次加载的模型信息（不自动加载）
            logger.debug(
                f"已加载配置: last_model={cfg.get('model_name')}, "
                f"quantization={cfg.get('quantization')}, device={cfg.get('device')}"
            )
        except Exception as e:
            logger.debug(f"读取配置失败: {e}")

    def _save_config(self) -> None:
        """持久化配置到文件"""
        try:
            cfg = {
                "model_name": self._model_name,
                "quantization": self._quantization,
                "device": self._device,
                "idle_timeout": self.idle_timeout,
                "auto_unload": self.auto_unload,
                "saved_at": time.time(),
            }
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"保存配置失败: {e}")

    # ─── 闲置自动卸载 ────────────────────────────────────

    def _start_cleaner(self) -> None:
        """启动后台清理线程"""
        def _clean_loop():
            while not self._cleaner_stop.wait(60.0):  # 每 60s 检查一次
                try:
                    if not self._model_loaded or not self.auto_unload:
                        continue
                    idle = time.time() - self._last_used_ts
                    if self.idle_timeout > 0 and idle > self.idle_timeout:
                        logger.info(f"模型闲置 {round(idle, 0)}s 超过阈值 {self.idle_timeout}s，自动卸载")
                        self.unload_model()
                except Exception as e:
                    logger.debug(f"清理线程异常: {e}")

        self._cleaner_thread = threading.Thread(target=_clean_loop, daemon=True, name="local-model-cleaner")
        self._cleaner_thread.start()

    def shutdown(self) -> None:
        """关闭客户端（停止后台线程、卸载模型）"""
        self._cleaner_stop.set()
        if self._cleaner_thread and self._cleaner_thread.is_alive():
            self._cleaner_thread.join(timeout=2.0)
        if self._model_loaded:
            self.unload_model()
        logger.info("LocalModelClient 已关闭")


# ─── 全局单例 ────────────────────────────────────────────
_singleton: Optional[LocalModelClient] = None
_singleton_lock = threading.Lock()


def get_local_model() -> LocalModelClient:
    """获取 LocalModelClient 全局单例"""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = LocalModelClient()
    return _singleton


if __name__ == "__main__":
    # 简单自测
    logging.basicConfig(level=logging.INFO)
    client = get_local_model()
    print("=== 状态 ===")
    print(json.dumps(client.status(), ensure_ascii=False, indent=2))
    print("\n=== 支持的模型 ===")
    print(json.dumps(client.list_supported_models(), ensure_ascii=False, indent=2))
    print("\n=== LLM 兼容接口 ===")
    print(json.dumps(client.to_llm_compatible(), ensure_ascii=False, indent=2))
    print(f"\n=== 健康检查: {client.health_check()}")
    print(f"\n=== 是否可用: {client.is_available()}")
