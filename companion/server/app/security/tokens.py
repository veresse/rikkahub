"""Token 生成与哈希。

- 明文 Token 只在签发时返回一次，服务端只保存 SHA-256 哈希。
- 使用 URL-safe 十六进制，方便放入 HTTP Header；禁止放入 URL Query。
"""

from __future__ import annotations

import hashlib
import secrets

TOKEN_BYTES = 32


def generate_token(nbytes: int = TOKEN_BYTES) -> str:
    """生成高熵 Token（默认 32 字节 = 64 个十六进制字符）。"""
    return secrets.token_hex(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
