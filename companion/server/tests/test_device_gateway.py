"""DeviceGateway（`/device/ws` 核心逻辑）行为测试。

用 FakeChannel 代替 Starlette WebSocket，覆盖握手、降级、会话替换、
action.result 关联、device.report 落库与断开清理。真实 WS 适配层由
`routers/ws.py` 负责，不在本文件测试。
"""

import asyncio

import pytest
from sqlalchemy import delete

from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.device import DeviceModel
from companion.server.app.services.device_gateway import DeviceGateway
from companion.server.app.services.device_registry import (
    get_device_link,
    reset_device_links,
    unregister_device_link,
)
from companion.server.app.services.events import EventBus
from companion.shared.catalog import get_catalog

DEVICE_ID = "dev-gateway-test"
TOKEN_HASH = "b" * 64


class FakeChannel:
    """最小 WebSocket 抽象：可以注入上行消息，记录下行与关闭码。"""

    def __init__(self):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self.sent_event = asyncio.Event()

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
        self.sent_event.set()

    async def receive(self):
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed is None:
            self.closed = (code, reason)
        # 模拟真实 WebSocket：关闭后阻塞中的 receive 立即返回 None。
        self.incoming.put_nowait(None)

    def push(self, message) -> None:
        self.incoming.put_nowait(message)

    async def wait_sent(self, predicate, timeout: float = 2.0) -> dict:
        async def _poll():
            while True:
                for message in self.sent:
                    if predicate(message):
                        return message
                self.sent_event.clear()
                await self.sent_event.wait()

        return await asyncio.wait_for(_poll(), timeout)


async def wait_until(predicate, timeout: float = 2.0):
    async def _poll():
        while True:
            result = predicate()
            if result:
                return result
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout)


async def wait_status(link, key: str, value, timeout: float = 2.0) -> dict:
    async def _poll():
        while True:
            status = await link.get_status()
            if status.get(key) == value:
                return status
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout)


