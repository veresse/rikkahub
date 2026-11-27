"""上传体积早退：在读取请求体之前按 Content-Length 拒绝。"""

from __future__ import annotations

import json

from companion.server.app.services.upload_limits import (
    MULTIPART_OVERHEAD_BYTES,
    ContentLengthLimitMiddleware,
    declared_length_exceeds,
)


def test_declared_length_exceeds_only_when_obviously_over():
    limit = 1000
    assert declared_length_exceeds({b"content-length": b"1000000"}, limit)
    # multipart 包装余量内不拒绝，交给路由的分块计数兜底。
    assert not declared_length_exceeds(
        {b"content-length": str(limit + MULTIPART_OVERHEAD_BYTES - 1).encode()},
        limit,
    )
    assert not declared_length_exceeds(None, limit)
    assert not declared_length_exceeds({}, limit)
    assert not declared_length_exceeds({b"content-length": b"not-a-number"}, limit)


async def _drive(middleware, scope):
    sent: list[dict] = []

    async def receive():
        raise AssertionError("middleware must not read the request body")

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


def _scope(path: str, content_length: bytes | None) -> dict:
    headers = [(b"content-type", b"multipart/form-data; boundary=x")]
    if content_length is not None:
        headers.append((b"content-length", content_length))
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
    }


async def test_oversized_upload_is_rejected_before_body_is_read():
    called = {"app": False}

    async def app(scope, receive, send):  # pragma: no cover - must not run
        called["app"] = True

    middleware = ContentLengthLimitMiddleware(
        app,
        {"/api/backup/preview": (lambda: 1000, "file_too_large")},
    )
    sent = await _drive(middleware, _scope("/api/backup/preview", b"9999999"))

    assert called["app"] is False
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {"detail": "file_too_large"}


async def test_asset_upload_uses_its_own_error_code():
    async def app(scope, receive, send):  # pragma: no cover - must not run
        raise AssertionError("must not run")

    middleware = ContentLengthLimitMiddleware(
        app,
        {"/api/assets/preview": (lambda: 1000, "archive_too_large")},
    )
    sent = await _drive(middleware, _scope("/api/assets/preview", b"9999999"))

    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {"detail": "archive_too_large"}


async def test_chunked_body_is_capped_before_spooling():
    """App 的流式上传没有 Content-Length；必须按实际字节早退。"""
    read_bytes = 0
    finished = {"value": False}

    async def app(scope, receive, send):
        nonlocal read_bytes
        while True:
            message = await receive()
            read_bytes += len(message.get("body", b""))
            if not message.get("more_body", False):
                break
        finished["value"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = ContentLengthLimitMiddleware(
        app,
        {"/api/assets/preview": (lambda: 1000, "archive_too_large")},
    )

    payload = b"x" * 400
    chunks = [payload] * 200  # 80 KB，超过 1000 + 64 KiB 余量

    async def receive():
        if not chunks:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(_scope("/api/assets/preview", None), receive, send)

    assert finished["value"] is False
    # 超过上限的那一块已经被读入，最多多出一个 chunk。
    assert read_bytes <= 1000 + MULTIPART_OVERHEAD_BYTES + len(payload)
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {"detail": "archive_too_large"}


async def test_chunked_body_under_limit_passes_through():
    async def app(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = ContentLengthLimitMiddleware(
        app,
        {"/api/assets/preview": (lambda: 1000, "archive_too_large")},
    )

    async def receive():
        return {"type": "http.request", "body": b"tiny", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(_scope("/api/assets/preview", None), receive, send)
    assert sent[0]["status"] == 200


async def test_small_or_unrelated_requests_pass_through():
    seen: list[str] = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = ContentLengthLimitMiddleware(
        app,
        {"/api/backup/preview": (lambda: 1000, "file_too_large")},
    )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    await middleware(_scope("/api/backup/preview", b"1001"), receive, send)
    await middleware(_scope("/api/backup/preview", None), receive, send)
    await middleware(_scope("/api/assistants", b"9999999"), receive, send)

    assert seen == [
        "/api/backup/preview",
        "/api/backup/preview",
        "/api/assistants",
    ]
