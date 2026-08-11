# -*- coding: utf-8 -*-
"""
guard/data_crypto.py — 本地数据字段级加密（P2修复）
====================================================
对 scu2_data/ 下的敏感数据（对话内容、user_id）做字段级加密，
防止明文落盘后被直接读取。

策略：
  - 对称加密（AES-256-GCM，使用 cryptography 库；不可用时降级为 XOR+HMAC）
  - 密钥来源：环境变量 SCU2_DATA_KEY（32字节hex）；未配置则派生自机器标识+项目路径
  - 字段级加密：仅加密 user_id 和 content，结构字段（session_id/timestamp/role）保留明文以便查询
  - 向前兼容：读取时自动识别加密/明文格式
"""
import os
import hmac
import hashlib
import logging
from typing import Optional

logger = logging.getLogger("scu2.guard.crypto")

# 加密标记前缀（识别加密数据）
_ENC_PREFIX = "ENCv1:"

# 机器级密钥缓存
_cached_key: Optional[bytes] = None


def _derive_key() -> bytes:
    """派生加密密钥（32字节）

    优先级：
      1. 环境变量 SCU2_DATA_KEY（hex字符串，64字符=32字节）
      2. 基于机器标识+项目根目录派生（开发模式兜底）
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    env_key = os.environ.get("SCU2_DATA_KEY", "")
    if env_key:
        try:
            _cached_key = bytes.fromhex(env_key)
            if len(_cached_key) == 32:
                logger.info("数据加密密钥从 SCU2_DATA_KEY 加载")
                return _cached_key
        except ValueError:
            logger.warning("SCU2_DATA_KEY 非法hex，回退到派生密钥")

    # 兜底：基于项目路径+用户名派生（开发模式，不安全但比明文好）
    base = os.path.expanduser("~") + "|" + os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _cached_key = hashlib.sha256(("scu2_data_key::" + base).encode("utf-8")).digest()
    logger.warning("⚠️ 使用派生密钥（开发模式），生产环境请配置 SCU2_DATA_KEY 环境变量")
    return _cached_key


def _xor_encrypt(data: bytes, key: bytes) -> bytes:
    """XOR加密（cryptography库不可用时的降级方案）"""
    klen = len(key)
    return bytes(b ^ key[i % klen] for i, b in enumerate(data))


def _hmac_sign(data: bytes, key: bytes) -> bytes:
    """HMAC-SHA256签名（防篡改）"""
    return hmac.new(key, data, hashlib.sha256).digest()[:16]


def encrypt_field(plaintext: str) -> str:
    """加密字段值，返回带前缀的密文字符串

    Args:
        plaintext: 明文（user_id 或 content）

    Returns:
        "ENCv1:<hex>" 格式的密文，或空字符串/None的原样返回
    """
    if not plaintext or not isinstance(plaintext, str):
        return plaintext
    # 已加密的不重复加密
    if plaintext.startswith(_ENC_PREFIX):
        return plaintext

    try:
        key = _derive_key()
        data = plaintext.encode("utf-8")
        # 尝试使用 cryptography 库（AES-GCM）
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            import secrets as _secrets
            nonce = _secrets.token_bytes(12)
            aesgcm = AESGCM(key)
            ct = aesgcm.encrypt(nonce, data, None)
            return _ENC_PREFIX + (nonce + ct).hex()
        except ImportError:
            # 降级：XOR + HMAC签名
            enc = _xor_encrypt(data, key)
            sig = _hmac_sign(enc, key)
            return _ENC_PREFIX + "L:" + (sig + enc).hex()
    except Exception as e:
        logger.debug(f"加密失败(返回明文): {e}")
        return plaintext


def decrypt_field(ciphertext: str) -> str:
    """解密字段值

    Args:
        ciphertext: "ENCv1:<hex>" 格式的密文，或明文（向前兼容）

    Returns:
        明文字符串
    """
    if not ciphertext or not isinstance(ciphertext, str):
        return ciphertext
    if not ciphertext.startswith(_ENC_PREFIX):
        # 明文（向前兼容旧数据）
        return ciphertext

    try:
        key = _derive_key()
        payload = ciphertext[len(_ENC_PREFIX):]

        # 判断加密方案
        if payload.startswith("L:"):
            # 降级方案：XOR + HMAC
            raw = bytes.fromhex(payload[2:])
            sig, enc = raw[:16], raw[16:]
            expected_sig = _hmac_sign(enc, key)
            if not hmac.compare_digest(sig, expected_sig):
                logger.warning("字段HMAC校验失败（可能被篡改）")
                return "[DECRYPT_FAILED]"
            return _xor_encrypt(enc, key).decode("utf-8", errors="replace")
        else:
            # AES-GCM
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw = bytes.fromhex(payload)
            nonce, ct = raw[:12], raw[12:]
            aesgcm = AESGCM(key)
            return aesgcm.decrypt(nonce, ct, None).decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"解密失败: {e}")
        return "[DECRYPT_FAILED]"


def is_encrypted(value: str) -> bool:
    """判断值是否已加密"""
    return isinstance(value, str) and value.startswith(_ENC_PREFIX)