def hello(**overrides) -> dict:
    payload = {
        "v": 2,
        "type": "hello",
        "device_id": DEVICE_ID,
        "firmware_version": "2.0.0",
        "action_catalog_version": get_catalog()["version"],
        "capabilities": ["action", "volume", "expression"],
        "audio": {
            "uplink": {"codec": "opus", "sample_rate": 16000, "channels": 1, "frame_ms": 60},
            "downlink": {"codec": "opus", "sample_rate": 24000, "channels": 1, "frame_ms": 60},
        },
        "ts": 1764000000000,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
async def seeded_device():
    async with AsyncSessionLocal() as session:
        session.add(
            DeviceModel(
                id=DEVICE_ID,
                name="网关测试机",
                token_hash=TOKEN_HASH,
                protocol_version=2,
                capabilities_json=["action"],
                volume=65,
                idle_enabled=True,
                pan_offset=1.0,
                tilt_offset=-1.0,
            )
        )
        await session.commit()
    yield DEVICE_ID
    async with AsyncSessionLocal() as session:
        await session.execute(delete(DeviceModel).where(DeviceModel.id == DEVICE_ID))
        await session.commit()


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_device_links()
    yield
    reset_device_links()


class Harness:
    def __init__(self):
        self.bus = EventBus()
        self.events: list[dict] = []

        async def collect(event: dict) -> None:
            self.events.append(event)

        self.bus.subscribe(collect)
        self.gateway = DeviceGateway(self.bus)

    async def start(self, channel: FakeChannel) -> asyncio.Task:
        return asyncio.create_task(self.gateway.handle(DEVICE_ID, channel))

    async def connected(self, **overrides) -> tuple[asyncio.Task, FakeChannel, dict]:
        channel = FakeChannel()
        task = await self.start(channel)
        channel.push(hello(**overrides))
        ack = await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
        return task, channel, ack


async def _stop(task: asyncio.Task, channel: FakeChannel) -> None:
    channel.push(None)
    await asyncio.wait_for(task, timeout=2.0)


async def test_hello_v2_registers_link_and_ack_contains_server_state(seeded_device):
    harness = Harness()
    task, channel, ack = await harness.connected()

    assert ack["v"] == 2
    assert ack["protocol_version"] == 2
    assert ack["capabilities"] == ["action", "volume", "expression"]
    assert ack["action_catalog_version"] == get_catalog()["version"]
    assert ack["warnings"] == []
    assert ack["state"] == {
        "volume": 65,
        "idle_enabled": True,
        "pan_offset": 1.0,
        "tilt_offset": -1.0,
    }
    assert ack["limits"]["pan"] == [-90.0, 90.0]
    assert ack["limits"]["tilt"] == [-20.0, 45.0]
    assert ack["limits"]["rate_limit_per_second"] == 10
    assert ack["audio"]["uplink"]["sample_rate"] == 16000
    assert ack["session_id"]
    assert get_device_link(DEVICE_ID) is not None

    async with AsyncSessionLocal() as session:
        device = await session.get(DeviceModel, DEVICE_ID)
        assert device.firmware_version == "2.0.0"
        assert device.protocol_version == 2
        assert device.capabilities_json == ["action", "volume", "expression"]
        assert device.last_seen_at is not None

    await _stop(task, channel)
    assert get_device_link(DEVICE_ID) is None


async def test_hello_timeout_closes_4408(seeded_device, monkeypatch):
    monkeypatch.setattr(settings, "DEVICE_HELLO_TIMEOUT_SECONDS", 0.05)
    harness = Harness()
    channel = FakeChannel()
    task = await harness.start(channel)

    await asyncio.wait_for(task, timeout=2.0)
    assert channel.closed == (4408, "hello_timeout")
    assert harness.events[-1]["type"] == "device.status"
    assert harness.events[-1]["online"] is False


async def test_v1_hello_degrades_without_capabilities(seeded_device):
    harness = Harness()
    task, channel, ack = await harness.connected(
        v=1, capabilities=None, action_catalog_version=None
    )

    assert ack["protocol_version"] == 1
    assert ack["capabilities"] == []
    assert "action_catalog_mismatch" not in ack["warnings"]

    async with AsyncSessionLocal() as session:
        device = await session.get(DeviceModel, DEVICE_ID)
        assert device.protocol_version == 1
        assert device.capabilities_json == []

    await _stop(task, channel)


async def test_catalog_version_mismatch_produces_warning(seeded_device):
    harness = Harness()
    task, channel, ack = await harness.connected(action_catalog_version="1.0.0")
    assert ack["warnings"] == ["action_catalog_mismatch"]
    await _stop(task, channel)


async def test_hello_device_id_mismatch_closes_4403(seeded_device):
    harness = Harness()
    channel = FakeChannel()
    task = await harness.start(channel)
    channel.push(hello(device_id="another-device"))

    await asyncio.wait_for(task, timeout=2.0)
    assert channel.closed == (4403, "device_mismatch")
    assert get_device_link(DEVICE_ID) is None


async def test_first_message_not_hello_closes_4408(seeded_device):
    harness = Harness()
    channel = FakeChannel()
    task = await harness.start(channel)
    channel.push({"type": "device.report", "volume": 10})

    await asyncio.wait_for(task, timeout=2.0)
    assert channel.closed == (4408, "invalid_hello")


async def test_action_result_resolves_pending_command(seeded_device):
    harness = Harness()
    task, channel, ack = await harness.connected()
    link = get_device_link(DEVICE_ID)

    send_task = asyncio.create_task(
        link.send(
            {
                "type": "action.play",
                "action_id": "act-1",
                "name": "nod",
                "intensity": 2,
                "speed": 2,
                "repeat": 1,
                "source": "llm",
            }
        )
    )
    command = await channel.wait_sent(lambda m: m.get("type") == "action.play")
    assert command["v"] == 2
    assert command["session_id"] == ack["session_id"]
    assert command["ts"] > 0

    channel.push(
        {"v": 2, "type": "action.result", "action_id": "act-1", "status": "done", "detail": ""}
    )
    result = await asyncio.wait_for(send_task, timeout=2.0)
    assert result.status == "done"

    event = await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "action.result"), None)
    )
    assert event["action_id"] == "act-1"
    assert event["source"] == "llm"

    await _stop(task, channel)


async def test_settled_action_result_is_not_rebroadcast(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    link = get_device_link(DEVICE_ID)

    send_task = asyncio.create_task(
        link.send(
            {
                "type": "action.play",
                "action_id": "act-dup",
                "name": "nod",
                "source": "app",
            }
        )
    )
    await channel.wait_sent(lambda m: m.get("action_id") == "act-dup")
    channel.push(
        {"v": 2, "type": "action.result", "action_id": "act-dup", "status": "done"}
    )
    assert (await asyncio.wait_for(send_task, timeout=2.0)).status == "done"
    await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "action.result"), None)
    )

    # 服务端已结算（例如超时后急停），设备迟到的同一 action_id 回执不得二次广播。
    channel.push(
        {
            "v": 2,
            "type": "action.result",
            "action_id": "act-dup",
            "status": "failed",
            "detail": "stopped",
        }
    )
    channel.push({"v": 2, "type": "device.report", "volume": 66})
    await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "device.report"), None)
    )

    results = [e for e in harness.events if e["type"] == "action.result"]
    assert len(results) == 1
    assert results[0]["status"] == "done"

    await _stop(task, channel)


async def test_unknown_action_result_is_not_broadcast(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()

    channel.push(
        {"v": 2, "type": "action.result", "action_id": "never-sent", "status": "done"}
    )
    channel.push({"v": 2, "type": "device.report", "volume": 67})
    await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "device.report"), None)
    )

    assert not [e for e in harness.events if e["type"] == "action.result"]

    await _stop(task, channel)


