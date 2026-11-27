"""服务端设置里密钥的对称加密。

密钥由 `SECRET_KEY` 经 HKDF-SHA256 派生，AES-GCM 提供机密性与完整性。
轮换 `SECRET_KEY` 会让旧密文无法解密：调用方按“未配置”处理，不崩溃、不回显。
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from companion.server.app.config import settings

_PREFIX = "v1:"
_SALT = b"companion-settings-v1"
_INFO = b"aes-gcm"
_NONCE_BYTES = 12


def _aead() -> AESGCM:
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        info=_INFO,
    ).derive(settings.SECRET_KEY.encode("utf-8"))
    return AESGCM(key)


def _aad(aad: str) -> bytes | None:
    # 把设置键名作为关联数据，密文无法在 deepseek/dashscope 两行之间互换。
    return aad.encode("utf-8") if aad else None


def encrypt_secret(plaintext: str, *, aad: str = "") -> str:
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = _aead().encrypt(nonce, plaintext.encode("utf-8"), _aad(aad))
    return _PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(value: str, *, aad: str = "") -> str:
    """解密 `v1:` 密文；任何格式/认证失败都抛 `ValueError`，异常里不含原文。"""
    if not value.startswith(_PREFIX):
        raise ValueError("unknown_secret_format")
    try:
        raw = base64.urlsafe_b64decode(value[len(_PREFIX) :].encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise ValueError("invalid_secret_encoding") from None
    if len(raw) <= _NONCE_BYTES:
        raise ValueError("truncated_secret")
    nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    try:
        return _aead().decrypt(nonce, ciphertext, _aad(aad)).decode("utf-8")
    except Exception:
        raise ValueError("secret_decrypt_failed") from None
