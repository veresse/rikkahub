"""`/device/ws` 路由外壳测试：Token 鉴权、关闭码与 Starlette 适配。

不启动真实 HTTP 服务器，用 FakeWebSocket 模拟 Starlette 消息格式。
"""

import asyncio

import pytest
from sqlalchemy import delete

from companion.server.app.database import AsyncSessionLocal
from companion.server.app.config import settings
from companion.server.app.models.device import DeviceModel
from companion.server.app.routers.ws import StarletteChannel, serve_websocket
from companion.server.app.security.tokens import hash_token
from companion.server.app.services.device_gateway import DeviceGateway
from companion.server.app.services.device_registry import reset_device_links
from companion.shared.catalog import get_catalog

DEVICE_TOKEN = "router-device-token"
DEVICE_ID = "dev-ws-router"


class FakeWebSocket:
    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = headers or {}
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent_event = asyncio.Event()

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed is None:
            self.closed = (code, reason)
        self.incoming.put_nowait({"type": "websocket.disconnect", "code": code})

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
        self.sent_event.set()

    async def receive(self):
        return await self.incoming.get()

    def push_text(self, text: str) -> None:
        self.incoming.put_nowait({"type": "websocket.receive", "text": text})

    def push_bytes(self, data: bytes) -> None:
        self.incoming.put_nowait({"type": "websocket.receive", "bytes": data})

    async def wait_sent(self, predicate, timeout: float = 2.0) -> dict:
        async def _poll():
            while True:
                for message in self.sent:
                    if predicate(message):
                        return message
                self.sent_event.clear()
                await self.sent_event.wait()

        return await asyncio.wait_for(_poll(), timeout)


@pytest.fixture
async def seeded_device():
    async with AsyncSessionLocal() as session:
        session.add(
            DeviceModel(
                id=DEVICE_ID,
                name="路由测试机",
                token_hash=hash_token(DEVICE_TOKEN),
            )
        )
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(delete(DeviceModel).where(DeviceModel.id == DEVICE_ID))
        await session.commit()


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_device_links()
    yield
    reset_device_links()


async def test_missing_token_is_rejected_after_accept():
    ws = FakeWebSocket()
    await serve_websocket(ws, expected="device", gateway=DeviceGateway())
    assert ws.accepted is True
    assert ws.closed == (4401, "unauthorized")
    assert ws.sent == []


async def test_app_token_on_device_route_is_4403(monkeypatch):
    from companion.server.app.security import ws_auth

    async def wrong_type(db, raw, *, expected):
        assert expected == "device"
        return ws_auth.WSAuthResult(ok=False, close_code=4403, reason="wrong_token_type")

    monkeypatch.setattr("companion.server.app.routers.ws.authenticate_ws", wrong_type)
    ws = FakeWebSocket(headers={"authorization": "Bearer some-app-token"})
    await serve_websocket(ws, expected="device", gateway=DeviceGateway())
    assert ws.closed == (4403, "wrong_token_type")


async def test_valid_device_handshake_over_starlette_adapter(seeded_device):
    ws = FakeWebSocket(headers={"authorization": f"Bearer {DEVICE_TOKEN}"})
    task = asyncio.create_task(
        serve_websocket(ws, expected="device", gateway=DeviceGateway())
    )
    assert await _wait_accepted(ws)

    ws.push_text(
        '{"v": 2, "type": "hello", "device_id": "%s", "capabilities": ["action"], '
        '"action_catalog_version": "%s"}' % (DEVICE_ID, get_catalog()["version"])
    )
    ack = await ws.wait_sent(lambda m: m.get("type") == "hello_ack")
    assert ack["protocol_version"] == 2
    assert ack["capabilities"] == ["action"]

    ws.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(task, timeout=2.0)


async def test_starlette_channel_translates_messages():
    ws = FakeWebSocket()
    channel = StarletteChannel(ws)

    ws.push_text('{"type": "device.report", "volume": 10}')
    assert await channel.receive() == {"type": "device.report", "volume": 10}

    ws.push_bytes(b"\xa1\x01")
    assert await channel.receive() == b"\xa1\x01"

    ws.push_text("not-json")
    assert await channel.receive() == {}

    ws.incoming.put_nowait({"type": "websocket.disconnect", "code": 1006})
    assert await channel.receive() is None

    await channel.send_json({"ok": True})
    assert ws.sent == [{"ok": True}]


async def test_starlette_channel_rejects_oversized_text(monkeypatch):
    monkeypatch.setattr(settings, "WS_MAX_MESSAGE_BYTES", 8)
    ws = FakeWebSocket()
    channel = StarletteChannel(ws)

    ws.push_text('{"x": "definitely too long"}')
    assert await channel.receive() is None
    assert ws.closed == (4429, "message_too_large")


async def test_starlette_channel_rejects_oversized_bytes(monkeypatch):
    monkeypatch.setattr(settings, "WS_MAX_MESSAGE_BYTES", 8)
    ws = FakeWebSocket()
    channel = StarletteChannel(ws)

    ws.push_bytes(b"\x00" * 9)
    assert await channel.receive() is None
    assert ws.closed == (4429, "message_too_large")


def test_ws_transport_limit_invariant_is_enforced():
    from pydantic import ValidationError

    from companion.server.app.config import Settings

    valid = Settings(WS_MAX_MESSAGE_BYTES=1024, WS_TRANSPORT_MAX_MESSAGE_BYTES=2048)
    assert valid.WS_TRANSPORT_MAX_MESSAGE_BYTES > valid.WS_MAX_MESSAGE_BYTES

    with pytest.raises(ValidationError):
        Settings(WS_MAX_MESSAGE_BYTES=2048, WS_TRANSPORT_MAX_MESSAGE_BYTES=1024)


async def _wait_accepted(ws: FakeWebSocket, timeout: float = 2.0) -> bool:
    async def _poll():
        while not ws.accepted:
            await asyncio.sleep(0.01)
        return True

    return await asyncio.wait_for(_poll(), timeout)
