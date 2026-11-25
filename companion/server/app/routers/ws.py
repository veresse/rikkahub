"""WebSocket 路由：`/device/ws`（`/app/ws` 在 Block D 的 AppGateway 部分接入）。

鉴权、协议处理与业务逻辑分别在 `security/ws_auth.py`、
`services/device_gateway.py` 中实现；本文件只做 Starlette 适配。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket

from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.security.ws_auth import authenticate_ws, parse_bearer_header
from companion.server.app.services.device_gateway import DeviceGateway

logger = logging.getLogger(__name__)

router = APIRouter()
device_gateway = DeviceGateway()


class StarletteChannel:
    """把 Starlette WebSocket 适配成网关使用的 `WSChannel`。"""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self._send_lock = asyncio.Lock()

    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_json(payload)

    async def receive(self) -> dict[str, Any] | bytes | None:
        message = await self.websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return None
        if message.get("bytes") is not None:
            return message["bytes"]
        text = message.get("text") or ""
        if len(text.encode("utf-8")) > settings.WS_MAX_MESSAGE_BYTES:
            logger.warning("websocket message too large, closing")
            await self.close(4429, "message_too_large")
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def close(self, code: int = 1000, reason: str = "") -> None:
        try:
            await self.websocket.close(code=code, reason=reason)
        except Exception:
            logger.debug("websocket close failed", exc_info=True)


async def serve_websocket(
    websocket: WebSocket,
    *,
    expected: str,
    gateway,
    session_factory=AsyncSessionLocal,
) -> None:
    """握手鉴权后再交给对应的网关。

    先 `accept` 再鉴权，才能把 4401/4403 这类自定义关闭码真正发给客户端；
    不接受任何业务消息。
    """
    raw_token = parse_bearer_header(websocket.headers.get("authorization"))
    await websocket.accept()

    async with session_factory() as db:
        auth = await authenticate_ws(db, raw_token, expected=expected)

    if not auth.ok:
        await websocket.close(code=auth.close_code, reason=auth.reason)
        return

    channel = StarletteChannel(websocket)
    if expected == "device":
        await gateway.handle(auth.device_id, channel)
    else:
        await gateway.handle(auth.token_id, channel)


@router.websocket("/device/ws")
async def device_ws(websocket: WebSocket) -> None:
    await serve_websocket(websocket, expected="device", gateway=device_gateway)
