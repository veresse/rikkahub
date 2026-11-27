"""进程内滑动窗口限速。

单 worker 部署（SQLite 只允许单写）下足够；多 worker 时需替换为 Redis 或
数据库计数。这里刻意保持零依赖，并暴露 `reset_rate_limits()` 供测试隔离。
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from threading import Lock

_LOCK = Lock()
_EVENTS: dict[str, deque[float]] = defaultdict(deque)


def reset_rate_limits() -> None:
    with _LOCK:
        _EVENTS.clear()


def _prune(bucket: deque[float], window_seconds: float, now: float) -> None:
    cutoff = now - window_seconds
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()


def current_count(key: str, window_seconds: float, *, now: float | None = None) -> int:
    current = time.monotonic() if now is None else now
    with _LOCK:
        bucket = _EVENTS.get(key)
        if not bucket:
            return 0
        _prune(bucket, window_seconds, current)
        return len(bucket)


def is_limited(key: str, limit: int, window_seconds: float, *, now: float | None = None) -> bool:
    return current_count(key, window_seconds, now=now) >= limit


def retry_after_seconds(
    key: str, window_seconds: float, *, now: float | None = None
) -> int:
    """窗口内最早一条记录过期还需多少秒；窗口为空返回 0。

    用于 `Retry-After`：客户端按这个值倒计时，归零后重试才可能成功。
    """
    current = time.monotonic() if now is None else now
    with _LOCK:
        bucket = _EVENTS.get(key)
        if not bucket:
            return 0
        _prune(bucket, window_seconds, current)
        if not bucket:
            return 0
        remaining = window_seconds - (current - bucket[0])
    return max(1, math.ceil(remaining))


def record(key: str, window_seconds: float, *, now: float | None = None) -> int:
    """记录一次事件并返回窗口内计数。"""
    current = time.monotonic() if now is None else now
    with _LOCK:
        bucket = _EVENTS[key]
        _prune(bucket, window_seconds, current)
        bucket.append(current)
        return len(bucket)


def clear(key: str) -> None:
    with _LOCK:
        _EVENTS.pop(key, None)
