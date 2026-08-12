# -*- coding: utf-8 -*-
"""
m_layer/multimodal.py — 多模态理解模块（M层）
================================================
实现图像、音频、视频的多模态理解能力，对外提供统一入口。

能力对标：AI助手对图像/音频/视频输入的"看图说话、听音辨义、抽帧析影"能力

功能：
  1. MultimodalProcessor 统一入口：process(input_data, modality) → result
  2. 自动模态检测：detect_modality(input_data)
  3. ImageUnderstanding 图像理解（PIL + pytesseract 可选）
  4. AudioUnderstanding 音频理解（wave + pydub/speech_recognition 可选）
  5. VideoUnderstanding 视频理解（cv2 可选）
  6. 多模态融合（text+image / text+audio / mixed）
  7. 结果缓存到 SCU3_data/multimodal_cache.json

降级策略：
  - PIL 不可用        → 仅提取图像元数据
  - pytesseract 不可用 → 跳过 OCR
  - cv2 不可用         → 仅提取视频元数据
  - 所有外部库不可用   → 返回基础信息（文件大小、格式、哈希）

架构归属：M层（认知层的多模态扩展）
依赖：可选 PIL / pytesseract / cv2 / pydub / speech_recognition
"""
import os
import json
import wave
import hashlib
import logging
import struct
from typing import Dict, Any, Optional, List, Tuple, Union
from datetime import datetime

logger = logging.getLogger("SCU3.m.multimodal")

