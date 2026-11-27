"""上传体积的早退保护。

FastAPI 会先读完整个请求体（multipart 还会落到临时文件）再进入路由函数，
所以路由里的“边读边计数”只能限制内存，不能阻止超大文件先写满磁盘。
这里用纯 ASGI 中间件在请求体进入 Starlette 之前限制累计字节数：

- 有 `Content-Length` 时直接拒绝明显超限的请求（最快）；
- 没有长度（App 的流式上传就是 `Transfer-Encoding: chunked`）时，包装
  `receive` 边读边计数，超过上限立即中止并回 413，最多只让上限大小的数据
  进入临时文件。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from starlette.responses import JSONResponse

# multipart 的 boundary、part 头等包装开销；只用来判断“明显超限”，
# 不能把恰好等于上限的请求误杀。
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def declared_length_exceeds(
    headers: Mapping[bytes, bytes] | None,
    limit: int,
) -> bool:
    """`Content-Length` 声明值是否超过 `limit`（含 multipart 余量）。"""
    if not headers:
        return False
    raw = headers.get(b"content-length")
    if raw is None:
        return False
    try:
        declared = int(raw)
    except (TypeError, ValueError):
        return False
    return declared > limit + MULTIPART_OVERHEAD_BYTES


class _PayloadTooLarge(BaseException):
    """内部信号：累计读取超过上限，由中间件转成 413。

    必须继承 `BaseException` 而不是 `Exception`：FastAPI 在解析请求体时
    `except Exception` 会把任何普通异常改写成 400「请求体解析失败」，
    我们的 413 就会丢失；Starlette 的 multipart 解析器对 `BaseException`
    只做清理后重新抛出。
    """


class ContentLengthLimitMiddleware:
    """按路径限制上传体积；超限直接 413，不再读取请求体。

    `rules` 的 value 是 `(limit_getter, detail)`：用 getter 而不是固定值，
    这样测试 monkeypatch `settings` 后也能生效。
    """

    def __init__(
        self,
        app,
        rules: dict[str, tuple[Callable[[], int], str]],
    ) -> None:
        self.app = app
        self.rules = rules

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        rule = self.rules.get(scope.get("path", ""))
        if rule is None:
            await self.app(scope, receive, send)
            return

        limit_getter, detail = rule
        limit = int(limit_getter())
        headers = {
            key.lower(): value for key, value in (scope.get("headers") or [])
        }
        if declared_length_exceeds(headers, limit):
            await self._reject(scope, receive, send, detail)
            return

        # 无 Content-Length（chunked）时按实际字节计数；余量用于 multipart 包装。
        budget = limit + MULTIPART_OVERHEAD_BYTES
        received = 0

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > budget:
                    raise _PayloadTooLarge
            return message

        started = False

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _PayloadTooLarge:
            if started:
                # 响应已开始（理论上不会在读 body 前发生）：只能让连接断开。
                raise
            await self._reject(scope, receive, send, detail)

    @staticmethod
    async def _reject(scope, receive, send, detail: str) -> None:
        response = JSONResponse({"detail": detail}, status_code=413)
        await response(scope, receive, send)
