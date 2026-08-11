# -*- coding: utf-8 -*-
"""
m_layer/voice_io.py — 语音输入输出模块（M层）
================================================
实现语音识别（STT）和语音合成（TTS）能力，对外提供统一入口。

能力对标：AI助手的"听懂语音、开口说话"能力

功能：
  1. VoiceIO 统一入口：recognize / synthesize / convert_format / recognize_stream
  2. SpeechRecognizer 语音识别（speech_recognition / Whisper / 离线命令识别）
  3. SpeechSynthesizer 语音合成（pyttsx3 / 系统 TTS / 简单蜂鸣音）
  4. 音频处理工具：record / play / adjust_volume / 噪声抑制
  5. 离线命令识别（降级方案，基于音频特征匹配预定义命令）
  6. 合成结果缓存到 scu2_data/voice_cache.json

降级策略：
  - speech_recognition 不可用 → 离线命令识别
  - pyttsx3 不可用          → 系统 TTS
  - 系统 TTS 不可用          → 返回空音频 + 文字
  - pyaudio 不可用（录音）   → 返回错误提示

架构归属：M层（认知层的语音通道）
依赖：可选 speech_recognition / whisper / pyttsx3 / pyaudio / pydub
"""
import os
import io
import json
import math
import wave
import threading
import time
import struct
import base64
import hashlib
import logging
import platform
import tempfile
import subprocess
from typing import Dict, Any, Optional, List, Tuple, Iterator

logger = logging.getLogger("scu2.m.voice")