# 项目根目录与数据目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "SCU3_data")
CACHE_PATH = os.path.join(DATA_DIR, "multimodal_cache.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ─── 外部依赖可选导入 ────────────────────────────────────
try:
    from PIL import Image, ImageStat, ImageFilter
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    logger.debug("PIL/Pillow 不可用，图像理解将仅提取元数据")

try:
    import pytesseract
    _PYTESSERACT_AVAILABLE = True
except ImportError:
    _PYTESSERACT_AVAILABLE = False
    logger.debug("pytesseract 不可用，跳过 OCR")

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False
    logger.debug("cv2 不可用，视频理解将仅提取元数据")

try:
    from pydub import AudioSegment
    _PYDUB_AVAILABLE = True
except ImportError:
    _PYDUB_AVAILABLE = False
    logger.debug("pydub 不可用，mp3 解码将不可用")

try:
    import speech_recognition as sr
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False
    logger.debug("speech_recognition 不可用，跳过 STT")


# ─── 模态与格式常量 ────────────────────────────────────
MODALITY_TEXT = "text"
MODALITY_IMAGE = "image"
MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"
MODALITY_MIXED = "mixed"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
AUDIO_EXTS = {".wav", ".mp3"}
VIDEO_EXTS = {".mp4", ".avi", ".mov"}


# =====================================================================
# 工具函数
# =====================================================================
def _file_hash(path: str, algorithm: str = "md5", chunk_size: int = 8192) -> str:
    """计算文件哈希（用于缓存键与去重）"""
    h = hashlib.new(algorithm)
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.warning(f"计算文件哈希失败 {path}: {e}")
        return ""


def _file_meta(path: str) -> Dict[str, Any]:
    """获取基础文件元信息（外部库全部不可用时的兜底）"""
    try:
        stat = os.stat(path)
        return {
            "path": path,
            "file_name": os.path.basename(path),
            "file_size": stat.st_size,
            "extension": os.path.splitext(path)[1].lower(),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "md5": _file_hash(path),
        }
    except Exception as e:
        return {"path": path, "error": str(e)}


# =====================================================================
# 图像理解
# =====================================================================
class ImageUnderstanding:
    """图像理解模块

    支持能力：
      - 图像加载与预处理（PIL）
      - 图像描述生成（规则+模板，无LLM时降级）
      - OCR 文字识别（pytesseract，可选）
      - 图像分类（颜色直方图、边缘检测等简单特征）
      - 人脸检测（简单肤色模型，可选）
      - 图像相似度对比
    支持格式：jpg, png, gif, bmp, webp
    """

    def __init__(self):
        self._available = _PIL_AVAILABLE

    # ── 加载与预处理 ────────────────────────────────
    def load(self, path: str) -> Optional[Any]:
        """加载图像为 PIL.Image 对象"""
        if not self._available:
            return None
        try:
            img = Image.open(path)
            img.load()  # 强制加载像素数据
            return img
        except Exception as e:
            logger.warning(f"加载图像失败 {path}: {e}")
            return None

    def preprocess(self, img: Any, max_size: int = 1024,
                   to_gray: bool = False) -> Any:
        """图像预处理：缩放到合理尺寸、可选灰度化"""
        if img is None:
            return None
        try:
            img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
            if to_gray:
                img = img.convert("L")
            w, h = img.size
            if max(w, h) > max_size:
                ratio = max_size / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)))
            return img
        except Exception as e:
            logger.warning(f"图像预处理失败: {e}")
            return img

    # ── 图像描述（规则+模板） ────────────────────────
    def describe(self, path: str) -> Dict[str, Any]:
        """生成图像描述（基于规则+模板，无LLM时降级）"""
        result = {
            "path": path,
            "description": "",
            "features": {},
        }
        img = self.load(path)
        if img is None:
            if not self._available:
                result["description"] = "PIL不可用，无法解析图像内容"
                result["degraded"] = True
            return result

        try:
            features = self._extract_features(img)
            result["features"] = features

            # 基于特征拼装描述
            parts = []
            parts.append(f"{img.size[0]}×{img.size[1]}像素的{img.format or '图像'}")
            parts.append(f"主色调为{features.get('dominant_color', '未知')}")
            parts.append(f"平均亮度{features.get('brightness', 0):.1f}（0-255）")
            if features.get("edge_density", 0) > 0.15:
                parts.append("边缘丰富，含较多细节或文字")
            else:
                parts.append("画面较平滑，细节较少")
            if features.get("is_colorful"):
                parts.append("色彩饱和度较高")
            else:
                parts.append("色彩较单一")

            result["description"] = "，".join(parts) + "。"

            # 可选 OCR
            ocr_text = self.ocr(path)
            if ocr_text:
                result["ocr_text"] = ocr_text
                result["description"] += f" 识别到文字：{ocr_text[:80]}"

            # 可选人脸检测
            faces = self.detect_faces(path)
            if faces:
                result["face_count"] = len(faces)
                result["faces"] = faces
                result["description"] += f" 检测到{len(faces)}处疑似人脸区域。"

        except Exception as e:
            logger.warning(f"图像描述生成失败 {path}: {e}")
            result["error"] = str(e)
        return result

    def _extract_features(self, img: Any) -> Dict[str, Any]:
        """提取图像特征（颜色直方图、亮度、边缘密度等）"""
        features: Dict[str, Any] = {}
        try:
            stat = ImageStat.Stat(img)
            # 平均亮度
            if img.mode == "L":
                brightness = stat.mean[0]
            else:
                brightness = sum(stat.mean) / len(stat.mean)
            features["brightness"] = float(brightness)
            features["contrast"] = float(sum(stat.stddev) / len(stat.stddev)) if stat.stddev else 0.0

            # 主色调与色彩丰富度
            if img.mode != "L":
                small = img.convert("RGB").resize((64, 64))
                pixels = list(small.getdata())
                r = sum(p[0] for p in pixels) / len(pixels)
                g = sum(p[1] for p in pixels) / len(pixels)
                b = sum(p[2] for p in pixels) / len(pixels)
                features["avg_color"] = (int(r), int(g), int(b))
                features["dominant_color"] = self._color_name(r, g, b)
                # 颜色方差作为色彩丰富度近似
                color_var = sum(
                    (p[0]-r)**2 + (p[1]-g)**2 + (p[2]-b)**2 for p in pixels
                ) / len(pixels)
                features["is_colorful"] = color_var > 1500

            # 边缘密度（用 FindEdges 滤镜）
            gray = img.convert("L") if img.mode != "L" else img
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_stat = ImageStat.Stat(edges)
            features["edge_density"] = float(edge_stat.mean[0]) / 255.0
        except Exception as e:
            logger.debug(f"特征提取异常: {e}")
        return features

    @staticmethod
    def _color_name(r: float, g: float, b: float) -> str:
        """根据 RGB 均值返回颜色名称"""
        mx = max(r, g, b)
        mn = min(r, g, b)
        if mx - mn < 25:
            v = (r + g + b) / 3
            if v < 60:
                return "黑色"
            if v < 180:
                return "灰色"
            return "白色"
        if r >= g and r >= b:
            return "红色" if r > b + 30 else "品红"
        if g >= r and g >= b:
            return "绿色"
        if b > r and b > g:
            return "蓝色" if b > g + 30 else "青色"
        return "混合色"

    # ── OCR ────────────────────────────────────────
    def ocr(self, path: str, lang: str = "chi_sim+eng") -> str:
        """OCR 文字识别（依赖 pytesseract）"""
        if not _PYTESSERACT_AVAILABLE or not self._available:
            return ""
        try:
            img = self.load(path)
            if img is None:
                return ""
            text = pytesseract.image_to_string(img, lang=lang)
            return text.strip()
        except Exception as e:
            logger.debug(f"OCR 失败 {path}: {e}")
            return ""

    # ── 分类（基于简单特征） ────────────────────────
    def classify(self, path: str) -> Dict[str, Any]:
        """基于简单特征的图像分类"""
        result = {"path": path, "category": "unknown", "confidence": 0.0}
        img = self.load(path)
        if img is None:
            return result
        try:
            feats = self._extract_features(img)
            category = "photo"
            confidence = 0.5
            edge = feats.get("edge_density", 0)
            contrast = feats.get("contrast", 0)

            # 高边缘密度 + 高对比度 → 倾向文档/线稿
            if edge > 0.25 and contrast > 60:
                category = "document_or_lineart"
                confidence = 0.75
            elif feats.get("is_colorful") and edge < 0.2:
                category = "illustration"
                confidence = 0.6
            elif edge < 0.08:
                category = "smooth_image"
                confidence = 0.55

            result.update({
                "category": category,
                "confidence": round(confidence, 3),
                "features": feats,
            })
        except Exception as e:
            logger.warning(f"图像分类失败 {path}: {e}")
            result["error"] = str(e)
        return result

    # ── 人脸检测（简单肤色模型） ────────────────────
    def detect_faces(self, path: str) -> List[Dict[str, Any]]:
        """基于简单肤色模型的人脸检测（可选，返回区域列表）"""
        if not self._available:
            return []
        try:
            img = self.load(path)
            if img is None:
                return []
            small = img.convert("RGB").resize((128, 128))
            pixels = small.load()
            w, h = small.size
            skin_map = [[0]*w for _ in range(h)]
            for y in range(h):
                for x in range(w):
                    r, g, b = pixels[x, y]
                    # 常见肤色判定规则
                    if r > 95 and g > 40 and b > 20 and \
                       max(r, g, b) - min(r, g, b) > 15 and \
                       abs(r - g) > 15 and r > g and r > b:
                        skin_map[y][x] = 1

            # 简单连通区域聚类（按行合并相邻肤色点）
            regions: List[Tuple[int, int, int, int, int]] = []  # x1,y1,x2,y2,area
            visited = [[False]*w for _ in range(h)]
            for y in range(h):
                for x in range(w):
                    if skin_map[y][x] and not visited[y][x]:
                        stack = [(x, y)]
                        xs, ys = [], []
                        while stack:
                            cx, cy = stack.pop()
                            if 0 <= cx < w and 0 <= cy < h and \
                               skin_map[cy][cx] and not visited[cy][cx]:
                                visited[cy][cx] = True
                                xs.append(cx)
                                ys.append(cy)
                                stack.extend([(cx+1, cy), (cx-1, cy),
                                              (cx, cy+1), (cx, cy-1)])
                        area = len(xs)
                        # 人脸区域经验阈值
                        if 40 < area < 1500:
                            x1, x2 = min(xs), max(xs)
                            y1, y2 = min(ys), max(ys)
                            # 长宽比合理
                            rw, rh = x2 - x1 + 1, y2 - y1 + 1
                            if 0.5 < rw / rh < 2.0:
                                regions.append((x1, y1, x2, y2, area))

            # 缩放回原图坐标
            ow, oh = img.size
            sx, sy = ow / w, oh / h
            faces = []
            for x1, y1, x2, y2, area in regions[:10]:
                faces.append({
                    "bbox": [int(x1*sx), int(y1*sy), int(x2*sx), int(y2*sy)],
                    "area_ratio": round(area / (w*h), 4),
                })
            return faces
        except Exception as e:
            logger.debug(f"人脸检测失败 {path}: {e}")
            return []

    # ── 相似度对比 ────────────────────────────────────
    def similarity(self, path_a: str, path_b: str) -> Dict[str, Any]:
        """图像相似度对比（基于缩略图与颜色直方图）"""
        result = {"path_a": path_a, "path_b": path_b, "similarity": 0.0}
        if not self._available:
            return result
        try:
            ia = self.load(path_a)
            ib = self.load(path_b)
            if ia is None or ib is None:
                result["error"] = "图像加载失败"
                return result
            ta = ia.convert("RGB").resize((32, 32))
            tb = ib.convert("RGB").resize((32, 32))
            pa = list(ta.getdata())
            pb = list(tb.getdata())
            same = sum(1 for a, b in zip(pa, pb) if a == b)
            result["similarity"] = round(same / len(pa), 4)

            # 颜色直方图相似度（余弦相似度）
            ha = ia.convert("RGB").histogram()
            hb = ib.convert("RGB").histogram()
            min_len = min(len(ha), len(hb))
            ha, hb = ha[:min_len], hb[:min_len]
            dot = sum(a*b for a, b in zip(ha, hb))
            na = sum(a*a for a in ha) ** 0.5
            nb = sum(b*b for b in hb) ** 0.5
            if na > 0 and nb > 0:
                result["histogram_similarity"] = round(dot / (na*nb), 4)
        except Exception as e:
            logger.warning(f"图像相似度计算失败: {e}")
            result["error"] = str(e)
        return result

    # ── 综合入口 ────────────────────────────────────
    def understand(self, path: str) -> Dict[str, Any]:
        """图像理解综合入口"""
        info = _file_meta(path)
        info["modality"] = MODALITY_IMAGE
        if not os.path.exists(path):
            info["error"] = "文件不存在"
            return info

        if not self._available:
            info["degraded"] = True
            info["note"] = "PIL不可用，仅返回基础文件信息"
            return info

        desc = self.describe(path)
        info["description"] = desc.get("description", "")
        info["features"] = desc.get("features", {})
        if "ocr_text" in desc:
            info["ocr_text"] = desc["ocr_text"]
        if "face_count" in desc:
            info["face_count"] = desc["face_count"]
            info["faces"] = desc.get("faces", [])
        info.update(self.classify(path))
        # classify 会覆盖 features，保留一份
        if "features" not in info and "features" in desc:
            info["features"] = desc["features"]
        return info


