"""统一毫秒时间戳，避免各模块各自实现。"""

import time


def now_ms() -> int:
    return int(time.time() * 1000)