# 项目根目录与数据目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "scu2_data")
CACHE_PATH = os.path.join(DATA_DIR, "voice_cache.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ─── 外部依赖可选导入 ────────────────────────────────────
try:
    import speech_recognition as sr
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False
    logger.debug("speech_recognition 不可用，STT 将降级到离线命令识别")

try:
    import pyttsx3
    _PYTTSX3_AVAILABLE = True
except ImportError:
    _PYTTSX3_AVAILABLE = False
    logger.debug("pyttsx3 不可用，TTS 将降级到系统 TTS")

try:
    import pyaudio
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False
    logger.debug("pyaudio 不可用，录音功能将受限")

try:
    from pydub import AudioSegment
    _PYDUB_AVAILABLE = True
except ImportError:
    _PYDUB_AVAILABLE = False
    logger.debug("pydub 不可用，音频格式转换将受限")

try:
    import whisper  # OpenAI Whisper 本地模型
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False
    logger.debug("whisper 不可用，跳过 Whisper STT 后端")

# 平台检测
_PLATFORM = platform.system().lower()  # 'windows' / 'darwin' / 'linux'

# WAV 默认参数
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_SAMPLE_WIDTH = 2  # 16-bit
DEFAULT_CHANNELS = 1


# =====================================================================
# 音频处理工具函数
# =====================================================================
def _write_wav(audio_bytes: bytes, sample_rate: int = DEFAULT_SAMPLE_RATE,
               sample_width: int = DEFAULT_SAMPLE_WIDTH,
               channels: int = DEFAULT_CHANNELS) -> bytes:
    """将裸 PCM 数据封装为 WAV 字节流"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_bytes)
    return buf.getvalue()


def _read_wav(audio_bytes: bytes) -> Dict[str, Any]:
    """解析 WAV 字节流，返回 PCM 与参数"""
    try:
        buf = io.BytesIO(audio_bytes)
        with wave.open(buf, "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        samples = _decode_pcm(raw, sample_width, n_channels)
        return {
            "samples": samples,
            "sample_rate": sample_rate,
            "sample_width": sample_width,
            "n_channels": n_channels,
            "n_frames": n_frames,
            "duration": n_frames / sample_rate if sample_rate else 0.0,
            "raw": raw,
        }
    except Exception as e:
        logger.warning(f"解析 WAV 失败: {e}")
        return {}


def _decode_pcm(raw: bytes, sample_width: int, n_channels: int) -> List[int]:
    """将 PCM 字节流解码为单声道样本列表"""
    if not raw:
        return []
    try:
        if sample_width == 2:
            fmt = "<" + "h" * (len(raw) // 2)
            samples = list(struct.unpack(fmt, raw))
        elif sample_width == 1:
            samples = [b - 128 for b in raw]
        elif sample_width == 4:
            fmt = "<" + "i" * (len(raw) // 4)
            samples = list(struct.unpack(fmt, raw))
        else:
            return []
        if n_channels > 1 and samples:
            samples = samples[0::n_channels]
        return samples
    except Exception:
        return []


def _encode_samples(samples: List[int], sample_width: int) -> bytes:
    """将样本列表编码为 PCM 字节流（带裁剪防溢出）"""
    if not samples:
        return b""
    max_val = (1 << (sample_width * 8 - 1)) - 1
    min_val = -(1 << (sample_width * 8 - 1))
    clamped = [max(min_val, min(max_val, int(s))) for s in samples]
    try:
        if sample_width == 2:
            return struct.pack("<" + "h" * len(clamped), *clamped)
        if sample_width == 1:
            return bytes((v + 128) & 0xFF for v in clamped)
        if sample_width == 4:
            return struct.pack("<" + "i" * len(clamped), *clamped)
    except Exception as e:
        logger.warning(f"样本编码失败: {e}")
    return b""


# =====================================================================
# 离线命令识别（降级方案）
# =====================================================================
# 预定义命令模板（关键词 + 特征签名）
# 特征签名: (典型时长秒, 典型平均音量, 典型过零率)
COMMAND_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "打开": {
        "keywords": ["打开", "open", "start"],
        "duration": (0.4, 0.9),
        "volume": (1500, 4000),
        "zcr": (0.05, 0.15),
        "label_en": "open",
    },
    "关闭": {
        "keywords": ["关闭", "close", "stop", "shutdown"],
        "duration": (0.5, 1.1),
        "volume": (1500, 4000),
        "zcr": (0.08, 0.20),
        "label_en": "close",
    },
    "查询": {
        "keywords": ["查询", "查", "query", "search", "look"],
        "duration": (0.6, 1.3),
        "volume": (1800, 4500),
        "zcr": (0.10, 0.22),
        "label_en": "query",
    },
    "执行": {
        "keywords": ["执行", "运行", "run", "execute", "do"],
        "duration": (0.5, 1.0),
        "volume": (2000, 5000),
        "zcr": (0.12, 0.25),
        "label_en": "execute",
    },
    "确认": {
        "keywords": ["确认", "是", "yes", "ok", "confirm"],
        "duration": (0.3, 0.7),
        "volume": (1200, 3500),
        "zcr": (0.04, 0.12),
        "label_en": "confirm",
    },
    "取消": {
        "keywords": ["取消", "否", "no", "cancel"],
        "duration": (0.4, 0.9),
        "volume": (1200, 3500),
        "zcr": (0.06, 0.16),
        "label_en": "cancel",
    },
}


class OfflineCommandRecognizer:
    """离线命令识别器（降级方案）

    基于音频特征（时长、音量、过零率）的简单命令匹配，
    无法识别具体语音内容，只能粗略分类到预定义命令。

    用法:
        ocr = OfflineCommandRecognizer()
        result = ocr.recognize(wav_bytes, language="zh")
        # result = {"command": "打开", "text": "打开", "confidence": 0.6, ...}
    """

    def __init__(self, templates: Optional[Dict[str, Dict[str, Any]]] = None):
        self.templates = templates or COMMAND_TEMPLATES

    def recognize(self, wav_bytes: bytes,
                  language: str = "zh") -> Dict[str, Any]:
        """识别命令

        Args:
            wav_bytes: WAV 格式音频字节流
            language: 语言（zh / en）

        Returns:
            {
                "text": str,           # 命令标签
                "confidence": float,   # 0-1
                "backend": "offline",
                "command": Optional[str],
                "language": str,
                "features": {...},
            }
        """
        result: Dict[str, Any] = {
            "text": "",
            "confidence": 0.0,
            "backend": "offline",
            "command": None,
            "language": language,
        }
        info = _read_wav(wav_bytes)
        if not info:
            result["error"] = "音频解析失败"
            return result

        features = self._extract_features(info)
        result["features"] = features

        # 若音频过短或无声，直接返回
        if features["duration"] < 0.2 or features["mean_volume"] < 100:
            result["error"] = "音频过短或无声"
            return result

        # 匹配模板
        best_cmd, best_score = self._match(features)
        if best_cmd is None:
            result["error"] = "未匹配到任何命令"
            return result

        confidence = min(0.9, max(0.3, best_score))
        result["command"] = best_cmd
        # 文本用命令关键词的本地化标签
        tpl = self.templates[best_cmd]
        label = best_cmd if language.startswith("zh") else tpl.get("label_en", best_cmd)
        result["text"] = label
        result["confidence"] = round(confidence, 3)
        return result

    def _extract_features(self, info: Dict[str, Any]) -> Dict[str, Any]:
        """提取音频特征：时长、平均/最大音量、过零率"""
        samples = info.get("samples", [])
        duration = info.get("duration", 0.0)
        features: Dict[str, Any] = {
            "duration": round(duration, 3),
            "mean_volume": 0.0,
            "max_volume": 0,
            "zcr": 0.0,  # 过零率（频率特征近似）
        }
        if not samples:
            return features
        try:
            abs_s = [abs(s) for s in samples]
            features["mean_volume"] = sum(abs_s) / len(abs_s)
            features["max_volume"] = max(abs_s)
            # 过零率（粗略反映频率特征）
            zc = 0
            for i in range(1, len(samples)):
                if (samples[i - 1] >= 0) != (samples[i] >= 0):
                    zc += 1
            features["zcr"] = zc / len(samples) if samples else 0.0
        except Exception as e:
            logger.debug(f"特征提取异常: {e}")
        return features

    def _match(self, features: Dict[str, Any]) -> Tuple[Optional[str], float]:
        """匹配最相似的命令模板，返回 (命令, 综合得分)"""
        best_cmd: Optional[str] = None
        best_score = 0.0
        for cmd, tpl in self.templates.items():
            score = self._score_template(features, tpl)
            if score > best_score:
                best_score = score
                best_cmd = cmd
        return best_cmd, best_score

    @staticmethod
    def _score_template(features: Dict[str, Any],
                        tpl: Dict[str, Any]) -> float:
        """计算特征与模板的匹配得分（0-1）

        三项特征各占权重：时长 0.4 / 音量 0.3 / 过零率 0.3
        区间内满分，偏离按比例衰减。
        """
        score = 0.0

        # 时长匹配
        d_lo, d_hi = tpl["duration"]
        dur = features["duration"]
        if d_lo <= dur <= d_hi:
            score += 0.4
        else:
            mid = (d_lo + d_hi) / 2
            span = max(0.1, (d_hi - d_lo) / 2)
            dev = abs(dur - mid) / span
            score += max(0.0, 0.4 - dev * 0.2)

        # 音量匹配
        v_lo, v_hi = tpl["volume"]
        vol = features["mean_volume"]
        if v_lo <= vol <= v_hi:
            score += 0.3
        else:
            mid = (v_lo + v_hi) / 2
            span = max(100.0, (v_hi - v_lo) / 2)
            dev = abs(vol - mid) / span
            score += max(0.0, 0.3 - dev * 0.15)

        # 过零率匹配
        z_lo, z_hi = tpl["zcr"]
        zcr = features["zcr"]
        if z_lo <= zcr <= z_hi:
            score += 0.3
        else:
            mid = (z_lo + z_hi) / 2
            span = max(0.01, (z_hi - z_lo) / 2)
            dev = abs(zcr - mid) / span
            score += max(0.0, 0.3 - dev * 0.15)

        return score


# =====================================================================
# 语音识别
# =====================================================================
class SpeechRecognizer:
    """语音识别模块

    支持后端（按优先级自动选择）：
      1. Whisper 本地模型（若可用）
      2. speech_recognition + Google Web Speech API（若可用）
      3. 离线命令识别（基于音频特征，降级方案）

    支持语言：中文（zh）、英文（en）

    用法:
        rec = SpeechRecognizer()
        result = rec.recognize(wav_bytes, language="zh")
        # result = {"text": "...", "confidence": 0.85, "backend": "google", ...}
    """

    def __init__(self, backend: Optional[str] = None,
                 whisper_model: str = "base"):
        self._backend = backend  # None 表示自动选择
        self._whisper_model_name = whisper_model
        self._whisper_model = None
        self._sr = sr.Recognizer() if _SR_AVAILABLE else None
        self._command_recognizer = OfflineCommandRecognizer()
        self._active_backend = self._select_backend()
        logger.info(f"语音识别后端: {self._active_backend}")

    def _select_backend(self) -> str:
        """自动选择可用后端"""
        if self._backend:
            return self._backend
        if _WHISPER_AVAILABLE:
            return "whisper"
        if _SR_AVAILABLE:
            return "google"
        return "offline"

    def recognize(self, audio_data: bytes, format: str = "wav",
                  language: str = "zh") -> Dict[str, Any]:
        """语音转文字

        Args:
            audio_data: 音频字节流
            format: 音频格式（wav / pcm / mp3 / m4a / ogg）
            language: 语言（zh / en）

        Returns:
            {
                "text": str,
                "confidence": float,  # 0-1
                "backend": str,
                "language": str,
            }
        """
        result: Dict[str, Any] = {
            "text": "",
            "confidence": 0.0,
            "backend": self._active_backend,
            "language": language,
        }
        if not audio_data:
            result["error"] = "音频数据为空"
            return result

        # 统一为 WAV 字节流
        wav_bytes = self._ensure_wav(audio_data, format)

        try:
            if self._active_backend == "whisper":
                rec = self._recognize_with_whisper(wav_bytes, language)
            elif self._active_backend == "google":
                rec = self._recognize_with_google(wav_bytes, language)
            else:
                rec = self._recognize_offline(wav_bytes, language)
            result.update(rec)
        except Exception as e:
            logger.warning(f"识别失败 ({self._active_backend}): {e}")
            result["error"] = str(e)
            # 尝试降级到离线
            if self._active_backend != "offline":
                logger.info("降级到离线命令识别")
                try:
                    rec = self._recognize_offline(wav_bytes, language)
                    result.update(rec)
                    result["backend"] = "offline"
                    result["degraded"] = True
                except Exception as e2:
                    result["error"] = f"{e}; 离线降级也失败: {e2}"
        return result

    def _ensure_wav(self, audio_data: bytes, format: str) -> bytes:
        """确保音频为 WAV 字节流"""
        fmt = format.lower()
        if fmt == "wav":
            return audio_data
        if fmt == "pcm":
            return _write_wav(audio_data)
        # mp3/m4a/ogg 等格式通过 pydub 转换
        if _PYDUB_AVAILABLE and fmt in ("mp3", "m4a", "ogg"):
            try:
                seg = AudioSegment.from_file(io.BytesIO(audio_data), format=fmt)
                buf = io.BytesIO()
                seg.export(buf, format="wav")
                return buf.getvalue()
            except Exception as e:
                logger.warning(f"音频格式转换失败 ({fmt}): {e}")
                return audio_data
        return audio_data

    def _recognize_with_whisper(self, wav_bytes: bytes,
                                language: str) -> Dict[str, Any]:
        """使用 Whisper 本地模型识别"""
        if not _WHISPER_AVAILABLE:
            return {"error": "whisper 不可用"}
        if self._whisper_model is None:
            logger.info(f"加载 Whisper 模型: {self._whisper_model_name}")
            self._whisper_model = whisper.load_model(self._whisper_model_name)
        # 写入临时文件供 Whisper 读取
        tmp_path = self._write_tmp_wav(wav_bytes)
        try:
            lang_code = "zh" if language.startswith("zh") else "en"
            result = self._whisper_model.transcribe(tmp_path, language=lang_code)
            text = result.get("text", "").strip()
            # Whisper 不直接给置信度，用 segments 平均 logprob 近似
            segments = result.get("segments", []) or []
            confs = [s.get("avg_logprob", 0) for s in segments if s]
            confidence = 0.0
            if confs:
                confidence = max(0.0, min(1.0, math.exp(sum(confs) / len(confs))))
            return {"text": text, "confidence": round(confidence, 3)}
        finally:
            self._cleanup_tmp(tmp_path)

    def _recognize_with_google(self, wav_bytes: bytes,
                               language: str) -> Dict[str, Any]:
        """使用 Google Web Speech API 识别"""
        if not _SR_AVAILABLE:
            return {"error": "speech_recognition 不可用"}
        try:
            buf = io.BytesIO(wav_bytes)
            with sr.AudioFile(buf) as src:
                audio = self._sr.record(src)
            lang_code = "zh-CN" if language.startswith("zh") else "en-US"
            text = self._sr.recognize_google(audio, language=lang_code,
                                             show_all=False)
            # Google 不返回置信度，给一个保守值
            return {"text": text, "confidence": 0.85}
        except sr.UnknownValueError:
            return {"text": "", "confidence": 0.0, "error": "无法识别语音内容"}
        except sr.RequestError as e:
            return {"text": "", "confidence": 0.0,
                    "error": f"STT 服务请求失败: {e}"}

    def _recognize_offline(self, wav_bytes: bytes,
                           language: str) -> Dict[str, Any]:
        """离线命令识别（降级方案）"""
        return self._command_recognizer.recognize(wav_bytes, language)

    def recognize_stream(self, audio_stream: Iterator[bytes],
                         language: str = "zh",
                         chunk_seconds: float = 2.0) -> Iterator[Dict[str, Any]]:
        """流式识别：逐块返回识别结果

        Args:
            audio_stream: 音频块迭代器（每块为 WAV 或 PCM 字节流）
            language: 语言
            chunk_seconds: 每块累积时长（用于决定何时触发识别）

        Yields:
            每个累积块的识别结果
        """
        buffer = b""
        chunk_bytes = int(DEFAULT_SAMPLE_RATE * DEFAULT_SAMPLE_WIDTH *
                          DEFAULT_CHANNELS * chunk_seconds)
        for chunk in audio_stream:
            buffer += chunk
            if len(buffer) >= chunk_bytes:
                wav_chunk = self._ensure_wav(buffer, "pcm")
                yield self.recognize(wav_chunk, format="wav", language=language)
                buffer = b""
        if buffer:
            wav_chunk = self._ensure_wav(buffer, "pcm")
            yield self.recognize(wav_chunk, format="wav", language=language)

    @staticmethod
    def _write_tmp_wav(wav_bytes: bytes) -> str:
        """写入临时 WAV 文件，返回路径"""
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="voice_io_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav_bytes)
        except Exception:
            os.close(fd)
            raise
        return path

    @staticmethod
    def _cleanup_tmp(path: str) -> None:
        """清理临时文件"""
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


# =====================================================================
# 语音合成
# =====================================================================
class SpeechSynthesizer:
    """语音合成模块

    支持后端（按优先级自动选择）：
      1. pyttsx3（离线 TTS，跨平台）
      2. 系统 TTS（Windows SAPI / macOS say / Linux espeak）
      3. 简单蜂鸣音（降级方案，无法 TTS 时）

    支持语言：中文（zh）、英文（en）
    输出格式：WAV

    用法:
        synth = SpeechSynthesizer()
        wav_bytes = synth.synthesize("你好", lang="zh")
    """

    def __init__(self, backend: Optional[str] = None):
        self._backend = backend
        self._engine = None  # pyttsx3 引擎
        self._active_backend = self._select_backend()
        logger.info(f"语音合成后端: {self._active_backend}")

    def _select_backend(self) -> str:
        """自动选择可用后端"""
        if self._backend:
            return self._backend
        if _PYTTSX3_AVAILABLE and self._init_pyttsx3():
            return "pyttsx3"
        if self._system_tts_available():
            return "system"
        return "beep"

    def _init_pyttsx3(self) -> bool:
        """初始化 pyttsx3 引擎"""
        try:
            self._engine = pyttsx3.init()
            return True
        except Exception as e:
            logger.debug(f"pyttsx3 初始化失败: {e}")
            self._engine = None
            return False

    @staticmethod
    def _system_tts_available() -> bool:
        """检测系统 TTS 是否可用"""
        try:
            if _PLATFORM == "windows":
                # Windows SAPI 通过 PowerShell 调用
                return True
            if _PLATFORM == "darwin":
                return subprocess.run(
                    ["which", "say"], capture_output=True
                ).returncode == 0
            if _PLATFORM == "linux":
                return subprocess.run(
                    ["which", "espeak"], capture_output=True
                ).returncode == 0
        except Exception:
            pass
        return False

    def synthesize(self, text: str, lang: str = "zh",
                   rate: int = 150, pitch: int = 50,
                   volume: float = 1.0) -> bytes:
        """文字转语音

        Args:
            text: 待合成文本
            lang: 语言（zh / en）
            rate: 语速（字/分钟，pyttsx3 用）
            pitch: 音调（0-100，pyttsx3 用）
            volume: 音量（0.0-1.0）

        Returns:
            WAV 格式音频字节流
        """
        if not text:
            return b""
        try:
            if self._active_backend == "pyttsx3":
                wav = self._synth_pyttsx3(text, lang, rate, pitch, volume)
                if wav:
                    return wav
                # 降级到系统 TTS
                logger.warning("pyttsx3 合成失败，降级到系统 TTS")
                wav = self._synth_system(text, lang)
                if wav:
                    return wav
            elif self._active_backend == "system":
                wav = self._synth_system(text, lang)
                if wav:
                    return wav
            # 最终降级：蜂鸣音
            logger.warning("TTS 不可用，降级为蜂鸣音")
            return self._synth_beep(text, volume)
        except Exception as e:
            logger.warning(f"语音合成失败: {e}")
            return self._synth_beep(text, volume)

    def _synth_pyttsx3(self, text: str, lang: str, rate: int,
                       pitch: int, volume: float) -> bytes:
        """使用 pyttsx3 合成"""
        if not self._engine:
            if not self._init_pyttsx3():
                return b""
        tmp_path = tempfile.mktemp(suffix=".wav", prefix="tts_")
        try:
            self._engine.setProperty("rate", rate)
            self._engine.setProperty("volume", max(0.0, min(1.0, volume)))
            # pyttsx3 的 pitch 支持有限，仅在部分驱动生效
            try:
                self._engine.setProperty("pitch", pitch)
            except Exception:
                pass
            # 选择符合语言的语音
            self._select_voice(lang)
            self._engine.save_to_file(text, tmp_path)
            self._engine.runAndWait()
            if not os.path.exists(tmp_path):
                return b""
            with open(tmp_path, "rb") as f:
                wav = f.read()
            return wav
        except Exception as e:
            logger.warning(f"pyttsx3 合成异常: {e}")
            return b""
        finally:
            self._cleanup_tmp(tmp_path)

    def _select_voice(self, lang: str) -> None:
        """选择符合语言的语音"""
        try:
            voices = self._engine.getProperty("voices")
            if not voices:
                return
            target = "chinese" if lang.startswith("zh") else "english"
            for v in voices:
                name = (v.name or "").lower()
                if target in name or lang[:2] in name:
                    self._engine.setProperty("voice", v.id)
                    return
            # 找不到匹配，使用默认
        except Exception as e:
            logger.debug(f"选择语音失败: {e}")

    def _synth_system(self, text: str, lang: str) -> bytes:
        """使用系统 TTS 合成"""
        tmp_path = tempfile.mktemp(suffix=".wav", prefix="tts_sys_")
        try:
            if _PLATFORM == "windows":
                self._synth_windows_sapi(text, lang, tmp_path)
            elif _PLATFORM == "darwin":
                # macOS say 命令直接输出 aiff，转 wav 用 afconvert
                aiff_path = tmp_path + ".aiff"
                subprocess.run(
                    ["say", "-o", aiff_path, text],
                    check=True, capture_output=True
                )
                subprocess.run(
                    ["afconvert", "-f", "WAVE", "-d", "LEI16@16000",
                     aiff_path, tmp_path],
                    check=True, capture_output=True
                )
                try:
                    os.remove(aiff_path)
                except OSError:
                    pass
            elif _PLATFORM == "linux":
                # espeak 直接输出 wav
                lang_code = "zh" if lang.startswith("zh") else "en"
                subprocess.run(
                    ["espeak", "-v", lang_code, "-w", tmp_path, text],
                    check=True, capture_output=True
                )
            else:
                return b""

            if os.path.exists(tmp_path):
                with open(tmp_path, "rb") as f:
                    return f.read()
            return b""
        except Exception as e:
            logger.warning(f"系统 TTS 合成失败: {e}")
            return b""
        finally:
            self._cleanup_tmp(tmp_path)

    @staticmethod
    def _synth_windows_sapi(text: str, lang: str, out_path: str) -> None:
        """Windows SAPI 合成（通过 PowerShell 调用 SAPI.SpVoice）"""
        # 转义单引号
        safe_text = text.replace("'", "''")
        # 路径中的反斜杠在 PowerShell 单引号字符串中是字面量，无需转义
        ps_script = (
            "$sapi = New-Object -ComObject SAPI.SpVoice; "
            "$fs = New-Object -ComObject SAPI.SpFileStream; "
            "$fs.Open('" + out_path + "', 3, 0); "  # SSFMCreateForWrite=3
            "$sapi.AudioOutputStream = $fs; "
            "$sapi.Speak('" + safe_text + "', 0); "
            "$fs.Close()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            check=True, capture_output=True
        )

    def _synth_beep(self, text: str, volume: float = 1.0) -> bytes:
        """降级方案：生成蜂鸣音（按字数生成有节奏的短音）

        无法合成真实语音，仅产生节奏化的提示音。
        """
        try:
            sr_rate = DEFAULT_SAMPLE_RATE
            n_chars = max(1, len(text))
            # 每字约 0.15s，最多 3s
            duration = min(3.0, 0.15 * n_chars + 0.2)
            n_samples = int(sr_rate * duration)
            amp = int(8000 * max(0.1, min(1.0, volume)))
            samples: List[int] = []
            for i in range(n_samples):
                t = i / sr_rate
                # 每 0.15s 切换一次频率，模拟"说话节奏"
                beat = int(t / 0.15) % 2
                freq = 660 if beat == 0 else 880
                val = int(amp * 0.6 *
                          math.sin(2 * math.pi * freq * t))
                samples.append(val)
            raw = _encode_samples(samples, sample_width=2)
            return _write_wav(raw, sample_rate=sr_rate,
                              sample_width=2, channels=1)
        except Exception as e:
            logger.warning(f"蜂鸣音生成失败: {e}")
            return b""

    @staticmethod
    def _cleanup_tmp(path: str) -> None:
        """清理临时文件"""
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


# =====================================================================
# 音频处理工具
# =====================================================================
class AudioTools:
    """音频处理工具集

    提供：
      - 录音 record(duration, sample_rate)
      - 播放 play(audio_bytes)
      - 音量调节 adjust_volume(audio_data, factor)
      - 噪声抑制 suppress_noise(audio_data, threshold)
    """

    @staticmethod
    def record(duration: float = 3.0,
               sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
        """录音指定时长

        Args:
            duration: 录音时长（秒）
            sample_rate: 采样率

        Returns:
            WAV 格式音频字节流；失败返回空字节并记录错误
        """
        if not _PYAUDIO_AVAILABLE:
            logger.error("pyaudio 不可用，无法录音")
            return b""
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=DEFAULT_CHANNELS,
                rate=sample_rate,
                input=True,
                frames_per_buffer=1024,
            )
            logger.info(f"开始录音 {duration}s @ {sample_rate}Hz")
            frames: List[bytes] = []
            n_frames = int(sample_rate / 1024 * duration)
            for _ in range(n_frames):
                data = stream.read(1024, exception_on_overflow=False)
                frames.append(data)
            stream.stop_stream()
            stream.close()
            pa.terminate()
            raw = b"".join(frames)
            return _write_wav(raw, sample_rate=sample_rate,
                              sample_width=2, channels=1)
        except Exception as e:
            logger.warning(f"录音失败: {e}")
            return b""

    @staticmethod
    def play(audio_bytes: bytes) -> bool:
        """播放 WAV 音频字节流

        Args:
            audio_bytes: WAV 格式音频

        Returns:
            是否成功
        """
        if not audio_bytes:
            return False
        try:
            if _PLATFORM == "windows":
                return AudioTools._play_windows(audio_bytes)
            # macOS / Linux: 写临时文件用系统命令播放
            tmp_path = tempfile.mktemp(suffix=".wav", prefix="play_")
            with open(tmp_path, "wb") as f:
                f.write(audio_bytes)
            try:
                if _PLATFORM == "darwin":
                    subprocess.run(["afplay", tmp_path], check=True)
                elif _PLATFORM == "linux":
                    subprocess.run(["aplay", "-q", tmp_path], check=True)
                else:
                    logger.warning(f"不支持的平台播放: {_PLATFORM}")
                    return False
                return True
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"播放失败: {e}")
            return False

    @staticmethod
    def _play_windows(audio_bytes: bytes) -> bool:
        """Windows 平台播放（winsound）"""
        try:
            import winsound  # Windows 内置
            tmp_path = tempfile.mktemp(suffix=".wav", prefix="play_")
            with open(tmp_path, "wb") as f:
                f.write(audio_bytes)
            try:
                winsound.PlaySound(tmp_path, winsound.SND_FILENAME)
                return True
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"Windows 播放失败: {e}")
            return False

    @staticmethod
    def adjust_volume(audio_data: bytes, factor: float = 1.0) -> bytes:
        """音量调节

        Args:
            audio_data: WAV 字节流
            factor: 音量倍数（0.0 静音，1.0 原音量，2.0 两倍）

        Returns:
            调节后的 WAV 字节流
        """
        if not audio_data:
            return b""
        info = _read_wav(audio_data)
        if not info:
            # 可能是裸 PCM，原样返回
            return audio_data
        try:
            samples = info["samples"]
            sample_width = info["sample_width"]
            sample_rate = info["sample_rate"]
            channels = info["n_channels"]
            adjusted = [s * factor for s in samples]
            raw = _encode_samples(adjusted, sample_width=sample_width)
            return _write_wav(raw, sample_rate=sample_rate,
                              sample_width=sample_width, channels=channels)
        except Exception as e:
            logger.warning(f"音量调节失败: {e}")
            return audio_data

    @staticmethod
    def suppress_noise(audio_data: bytes,
                       threshold: int = 300) -> bytes:
        """简单噪声抑制（门限滤波）

        对幅值低于阈值的样本置零，抑制背景噪声。

        Args:
            audio_data: WAV 字节流
            threshold: 噪声门限（样本绝对值）

        Returns:
            滤波后的 WAV 字节流
        """
        if not audio_data:
            return b""
        info = _read_wav(audio_data)
        if not info:
            return audio_data
        try:
            samples = info["samples"]
            sample_width = info["sample_width"]
            sample_rate = info["sample_rate"]
            channels = info["n_channels"]
            filtered = [s if abs(s) >= threshold else 0 for s in samples]
            raw = _encode_samples(filtered, sample_width=sample_width)
            return _write_wav(raw, sample_rate=sample_rate,
                              sample_width=sample_width, channels=channels)
        except Exception as e:
            logger.warning(f"噪声抑制失败: {e}")
            return audio_data


# =====================================================================
# VoiceIO 统一入口
# =====================================================================
class VoiceIO:
    """语音输入输出统一入口

    用法:
        vio = get_voice_io()
        text = vio.recognize(audio_bytes, format="wav")
        audio = vio.synthesize("你好", lang="zh")
        vio.play(audio)
        wav = vio.record(duration=3.0)
    """

    def __init__(self):
        self.recognizer = SpeechRecognizer()
        self.synthesizer = SpeechSynthesizer()
        self.tools = AudioTools()
        self._cache: Dict[str, Any] = {}
        self._load_cache()

    # ── 缓存 ────────────────────────────────────────
    def _load_cache(self) -> None:
        """加载缓存"""
        try:
            if os.path.exists(CACHE_PATH):
                with open(CACHE_PATH, "r", encoding="utf-8") as f:
                    self._cache = json.load(f) or {}
        except Exception as e:
            logger.warning(f"加载语音缓存失败: {e}")
            self._cache = {}

    def _save_cache(self) -> None:
        """保存缓存"""
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存语音缓存失败: {e}")

    def _synth_cache_key(self, text: str, lang: str,
                         rate: int, pitch: int, volume: float) -> str:
        """合成结果缓存键"""
        raw = f"tts|{lang}|{rate}|{pitch}|{volume}|{text}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()
        self._save_cache()
        logger.info("语音缓存已清空")

    # ── 语音识别 ────────────────────────────────────
    def recognize(self, audio_data: bytes, format: str = "wav",
                  language: str = "zh") -> str:
        """语音转文字

        Args:
            audio_data: 音频字节流
            format: 音频格式（wav / pcm / mp3）
            language: 语言（zh / en）

        Returns:
            识别出的文字
        """
        result = self.recognizer.recognize(audio_data, format=format,
                                           language=language)
        return result.get("text", "")

    def recognize_detail(self, audio_data: bytes, format: str = "wav",
                         language: str = "zh") -> Dict[str, Any]:
        """语音转文字（带详细信息：置信度、后端等）"""
        return self.recognizer.recognize(audio_data, format=format,
                                         language=language)

    def recognize_stream(self, audio_stream: Iterator[bytes],
                         language: str = "zh") -> Iterator[Dict[str, Any]]:
        """流式识别：逐块返回识别结果"""
        yield from self.recognizer.recognize_stream(audio_stream,
                                                    language=language)

    # ── 语音合成 ────────────────────────────────────
    def synthesize(self, text: str, lang: str = "zh",
                   rate: int = 150, pitch: int = 50,
                   volume: float = 1.0) -> bytes:
        """文字转语音

        Args:
            text: 待合成文本
            lang: 语言（zh / en）
            rate: 语速
            pitch: 音调（0-100）
            volume: 音量（0.0-1.0）

        Returns:
            WAV 格式音频字节流
        """
        if not text:
            return b""
        # 缓存命中
        cache_key = self._synth_cache_key(text, lang, rate, pitch, volume)
        cached = self._cache.get(cache_key)
        if cached and isinstance(cached, str):
            try:
                logger.debug(f"合成缓存命中: {cache_key}")
                return base64.b64decode(cached)
            except Exception:
                pass
        wav = self.synthesizer.synthesize(text, lang=lang, rate=rate,
                                          pitch=pitch, volume=volume)
        if wav:
            try:
                self._cache[cache_key] = base64.b64encode(wav).decode("ascii")
                # 限制缓存大小（防止膨胀）
                if len(self._cache) > 200:
                    keys = list(self._cache.keys())
                    for k in keys[:len(keys) // 4]:
                        self._cache.pop(k, None)
                self._save_cache()
            except Exception as e:
                logger.debug(f"缓存合成结果失败: {e}")
        return wav

    # ── 音频格式转换 ────────────────────────────────
    def convert_format(self, audio_data: bytes, from_fmt: str,
                       to_fmt: str) -> bytes:
        """音频格式转换

        Args:
            audio_data: 原始音频字节流
            from_fmt: 源格式（wav / pcm / mp3 / m4a / ogg）
            to_fmt: 目标格式（wav / pcm / mp3 / m4a / ogg）

        Returns:
            转换后的音频字节流
        """
        if from_fmt == to_fmt:
            return audio_data
        try:
            # 先统一转为 WAV
            src = from_fmt.lower()
            dst = to_fmt.lower()
            if src == "wav":
                wav_bytes = audio_data
            elif src == "pcm":
                wav_bytes = _write_wav(audio_data)
            elif _PYDUB_AVAILABLE and src in ("mp3", "m4a", "ogg"):
                seg = AudioSegment.from_file(io.BytesIO(audio_data),
                                             format=src)
                buf = io.BytesIO()
                seg.export(buf, format="wav")
                wav_bytes = buf.getvalue()
            else:
                logger.warning(f"不支持的源格式: {from_fmt}")
                return audio_data

            if dst == "wav":
                return wav_bytes
            if dst == "pcm":
                info = _read_wav(wav_bytes)
                return info.get("raw", b"")
            if _PYDUB_AVAILABLE and dst in ("mp3", "m4a", "ogg"):
                seg = AudioSegment.from_file(io.BytesIO(wav_bytes),
                                             format="wav")
                buf = io.BytesIO()
                seg.export(buf, format=dst)
                return buf.getvalue()
            logger.warning(f"不支持的目标格式: {to_fmt}")
            return wav_bytes
        except Exception as e:
            logger.warning(f"音频格式转换失败 ({from_fmt}->{to_fmt}): {e}")
            return audio_data

    # ── 录音 ────────────────────────────────────────
    def record(self, duration: float = 3.0,
               sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
        """录音指定时长，返回 WAV 字节流"""
        return self.tools.record(duration=duration,
                                 sample_rate=sample_rate)

    # ── 播放 ────────────────────────────────────────
    def play(self, audio_bytes: bytes) -> bool:
        """播放音频"""
        return self.tools.play(audio_bytes)

    # ── 音量调节 ────────────────────────────────────
    def adjust_volume(self, audio_data: bytes,
                      factor: float = 1.0) -> bytes:
        """音量调节"""
        return self.tools.adjust_volume(audio_data, factor=factor)

    # ── 噪声抑制 ────────────────────────────────────
    def suppress_noise(self, audio_data: bytes,
                       threshold: int = 300) -> bytes:
        """噪声抑制（门限滤波）"""
        return self.tools.suppress_noise(audio_data, threshold=threshold)

    # ── 状态信息 ────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        """返回当前可用后端与依赖状态"""
        return {
            "recognizer_backend": self.recognizer._active_backend,
            "synthesizer_backend": self.synthesizer._active_backend,
            "dependencies": {
                "speech_recognition": _SR_AVAILABLE,
                "pyttsx3": _PYTTSX3_AVAILABLE,
                "pyaudio": _PYAUDIO_AVAILABLE,
                "pydub": _PYDUB_AVAILABLE,
                "whisper": _WHISPER_AVAILABLE,
            },
            "platform": _PLATFORM,
            "cache_size": len(self._cache),
        }


# =====================================================================
# 实时持续监听（VAD + 后台线程 + 唤醒词）
# =====================================================================

# webrtcvad 可选导入（C 扩展，无则降级到能量阈值 VAD)
_WEBRTC_VAD_AVAILABLE = False
try:
    import webrtcvad
    _WEBRTC_VAD_AVAILABLE = True
except ImportError:
    logger.debug("webrtcvad 不可用，持续监听将使用能量阈值 VAD")


class ContinuousListener:
    """实时持续语音监听器

    在后台线程持续采集麦克风音频，使用 VAD 检测语音段，
    检测到语音后自动识别并触发回调。

    支持两种模式：
      1. 直通模式：检测到任何语音段即识别并回调
      2. 唤醒词模式：先识别唤醒词（如"嘿 SCU2"），命中后再识别命令

    用法：
        listener = ContinuousListener()
        listener.on_utterance = my_callback  # callback(text: str)
        listener.start(wake_word="嘿 SCU2")
        # ... 持续监听中 ...
        listener.stop()

    VAD 策略：
      - webrtcvad 可用：使用 WebRTC VAD（更精准）
      - 不可用：能量阈值 VAD（纯 Python，基于 RMS）
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_duration_ms: int = 30,
        silence_duration_ms: int = 700,
        max_utterance_seconds: float = 10.0,
    ):
        """初始化监听器

        Args:
            sample_rate: 采样率（Hz）
            frame_duration_ms: VAD 帧长（毫秒，webrtcvad 支持 10/20/30）
            silence_duration_ms: 静音时长阈值，超过则认为一句话结束
            max_utterance_seconds: 单次发言最大时长，防止无限录音
        """
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * frame_duration_ms / 1000) * DEFAULT_SAMPLE_WIDTH
        self.silence_duration_ms = silence_duration_ms
        self.max_utterance_seconds = max_utterance_seconds

        # 回调
        self.on_utterance = None  # Callable[[str], None]
        self.on_wake_word = None  # Callable[[], None]
        self.on_state_change = None  # Callable[[str], None]  idle/listening/speaking

        # 状态
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._wake_word: Optional[str] = None
        self._wake_word_active = False  # 唤醒词模式下，是否已检测到唤醒词
        self._language = "zh"

        # VAD
        self._vad = None
        if _WEBRTC_VAD_AVAILABLE:
            try:
                self._vad = webrtcvad.Vad(2)  # 0-3, 2=中等激进度
            except Exception:
                self._vad = None
        # 能量阈值（RMS VAD 用）
        self._energy_threshold = 300  # 自适应调整
        self._dynamic_energy_adjust_ratio = 0.15

        # pyaudio 流
        self._stream = None

    @property
    def available(self) -> bool:
        """是否可用（需 pyaudio）"""
        return _PYAUDIO_AVAILABLE

    @property
    def running(self) -> bool:
        return self._running

    @property
    def vad_backend(self) -> str:
        return "webrtcvad" if self._vad is not None else "energy_rms"

    def start(
        self,
        wake_word: Optional[str] = None,
        language: str = "zh",
        device_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        """启动后台监听

        Args:
            wake_word: 唤醒词（如 "嘿 SCU2"）。为 None 则直通模式
            language: 识别语言
            device_index: 麦克风设备索引（为空用默认）

        Returns:
            {success, wake_word, vad_backend, error}
        """
        if not _PYAUDIO_AVAILABLE:
            return {"success": False, "error": "pyaudio 不可用，无法采集麦克风音频。请执行: pip install pyaudio"}
        if self._running:
            return {"success": True, "message": "监听已在运行", "wake_word": self._wake_word}
        if not _SR_AVAILABLE and not _WHISPER_AVAILABLE:
            return {"success": False, "error": "speech_recognition/whisper 均不可用，无法识别"}

        self._wake_word = wake_word
        self._wake_word_active = (wake_word is None)  # 直通模式视为已唤醒
        self._language = language
        self._stop_event.clear()

        self._thread = threading.Thread(target=self._listen_loop, args=(device_index,), daemon=True)
        self._thread.start()
        self._running = True

        logger.info(f"持续监听已启动 (wake_word={wake_word}, vad={self.vad_backend})")
        self._notify_state("listening")
        return {
            "success": True,
            "wake_word": wake_word,
            "vad_backend": self.vad_backend,
            "language": language,
            "error": None,
        }

    def stop(self) -> Dict[str, Any]:
        """停止监听"""
        if not self._running:
            return {"success": True, "message": "监听未运行"}
        self._stop_event.set()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._notify_state("idle")
        logger.info("持续监听已停止")
        return {"success": True}

    def status(self) -> Dict[str, Any]:
        """监听状态"""
        return {
            "available": self.available,
            "running": self._running,
            "vad_backend": self.vad_backend,
            "wake_word": self._wake_word,
            "wake_word_active": self._wake_word_active,
            "language": self._language,
            "sample_rate": self.sample_rate,
            "frame_duration_ms": self.frame_duration_ms,
            "silence_duration_ms": self.silence_duration_ms,
            "energy_threshold": self._energy_threshold,
            "pyaudio": _PYAUDIO_AVAILABLE,
            "webrtcvad": _WEBRTC_VAD_AVAILABLE,
        }

    # ─── 内部实现 ────────────────────────────────────

    def _listen_loop(self, device_index: Optional[int]):
        """监听主循环（后台线程）"""
        import pyaudio
        try:
            pa = pyaudio.PyAudio()
            self._stream = pa.open(
                format=pyaudio.paInt16,
                channels=DEFAULT_CHANNELS,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=int(self.sample_rate * self.frame_duration_ms / 1000),
                input_device_index=device_index,
            )
            logger.debug("麦克风流已打开")

            frames_buffer = []
            silence_frames = 0
            in_speech = False
            frame_count = 0

            while not self._stop_event.is_set():
                try:
                    frame = self._stream.read(
                        int(self.sample_rate * self.frame_duration_ms / 1000),
                        exception_on_overflow=False,
                    )
                except Exception as e:
                    logger.debug(f"读取音频帧失败: {e}")
                    continue

                frame_count += 1
                is_speech = self._detect_speech(frame)

                if is_speech:
                    if not in_speech:
                        in_speech = True
                        self._notify_state("speaking")
                    frames_buffer.append(frame)
                    silence_frames = 0
                    # 自适应降低阈值（背景噪声可能变小）
                    self._adjust_energy(frame, is_speech=True)
                else:
                    if in_speech:
                        frames_buffer.append(frame)  # 静音段也加入（保留尾音）
                        silence_frames += 1
                        self._adjust_energy(frame, is_speech=False)
                        # 静音达到阈值，认为一句话结束
                        silence_ms = silence_frames * self.frame_duration_ms
                        if silence_ms >= self.silence_duration_ms:
                            self._process_utterance(frames_buffer)
                            frames_buffer = []
                            silence_frames = 0
                            in_speech = False
                            self._notify_state("listening")
                    # 超长保护
                    if len(frames_buffer) > 0:
                        total_seconds = len(frames_buffer) * self.frame_duration_ms / 1000
                        if total_seconds >= self.max_utterance_seconds:
                            self._process_utterance(frames_buffer)
                            frames_buffer = []
                            silence_frames = 0
                            in_speech = False
                            self._notify_state("listening")

        except Exception as e:
            logger.error(f"监听循环异常: {e}")
        finally:
            try:
                if self._stream:
                    self._stream.stop_stream()
                    self._stream.close()
                pa.terminate()
            except Exception:
                pass
            self._running = False

    def _detect_speech(self, frame: bytes) -> bool:
        """VAD 检测一帧是否为语音

        Args:
            frame: PCM 16-bit 单声道帧

        Returns:
            True=语音, False=静音
        """
        if self._vad is not None:
            # webrtcvad
            try:
                return self._vad.is_speech(frame, self.sample_rate)
            except Exception:
                pass
        # 能量阈值 VAD
        return self._energy_vad(frame)

    def _energy_vad(self, frame: bytes) -> bool:
        """能量阈值 VAD（基于 RMS）"""
        n_samples = len(frame) // DEFAULT_SAMPLE_WIDTH
        if n_samples == 0:
            return False
        samples = struct.unpack(f"<{n_samples}h", frame)
        # RMS
        sum_sq = sum(s * s for s in samples)
        rms = math.sqrt(sum_sq / n_samples)
        return rms > self._energy_threshold

    def _adjust_energy(self, frame: bytes, is_speech: bool):
        """自适应调整能量阈值"""
        n_samples = len(frame) // DEFAULT_SAMPLE_WIDTH
        if n_samples == 0:
            return
        samples = struct.unpack(f"<{n_samples}h", frame)
        sum_sq = sum(s * s for s in samples)
        rms = math.sqrt(sum_sq / n_samples)
        if not is_speech:
            # 静音帧：缓慢降低阈值（适应背景噪声变化）
            target = rms * 1.5
            self._energy_threshold = (
                self._energy_threshold * (1 - self._dynamic_energy_adjust_ratio)
                + target * self._dynamic_energy_adjust_ratio
            )

    def _process_utterance(self, frames: List[bytes]):
        """处理一段语音：转 WAV → 识别 → 唤醒词检测 → 回调"""
        if not frames:
            return
        # 拼接为 WAV
        audio_bytes = b"".join(frames)
        wav_bytes = _write_wav(audio_bytes, sample_rate=self.sample_rate)

        # 太短的丢弃（< 0.3s，多半是噪声）
        duration = len(audio_bytes) / (self.sample_rate * DEFAULT_SAMPLE_WIDTH)
        if duration < 0.3:
            return

        # 识别
        try:
            voice = get_voice_io()
            result = voice.recognize(wav_bytes, format="wav", language=self._language)
        except Exception as e:
            logger.debug(f"识别失败: {e}")
            return

        text = result.get("text", "").strip()
        if not text:
            return

        logger.info(f"识别到: {text!r} (duration={duration:.1f}s)")

        # 唤醒词检测
        if self._wake_word and not self._wake_word_active:
            if self._wake_word.lower() in text.lower():
                self._wake_word_active = True
                logger.info(f"唤醒词命中: {self._wake_word}")
                if self.on_wake_word:
                    try:
                        self.on_wake_word()
                    except Exception as e:
                        logger.error(f"on_wake_word 回调异常: {e}")
                self._notify_state("awake")
            return  # 唤醒词模式下，唤醒词本身不作为命令

        # 命令识别 → 回调
        if self.on_utterance:
            try:
                self.on_utterance(text)
            except Exception as e:
                logger.error(f"on_utterance 回调异常: {e}")

        # 唤醒词模式下，单次命令后重新进入待唤醒状态
        if self._wake_word:
            self._wake_word_active = False
            self._notify_state("listening")

    def _notify_state(self, state: str):
        """通知状态变化"""
        if self.on_state_change:
            try:
                self.on_state_change(state)
            except Exception as e:
                logger.debug(f"on_state_change 回调异常: {e}")


_listener_instance: Optional[ContinuousListener] = None


def get_listener() -> ContinuousListener:
    """获取 ContinuousListener 全局单例"""
    global _listener_instance
    if _listener_instance is None:
        _listener_instance = ContinuousListener()
    return _listener_instance


# =====================================================================
# 单例
# =====================================================================
_voice_io_instance: Optional[VoiceIO] = None


def get_voice_io() -> VoiceIO:
    """获取语音 IO 单例"""
    global _voice_io_instance
    if _voice_io_instance is None:
        _voice_io_instance = VoiceIO()
    return _voice_io_instance