async def test_last_report_is_persisted_on_disconnect(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()

    channel.push(
        {"v": 2, "type": "device.report", "volume": 23, "state": "speaking"}
    )
    channel.push(None)
    await asyncio.wait_for(task, timeout=2.0)

    async with AsyncSessionLocal() as session:
        device = await session.get(DeviceModel, DEVICE_ID)
        assert device.volume == 23
        assert device.state == "speaking"
        assert device.last_report_json["volume"] == 23


async def test_device_report_updates_snapshot_and_publishes_event(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    link = get_device_link(DEVICE_ID)

    channel.push(
        {
            "v": 2,
            "type": "device.report",
            "volume": 52,
            "pan": -12.5,
            "tilt": 3.0,
            "idle": False,
            "state": "speaking",
            "pan_offset": 2.0,
            "tilt_offset": -2.0,
        }
    )

    status = await wait_status(link, "volume", 52)
    assert status["pan"] == -12.5
    assert status["tilt"] == 3.0
    assert status["idle_enabled"] is False
    assert status["state"] == "speaking"

    event = await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "device.report"), None)
    )
    assert event["volume"] == 52
    assert event["device_id"] == DEVICE_ID

    async with AsyncSessionLocal() as session:
        device = await session.get(DeviceModel, DEVICE_ID)
        assert device.volume == 52
        assert device.idle_enabled is False
        assert device.state == "speaking"
        assert device.last_report_json["pan"] == -12.5
        assert device.pan_offset == 2.0

    await _stop(task, channel)


async def test_disconnect_fails_pending_commands_and_unregisters(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    link = get_device_link(DEVICE_ID)

    send_task = asyncio.create_task(
        link.send({"type": "action.play", "action_id": "act-2", "name": "nod"})
    )
    await channel.wait_sent(lambda m: m.get("type") == "action.play")

    channel.push(None)
    result = await asyncio.wait_for(send_task, timeout=2.0)
    assert result.status == "failed"
    assert result.detail == "device_offline"

    await asyncio.wait_for(task, timeout=2.0)
    assert get_device_link(DEVICE_ID) is None
    offline = harness.events[-1]
    assert offline["type"] == "device.status"
    assert offline["online"] is False


async def test_second_connection_replaces_first(seeded_device):
    harness = Harness()
    task_a, channel_a, _ = await harness.connected()
    task_b, channel_b, _ = await harness.connected()

    assert channel_a.closed == (4409, "replaced")
    assert get_device_link(DEVICE_ID) is not None

    await asyncio.wait_for(task_a, timeout=2.0)
    # 旧连接退出不得注销新连接
    assert get_device_link(DEVICE_ID) is not None

    await _stop(task_b, channel_b)
    assert get_device_link(DEVICE_ID) is None


async def test_stop_does_not_wait_for_result_and_resolves_pending(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    link = get_device_link(DEVICE_ID)

    send_task = asyncio.create_task(
        link.send({"type": "action.play", "action_id": "act-3", "name": "dance"})
    )
    await channel.wait_sent(lambda m: m.get("type") == "action.play")

    stop_result = await asyncio.wait_for(
        link.send({"type": "action.stop", "reason": "user_stop"}), timeout=2.0
    )
    assert stop_result.status == "done"
    stop_command = await channel.wait_sent(lambda m: m.get("type") == "action.stop")
    assert stop_command["reason"] == "user_stop"

    result = await asyncio.wait_for(send_task, timeout=2.0)
    assert result.status == "failed"
    assert result.detail == "stopped"

    await _stop(task, channel)


async def test_unknown_and_binary_messages_are_ignored(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()

    channel.push({"v": 2, "type": "totally.unknown", "foo": 1})
    channel.push(b"\xa1\x01\x01\x00\x00\x00\x00\x01\x00\x00\x00\x00payload")
    channel.push(
        {"v": 2, "type": "device.report", "volume": 33, "state": "idle"}
    )

    link = get_device_link(DEVICE_ID)
    await wait_status(link, "volume", 33)
    assert get_device_link(DEVICE_ID) is not None

    await _stop(task, channel)


async def test_unregister_with_other_link_keeps_current(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    current = get_device_link(DEVICE_ID)
    unregister_device_link(DEVICE_ID, link=object())
    assert get_device_link(DEVICE_ID) is current
    await _stop(task, channel)


async def test_device_report_event_uses_protocol_idle_field(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()

    channel.push(
        {
            "v": 2,
            "type": "device.report",
            "volume": 41,
            "pan": 200,
            "tilt": -100,
            "idle": False,
            "state": "idle",
            "ts": 1,
        }
    )

    event = await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "device.report"), None)
    )
    # 对外用协议字段 `idle`，并且边界值必须被夹紧。
    assert event["idle"] is False
    assert "idle_enabled" not in event
    assert event["pan"] == 90.0
    assert event["tilt"] == -20.0
    assert isinstance(event["ts"], int) and event["ts"] > 0

    await _stop(task, channel)


async def test_v1_session_rejects_v2_control_messages(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected(v=1, capabilities=None)

    link = get_device_link(DEVICE_ID)
    result = await link.send(
        {"v": 2, "type": "action.play", "action_id": "v1-act", "name": "nod"}
    )
    assert result.status == "failed"
    assert result.detail == "unsupported_protocol"
    assert not any(m.get("type") == "action.play" for m in channel.sent)

    await _stop(task, channel)
