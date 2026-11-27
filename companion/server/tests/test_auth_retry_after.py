"""登录限速 429 必须带 Retry-After（App 用它做倒计时）。"""

from companion.server.app.config import settings
from companion.server.app.security.rate_limit import record, retry_after_seconds


def test_retry_after_zero_when_empty():
    assert retry_after_seconds("login:empty", 300, now=1000.0) == 0


def test_retry_after_counts_down_to_oldest_expiry():
    key = "login:countdown"
    for moment in (1000.0, 1001.0, 1002.0):
        record(key, 300, now=moment)

    assert retry_after_seconds(key, 300, now=1000.0) == 300
    assert retry_after_seconds(key, 300, now=1100.0) == 200


def test_retry_after_never_exceeds_window():
    key = "login:window"
    record(key, 60, now=10.0)

    assert retry_after_seconds(key, 60, now=10.0) == 60


async def test_login_429_carries_retry_after(client, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT", 1)
    monkeypatch.setattr(settings, "LOGIN_RATE_WINDOW_SECONDS", 300)
    bad = {"username": "admin", "password": "wrong"}

    first = await client.post("/api/auth/login", json=bad)
    assert first.status_code == 401

    second = await client.post("/api/auth/login", json=bad)
    assert second.status_code == 429
    header = second.headers.get("retry-after")
    assert header is not None
    assert 1 <= int(header) <= 300