# =====================================================================
# 音频理解
# =====================================================================
class AudioUnderstanding:
    """音频理解模块

    支持能力：
      - 音频加载（wave 标准库；mp3 通过 pydub 解码）
      - 音频特征提取（时长、采样率、音量、静音段）
      - 语音转文字（STT，可选 speech_recognition）
      - 音频分类（音乐/语音/噪声，基于简单特征）
    支持格式：wav, mp3（mp3 需 pydub）
    """

    def __init__(self):
        pass

    # ── 加载 ────────────────────────────────────────
    def load(self, path: str) -> Optional[Dict[str, Any]]:
        """加载音频文件，返回统一格式的样本字典

        返回结构:
            {
                "samples": List[int],  # 单声道 16-bit 样本
                "sample_rate": int,
                "n_channels": int,
                "sample_width": int,
                "n_frames": int,
                "duration": float,
            }
        """
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".wav":
                return self._load_wav(path)
            if ext == ".mp3":
                return self._load_mp3(path)
            logger.warning(f"不支持的音频格式: {ext}")
            return None
        except Exception as e:
            logger.warning(f"音频加载失败 {path}: {e}")
            return None

    def _load_wav(self, path: str) -> Optional[Dict[str, Any]]:
        """加载 WAV 文件"""
        try:
            with wave.open(path, "rb") as wf:
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)

            # 解码为 16-bit 样本列表
            if sample_width == 2:
                fmt = "<" + "h" * (len(raw) // 2)
                samples = list(struct.unpack(fmt, raw))
            elif sample_width == 1:
                samples = [b - 128 for b in raw]  # 8-bit unsigned
            elif sample_width == 4:
                fmt = "<" + "i" * (len(raw) // 4)
                samples = list(struct.unpack(fmt, raw))
            else:
                samples = []

            # 多声道 → 取第一声道
            if n_channels > 1 and samples:
                samples = samples[0::n_channels]

            duration = n_frames / sample_rate if sample_rate else 0.0
            return {
                "samples": samples,
                "sample_rate": sample_rate,
                "n_channels": n_channels,
                "sample_width": sample_width,
                "n_frames": n_frames,
                "duration": duration,
            }
        except Exception as e:
            logger.warning(f"WAV 加载失败 {path}: {e}")
            return None

    def _load_mp3(self, path: str) -> Optional[Dict[str, Any]]:
        """加载 MP3 文件（依赖 pydub）"""
        if not _PYDUB_AVAILABLE:
            logger.warning("pydub 不可用，无法解码 mp3")
            return None
        try:
            seg = AudioSegment.from_file(path)
            samples = list(seg.get_array_of_samples())
            if seg.channels > 1 and samples:
                samples = samples[0::seg.channels]
            duration = len(seg) / 1000.0
            return {
                "samples": samples,
                "sample_rate": seg.frame_rate,
                "n_channels": seg.channels,
                "sample_width": seg.sample_width,
                "n_frames": len(samples),
                "duration": duration,
            }
        except Exception as e:
            logger.warning(f"MP3 加载失败 {path}: {e}")
            return None

    # ── 特征提取 ────────────────────────────────────
    def extract_features(self, path: str) -> Dict[str, Any]:
        """提取音频特征：时长、采样率、音量、静音段"""
        result: Dict[str, Any] = {"path": path}
        audio = self.load(path)
        if audio is None:
            result["error"] = "音频加载失败"
            return result

        samples = audio["samples"]
        sr = audio["sample_rate"]
        result["sample_rate"] = sr
        result["n_channels"] = audio["n_channels"]
        result["sample_width"] = audio["sample_width"]
        result["duration"] = round(audio["duration"], 3)

        if not samples:
            result["error"] = "无可用样本数据"
            return result

        try:
            # 平均音量（RMS 近似，基于峰值绝对值）
            abs_samples = [abs(s) for s in samples]
            mean_vol = sum(abs_samples) / len(abs_samples)
            max_vol = max(abs_samples)
            result["mean_volume"] = mean_vol
            result["max_volume"] = max_vol

            # 分帧计算静音段（每帧约 20ms）
            frame_size = max(1, int(sr * 0.02))
            silence_threshold = max(1, int(mean_vol * 0.3))
            silence_segments: List[Tuple[float, float]] = []
            in_silence = False
            sil_start = 0
            for i in range(0, len(samples), frame_size):
                frame = samples[i:i+frame_size]
                if not frame:
                    break
                vol = sum(abs(s) for s in frame) / len(frame)
                t = i / sr
                if vol < silence_threshold:
                    if not in_silence:
                        in_silence = True
                        sil_start = t
                else:
                    if in_silence:
                        in_silence = False
                        if t - sil_start > 0.1:
                            silence_segments.append((round(sil_start, 3), round(t, 3)))
            if in_silence:
                end_t = len(samples) / sr
                if end_t - sil_start > 0.1:
                    silence_segments.append((round(sil_start, 3), round(end_t, 3)))

            result["silence_segments"] = silence_segments
            result["silence_ratio"] = round(
                sum(b - a for a, b in silence_segments) / audio["duration"], 4
            ) if audio["duration"] > 0 else 0.0
        except Exception as e:
            logger.debug(f"音频特征提取异常: {e}")
            result["feature_error"] = str(e)
        return result

    # ── 语音转文字 ────────────────────────────────────
    def transcribe(self, path: str, language: str = "zh-CN") -> Dict[str, Any]:
        """语音转文字（STT，依赖 speech_recognition）"""
        result = {"path": path, "text": ""}
        if not _SR_AVAILABLE:
            result["error"] = "speech_recognition 不可用，跳过 STT"
            return result
        try:
            r = sr.Recognizer()
            ext = os.path.splitext(path)[1].lower()
            if ext == ".wav":
                with sr.AudioFile(path) as src:
                    audio_data = r.record(src)
            elif ext == ".mp3" and _PYDUB_AVAILABLE:
                # 转为 wav 临时数据
                seg = AudioSegment.from_file(path)
                tmp_wav = path + ".tmp.wav"
                seg.export(tmp_wav, format="wav")
                try:
                    with sr.AudioFile(tmp_wav) as src:
                        audio_data = r.record(src)
                finally:
                    try:
                        os.remove(tmp_wav)
                    except OSError:
                        pass
            else:
                result["error"] = f"暂不支持该格式的 STT: {ext}"
                return result

            text = r.recognize_google(audio_data, language=language)
            result["text"] = text
        except sr.UnknownValueError:
            result["error"] = "无法识别语音内容"
        except sr.RequestError as e:
            result["error"] = f"STT 服务请求失败: {e}"
        except Exception as e:
            logger.warning(f"STT 失败 {path}: {e}")
            result["error"] = str(e)
        return result

    # ── 分类（音乐/语音/噪声） ────────────────────────
    def classify(self, path: str) -> Dict[str, Any]:
        """基于简单特征的音频分类"""
        result = {"path": path, "category": "unknown", "confidence": 0.0}
        feats = self.extract_features(path)
        if "error" in feats:
            return result
        try:
            mean_vol = feats.get("mean_volume", 0)
            silence_ratio = feats.get("silence_ratio", 0)
            duration = feats.get("duration", 0)

            # 经验规则：
            #   - 高静音比 + 短时 → 语音
            #   - 持续低静音 → 音乐
            #   - 低音量且极短 → 噪声
            if duration < 1.0 and mean_vol < 200:
                category = "noise"
                confidence = 0.6
            elif silence_ratio > 0.25:
                category = "speech"
                confidence = 0.65
            elif silence_ratio < 0.05 and duration > 5:
                category = "music"
                confidence = 0.6
            else:
                category = "speech_or_music"
                confidence = 0.45

            result.update({
                "category": category,
                "confidence": confidence,
                "features": feats,
            })
        except Exception as e:
            logger.warning(f"音频分类失败 {path}: {e}")
            result["error"] = str(e)
        return result

    # ── 综合入口 ────────────────────────────────────
    def understand(self, path: str) -> Dict[str, Any]:
        info = _file_meta(path)
        info["modality"] = MODALITY_AUDIO
        if not os.path.exists(path):
            info["error"] = "文件不存在"
            return info

        feats = self.extract_features(path)
        info["features"] = feats
        info.update(self.classify(path))
        # classify 已包含 features
        info["features"] = feats

        # 可选 STT
        if _SR_AVAILABLE:
            stt = self.transcribe(path)
            if stt.get("text"):
                info["transcript"] = stt["text"]
                info["description"] = f"语音转文字：{stt['text'][:120]}"
            elif "error" in stt:
                info["stt_error"] = stt["error"]
        return info


# =====================================================================
# 视频理解
# =====================================================================
class VideoUnderstanding:
    """视频理解模块

    支持能力：
      - 视频关键帧提取（cv2，可选）
      - 视频元数据提取（时长、分辨率、帧率）
      - 视频摘要生成（基于关键帧）
    支持格式：mp4, avi, mov
    """

    def __init__(self):
        self._available = _CV2_AVAILABLE
        self._image_u = ImageUnderstanding()

    # ── 元数据 ────────────────────────────────────
    def extract_metadata(self, path: str) -> Dict[str, Any]:
        """提取视频元数据"""
        result: Dict[str, Any] = {"path": path}
        if not self._available:
            result["degraded"] = True
            result["note"] = "cv2 不可用，仅返回基础文件信息"
            return result
        try:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                result["error"] = "无法打开视频文件"
                cap.release()
                return result
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = frame_count / fps if fps > 0 else 0.0
            result.update({
                "width": width,
                "height": height,
                "fps": round(fps, 3),
                "frame_count": frame_count,
                "duration": round(duration, 3),
                "resolution": f"{width}×{height}",
            })
            cap.release()
        except Exception as e:
            logger.warning(f"视频元数据提取失败 {path}: {e}")
            result["error"] = str(e)
        return result

    # ── 关键帧提取 ────────────────────────────────
    def extract_keyframes(self, path: str, n_frames: int = 5,
                          output_dir: Optional[str] = None) -> List[str]:
        """提取视频关键帧，返回保存的图片路径列表"""
        if not self._available:
            return []
        if output_dir is None:
            output_dir = os.path.join(DATA_DIR, "keyframes")
        os.makedirs(output_dir, exist_ok=True)

        saved_paths: List[str] = []
        try:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                return []
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if frame_count <= 0:
                cap.release()
                return []

            # 均匀采样关键帧
            step = max(1, frame_count // n_frames)
            base_name = os.path.splitext(os.path.basename(path))[0]
            for i in range(n_frames):
                pos = min(frame_count - 1, i * step)
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                out_path = os.path.join(output_dir, f"{base_name}_kf{i}.jpg")
                cv2.imwrite(out_path, frame)
                saved_paths.append(out_path)
            cap.release()
        except Exception as e:
            logger.warning(f"关键帧提取失败 {path}: {e}")
        return saved_paths

    # ── 视频摘要 ────────────────────────────────────
    def summarize(self, path: str, n_frames: int = 5) -> Dict[str, Any]:
        """基于关键帧生成视频摘要"""
        result: Dict[str, Any] = {"path": path}
        meta = self.extract_metadata(path)
        result["metadata"] = meta

        if not self._available:
            result["degraded"] = True
            return result

        keyframes = self.extract_keyframes(path, n_frames=n_frames)
        result["keyframes"] = keyframes
        frame_descriptions: List[str] = []
        for kf in keyframes:
            desc = self._image_u.describe(kf)
            if desc.get("description"):
                frame_descriptions.append(desc["description"])
        result["frame_descriptions"] = frame_descriptions

        # 拼装摘要
        if frame_descriptions:
            result["summary"] = (
                f"视频共{meta.get('duration', 0)}秒，"
                f"分辨率{meta.get('resolution', '未知')}，"
                f"抽取{len(keyframes)}个关键帧："
                + " | ".join(frame_descriptions)
            )
        else:
            result["summary"] = (
                f"视频共{meta.get('duration', 0)}秒，"
                f"分辨率{meta.get('resolution', '未知')}，未能提取关键帧内容。"
            )
        return result

    # ── 综合入口 ────────────────────────────────────
    def understand(self, path: str) -> Dict[str, Any]:
        info = _file_meta(path)
        info["modality"] = MODALITY_VIDEO
        if not os.path.exists(path):
            info["error"] = "文件不存在"
            return info

        if not self._available:
            info["degraded"] = True
            info["note"] = "cv2 不可用，仅返回基础文件信息"
            return info

        summary = self.summarize(path)
        info["metadata"] = summary.get("metadata", {})
        info["keyframes"] = summary.get("keyframes", [])
        info["summary"] = summary.get("summary", "")
        if "frame_descriptions" in summary:
            info["frame_descriptions"] = summary["frame_descriptions"]
        return info


# =====================================================================
# 多模态处理器
# =====================================================================
class MultimodalProcessor:
    """多模态理解统一处理器

    用法:
        proc = get_multimodal_processor()
        result = proc.process("/path/to/image.jpg")
        # result = {
        #     "modality": "image",
        #     "description": "...",
        #     "features": {...},
        #     ...
        # }
    """

    def __init__(self):
        self._image_u = ImageUnderstanding()
        self._audio_u = AudioUnderstanding()
        self._video_u = VideoUnderstanding()
        self._cache: Dict[str, Any] = {}
        self._load_cache()

    # ── 缓存 ────────────────────────────────────────
    def _load_cache(self) -> None:
        """从磁盘加载缓存"""
        try:
            if os.path.exists(CACHE_PATH):
                with open(CACHE_PATH, "r", encoding="utf-8") as f:
                    self._cache = json.load(f) or {}
        except Exception as e:
            logger.warning(f"加载多模态缓存失败: {e}")
            self._cache = {}

    def _save_cache(self) -> None:
        """持久化缓存到磁盘"""
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存多模态缓存失败: {e}")

    def _cache_key(self, input_data: Any, modality: str) -> str:
        """生成缓存键（基于文件路径+修改时间+模态）"""
        if isinstance(input_data, str) and os.path.exists(input_data):
            try:
                stat = os.stat(input_data)
                return f"{modality}:{input_data}:{stat.st_mtime}:{stat.st_size}"
            except OSError:
                pass
        # 文本或非文件输入用哈希
        try:
            h = hashlib.md5(str(input_data).encode("utf-8")).hexdigest()
            return f"{modality}:{h}"
        except Exception:
            return f"{modality}:{id(input_data)}"

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()
        self._save_cache()
        logger.info("多模态缓存已清空")

    # ── 模态检测 ────────────────────────────────────
    def detect_modality(self, input_data: Any) -> str:
        """自动检测输入模态

        Returns:
            "text" | "image" | "audio" | "video" | "mixed"
        """
        # 字符串输入
        if isinstance(input_data, str):
            # 先按扩展名判断模态（文件不存在但扩展名明确也算对应模态）
            ext = os.path.splitext(input_data)[1].lower()
            if ext in IMAGE_EXTS:
                return MODALITY_IMAGE
            if ext in AUDIO_EXTS:
                return MODALITY_AUDIO
            if ext in VIDEO_EXTS:
                return MODALITY_VIDEO
            # 无扩展名或未知扩展名 → 视为文本
            return MODALITY_TEXT

        # 字典混合输入
        if isinstance(input_data, dict):
            modalities = set()
            for key in ("text", "image", "image_path", "audio", "audio_path",
                        "video", "video_path"):
                if input_data.get(key):
                    if key == "text":
                        modalities.add(MODALITY_TEXT)
                    elif key in ("image", "image_path"):
                        modalities.add(MODALITY_IMAGE)
                    elif key in ("audio", "audio_path"):
                        modalities.add(MODALITY_AUDIO)
                    elif key in ("video", "video_path"):
                        modalities.add(MODALITY_VIDEO)
            if len(modalities) > 1:
                return MODALITY_MIXED
            if len(modalities) == 1:
                return modalities.pop()
            return MODALITY_TEXT

        # 列表/元组混合输入
        if isinstance(input_data, (list, tuple)):
            modalities = set()
            for item in input_data:
                modalities.add(self.detect_modality(item))
            modalities.discard(MODALITY_TEXT)  # 文本不单独计入混合
            if len(modalities) > 1:
                return MODALITY_MIXED
            if len(modalities) == 1:
                return modalities.pop()
            return MODALITY_TEXT

        return MODALITY_TEXT

    # ── 统一入口 ────────────────────────────────────
    def process(self, input_data: Any,
                modality: Optional[str] = None) -> Dict[str, Any]:
        """多模态理解统一入口

        Args:
            input_data: 输入数据
                - 文本字符串
                - 文件路径字符串（图像/音频/视频）
                - 字典（混合输入，含 text/image_path/audio_path/video_path）
                - 列表/元组（混合输入）
            modality: 显式指定模态，None 时自动检测

        Returns:
            统一格式的理解结果
        """
        if modality is None:
            modality = self.detect_modality(input_data)

        logger.info(f"多模态处理: modality={modality}")

        # 缓存命中
        cache_key = self._cache_key(input_data, modality)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            cached["cached"] = True
            logger.debug(f"缓存命中: {cache_key}")
            return cached

        try:
            if modality == MODALITY_TEXT:
                result = self._process_text(input_data)
            elif modality == MODALITY_IMAGE:
                result = self._image_u.understand(input_data)
            elif modality == MODALITY_AUDIO:
                result = self._audio_u.understand(input_data)
            elif modality == MODALITY_VIDEO:
                result = self._video_u.understand(input_data)
            elif modality == MODALITY_MIXED:
                result = self._process_mixed(input_data)
            else:
                result = {"error": f"未知模态: {modality}", "modality": modality}

            result["modality"] = modality
            result["processed_at"] = datetime.now().isoformat()

            # 写入缓存
            self._cache[cache_key] = result
            if len(self._cache) > 500:  # 防止缓存膨胀
                # 删除最早的 1/4（按插入顺序，Python3.7+ dict 有序）
                keys = list(self._cache.keys())
                for k in keys[:len(keys)//4]:
                    self._cache.pop(k, None)
            self._save_cache()
            return result
        except Exception as e:
            logger.exception(f"多模态处理失败: {e}")
            return {"error": str(e), "modality": modality}

    # ── 文本处理 ────────────────────────────────────
    def _process_text(self, input_data: Any) -> Dict[str, Any]:
        """文本理解（轻量级，仅做基础统计）"""
        text = input_data if isinstance(input_data, str) else str(input_data)
        return {
            "modality": MODALITY_TEXT,
            "text": text,
            "length": len(text),
            "char_count": len(text),
            "word_count": len(text.split()),
            "line_count": text.count("\n") + 1 if text else 0,
            "description": f"文本输入，共{len(text)}字符",
        }

    # ── 混合处理 ────────────────────────────────────
    def _process_mixed(self, input_data: Any) -> Dict[str, Any]:
        """混合模态处理：分别处理后融合"""
        parts: Dict[str, Any] = {}
        descriptions: List[str] = []

        if isinstance(input_data, dict):
            # 文本部分
            text = input_data.get("text", "")
            if text:
                parts["text"] = self._process_text(text)
                descriptions.append(f"文本：{text[:60]}")
            # 图像部分
            img_path = input_data.get("image") or input_data.get("image_path")
            if img_path:
                parts["image"] = self._image_u.understand(img_path)
                if parts["image"].get("description"):
                    descriptions.append(f"图像：{parts['image']['description']}")
            # 音频部分
            aud_path = input_data.get("audio") or input_data.get("audio_path")
            if aud_path:
                parts["audio"] = self._audio_u.understand(aud_path)
                desc = parts["audio"].get("description") or \
                    parts["audio"].get("transcript", "")
                if desc:
                    descriptions.append(f"音频：{desc}")
            # 视频部分
            vid_path = input_data.get("video") or input_data.get("video_path")
            if vid_path:
                parts["video"] = self._video_u.understand(vid_path)
                if parts["video"].get("summary"):
                    descriptions.append(f"视频：{parts['video']['summary']}")

        elif isinstance(input_data, (list, tuple)):
            for i, item in enumerate(input_data):
                sub_modality = self.detect_modality(item)
                if sub_modality == MODALITY_MIXED:
                    sub_result = self._process_mixed(item)
                    parts[f"item_{i}"] = sub_result
                    if sub_result.get("fused_description"):
                        descriptions.append(sub_result["fused_description"])
                else:
                    sub_result = self.process(item, sub_modality)
                    parts[f"item_{i}_{sub_modality}"] = sub_result
                    desc = sub_result.get("description") or \
                        sub_result.get("summary") or \
                        sub_result.get("transcript", "")
                    if desc:
                        descriptions.append(f"{sub_modality}：{desc}")

        # 融合描述
        fused = "多模态融合结果：\n" + "\n".join(descriptions) if descriptions \
            else "未提取到有效内容"
        return {
            "modality": MODALITY_MIXED,
            "parts": parts,
            "fused_description": fused,
            "description": fused,
        }


# =====================================================================
# 单例
# =====================================================================
_processor_instance: Optional[MultimodalProcessor] = None


def get_multimodal_processor() -> MultimodalProcessor:
    """获取多模态处理器单例"""
    global _processor_instance
    if _processor_instance is None:
        _processor_instance = MultimodalProcessor()
    return _processor_instance
