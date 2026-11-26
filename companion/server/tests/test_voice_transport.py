"""音频传输与 VoiceSession 帧门控测试。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import delete

from companion.server.app.audio.framing import (
    FLAG_LAST,
    FLAG_SILENCE,
    KIND_DOWNLINK,
    KIND_UPLINK,
    AudioFrame,
)
from companion.server.app.audio.pipeline import VoiceSession
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.device import DeviceModel
from companion.server.app.services.device_gateway import DeviceGateway
from companion.server.app.services.device_registry import (
    get_device_link,
    reset_device_links,
)
from companion.shared.catalog import get_catalog

DEVICE_ID = "dev-voice-transport"
TOKEN_HASH = "c" * 64


class VoiceChannel:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.sent_bytes: list[bytes] = []
        self.closed: tuple[int, str] | None = None
        self.sent_event = asyncio.Event()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        self.sent_event.set()

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)
        self.sent_event.set()

    async def receive(self):
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed is None:
            self.closed = (code, reason)
        self.incoming.put_nowait(None)

    def push(self, message) -> None:
        self.incoming.put_nowait(message)

    async def wait_sent(self, predicate, timeout: float = 2.0) -> dict[str, Any]:
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
            if predicate():
                return True
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_device_links()
    yield
    reset_device_links()


@pytest.fixture
async def seeded_device():
    async with AsyncSessionLocal() as db:
        db.add(DeviceModel(id=DEVICE_ID, name="语音传输测试机", token_hash=TOKEN_HASH))
        await db.commit()
    yield
    async with AsyncSessionLocal() as db:
        await db.execute(delete(DeviceModel).where(DeviceModel.id == DEVICE_ID))
        await db.commit()


async def _hello() -> dict[str, Any]:
    return {
        "v": 2,
        "type": "hello",
        "device_id": DEVICE_ID,
        "capabilities": ["action"],
        "action_catalog_version": get_catalog()["version"],
    }


@pytest.fixture
async def gateway_session(seeded_device):
    gateway = DeviceGateway()
    channel = VoiceChannel()
    task = asyncio.create_task(gateway.handle(DEVICE_ID, channel))
    channel.push(await _hello())
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    session = get_device_link(DEVICE_ID)
    assert session is not None
    await wait_until(lambda: session.ready)
    try:
        yield session, channel
    finally:
        channel.push(None)
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_send_audio_assigns_sequence_and_downlink_kind(gateway_session):
    session, channel = gateway_session
    assert await session.send_audio(b"a", turn_seq=0) is True
    assert await session.send_audio(b"bb", turn_seq=0, flags=FLAG_LAST) is True

    frames = [AudioFrame.parse(raw) for raw in channel.sent_bytes]
    assert [frame.seq for frame in frames] == [0, 1]
    assert all(frame.kind == KIND_DOWNLINK for frame in frames)
    assert all(frame.turn_seq == 0 for frame in frames)
    assert frames[1].last is True


async def test_send_audio_refused_after_close(gateway_session):
    session, _ = gateway_session
    await session.close()
    assert await session.send_audio(b"a", turn_seq=0) is False


async def test_send_audio_validates_empty_payload(gateway_session):
    session, channel = gateway_session
    assert await session.send_audio(b"", turn_seq=0) is False
    assert await session.send_audio(b"", turn_seq=0, flags=FLAG_SILENCE) is True
    assert AudioFrame.parse(channel.sent_bytes[0]).silence is True


async def test_binary_before_voice_start_is_dropped(gateway_session):
    session, channel = gateway_session
    channel.push(AudioFrame(KIND_UPLINK, 0, 0, 0, b"x").encode())
    await asyncio.sleep(0.05)
    assert session.voice is None


async def test_voice_start_then_frames_are_gated_by_turn_seq(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    for seq, turn_seq in enumerate((0, 1, 0, 2)):
        channel.push(AudioFrame(KIND_UPLINK, 0, seq, turn_seq, b"x").encode())
    await wait_until(lambda: session.voice.frames_received == 3)
    assert session.voice.state == "listening"


async def test_new_turn_restarts_turn_seq(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    channel.push(AudioFrame(KIND_UPLINK, 0, 0, 9, b"x").encode())
    await wait_until(lambda: session.voice.frames_received == 1)
    channel.push({"v": 2, "type": "voice.cancel", "turn_id": "t1", "reason": "user_stop"})
    await channel.wait_sent(lambda m: m.get("type") == "voice.cancel_ack")
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t2"})
    await wait_until(lambda: session.voice.turn_id == "t2")
    channel.push(AudioFrame(KIND_UPLINK, 0, 0, 0, b"x").encode())
    await wait_until(lambda: session.voice.frames_received == 1)


async def test_voice_start_twice_returns_busy(gateway_session):
    _, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t2"})
    error = await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert error["code"] == "busy"
    assert error["turn_id"] == "t2"


async def test_voice_cancel_returns_ack_and_returns_idle(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    channel.push({"v": 2, "type": "voice.cancel", "turn_id": "t1", "reason": "barge_in"})
    ack = await channel.wait_sent(lambda m: m.get("type") == "voice.cancel_ack")
    assert ack["reason"] == "barge_in"
    assert ack["turn_id"] == "t1"
    await wait_until(lambda: session.voice.state == "idle")


async def test_voice_end_moves_to_thinking(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    channel.push({"v": 2, "type": "voice.end", "turn_id": "t1"})
    await wait_until(lambda: session.voice.state == "thinking")
    channel.push(AudioFrame(KIND_UPLINK, 0, 1, 1, b"x").encode())
    await asyncio.sleep(0.05)
    assert session.voice.frames_received == 0


async def test_voice_end_with_unknown_turn_returns_error(gateway_session):
    _, channel = gateway_session
    channel.push({"v": 2, "type": "voice.end", "turn_id": "missing"})
    error = await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert error["code"] == "invalid_turn"


async def test_voice_cancel_unknown_turn_returns_error(gateway_session):
    _, channel = gateway_session
    channel.push({"v": 2, "type": "voice.cancel", "turn_id": "missing", "reason": "barge_in"})
    error = await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert error["code"] == "invalid_turn"


async def test_downlink_kind_from_device_is_rejected(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    channel.push(AudioFrame(KIND_DOWNLINK, 0, 0, 0, b"x").encode())
    await asyncio.sleep(0.05)
    assert session.voice.frames_received == 0


async def test_malformed_binary_does_not_kill_control_channel(gateway_session):
    session, channel = gateway_session
    channel.push(b"\xa1\x01")
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    assert session.voice.state == "listening"


async def test_voice_session_generation_and_state():
    session = VoiceSession("dev")
    assert session.start("t1") is True
    first_generation = session.generation
    session.cancel()
    assert session.generation == first_generation + 1
    assert session.state == "idle"
    assert session.start("t2") is True
    assert session.state == "listening"
    session.close()
    assert session.state == "closed"
    assert session.start("t3") is False
