"""管理员口令哈希。

只使用标准库 PBKDF2-HMAC-SHA256，避免为一个口令引入额外原生依赖。
存储格式：`pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>`。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 260_000


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    if not password:
        raise ValueError("password 不能为空")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return f"{ALGORITHM}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验口令；任何格式异常都视为不匹配，不抛异常。"""
    if not password or not stored:
        return False
    try:
        algorithm, iterations_raw, salt, digest_hex = stored.split("$")
        iterations = int(iterations_raw)
    except (ValueError, AttributeError):
        return False
    if algorithm != ALGORITHM or iterations <= 0:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), iterations
    )
    return hmac.compare_digest(candidate.hex(), digest_hex)


def needs_rehash(stored: str, *, iterations: int = DEFAULT_ITERATIONS) -> bool:
    """判断现有哈希是否使用了更弱的迭代次数，便于登录成功后平滑升级。"""
    try:
        algorithm, iterations_raw, _, _ = stored.split("$")
        return algorithm != ALGORITHM or int(iterations_raw) < iterations
    except (ValueError, AttributeError):
        return True
