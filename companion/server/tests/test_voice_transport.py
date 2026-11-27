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
from companion.server.app.audio.providers import UnavailableAsrProvider
from companion.server.app.audio.runtime import VoiceRuntime
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
        self.order: list[tuple[str, Any]] = []
        self.closed: tuple[int, str] | None = None
        self.sent_event = asyncio.Event()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        self.order.append(("json", payload))
        # 显式让出事件循环：这样“JSON 先于 bytes 且不被别的段插入”的断言
        # 才能真正检验 `_send_lock`，而不是靠两个协程恰好顺序执行。
        await asyncio.sleep(0)
        self.sent_event.set()

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)
        self.order.append(("bytes", data))
        await asyncio.sleep(0)
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
    # 已有活跃轮次时拒绝另一个 voice.start 的 busy 是非致命的：设备保持 t1。
    assert error["fatal"] is False


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
    error = await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert error["code"] == "invalid_frame"
    assert error["fatal"] is False
    assert session.voice.frames_received == 0


async def test_malformed_binary_while_listening_reports_once_and_recovers(
    gateway_session,
):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    channel.push(b"\xa1\x01")
    channel.push(b"\xa1\x01\x01\x00" + b"\x00" * 8)  # 缺 payload
    error = await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert error["code"] == "invalid_frame"
    assert error["turn_id"] == "t1"
    assert error["fatal"] is False
    # 同一轮只回一次，设备灌垃圾不能占满下行锁。
    await asyncio.sleep(0.05)
    assert sum(m.get("code") == "invalid_frame" for m in channel.sent) == 1

    channel.push(AudioFrame(KIND_UPLINK, 0, 0, 0, b"x").encode())
    await wait_until(lambda: session.voice.frames_received == 1)


async def test_duplicate_voice_start_same_turn_is_idempotent(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    channel.push(AudioFrame(KIND_UPLINK, 0, 0, 0, b"x").encode())
    await wait_until(lambda: session.voice.frames_received == 1)
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await asyncio.sleep(0.05)
    assert not any(m.get("code") == "busy" for m in channel.sent)
    assert session.voice.state == "listening"
    assert session.voice.frames_received == 1


async def test_segment_played_is_idempotent_and_late_reports_are_ignored(
    gateway_session,
):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    channel.push({"v": 2, "type": "voice.end", "turn_id": "t1"})
    await wait_until(lambda: session.voice.state == "thinking")
    assert session.voice.mark_speaking() is True

    for seq in (0, 0, 2, 1):
        channel.push(
            {"v": 2, "type": "voice.segment_played", "turn_id": "t1", "segment_seq": seq}
        )
    await asyncio.sleep(0.05)
    assert session.voice._last_segment_seq == 2

    channel.push({"v": 2, "type": "voice.cancel", "turn_id": "t1", "reason": "barge_in"})
    await channel.wait_sent(lambda m: m.get("type") == "voice.cancel_ack")
    channel.push(
        {"v": 2, "type": "voice.segment_played", "turn_id": "t1", "segment_seq": 3}
    )
    await asyncio.sleep(0.05)
    assert not any(m.get("type") == "voice.error" for m in channel.sent)


async def test_late_cancel_for_finished_turn_is_acked_again(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    channel.push({"v": 2, "type": "voice.cancel", "turn_id": "t1", "reason": "user_stop"})
    await channel.wait_sent(lambda m: m.get("type") == "voice.cancel_ack")
    # ack 丢失后设备重试：重放同一个 cancel 必须幂等再 ack，不能报错。
    channel.push({"v": 2, "type": "voice.cancel", "turn_id": "t1", "reason": "user_stop"})
    await asyncio.sleep(0.05)
    acks = [m for m in channel.sent if m.get("type") == "voice.cancel_ack"]
    assert len(acks) == 2
    assert all(m["turn_id"] == "t1" for m in acks)
    assert not any(m.get("type") == "voice.error" for m in channel.sent)


async def test_segment_played_without_a_turn_is_invalid_turn(gateway_session):
    _, channel = gateway_session
    channel.push(
        {"v": 2, "type": "voice.segment_played", "turn_id": "missing", "segment_seq": 0}
    )
    error = await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert error["code"] == "invalid_turn"
    assert error["fatal"] is False


async def test_v1_voice_event_gets_unsupported_protocol(seeded_device):
    gateway = DeviceGateway()
    channel = VoiceChannel()
    task = asyncio.create_task(gateway.handle(DEVICE_ID, channel))
    channel.push(
        {
            "v": 1,
            "type": "hello",
            "device_id": DEVICE_ID,
            "capabilities": [],
        }
    )
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    channel.push(
        {"v": 2, "type": "voice.event", "event": "asr_final", "turn_id": "t1", "text": "hi"}
    )
    error = await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert error["code"] == "unsupported_protocol"
    assert error["fatal"] is True
    # 后续其它 voice.* 不再重复报错。
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t2"})
    await asyncio.sleep(0.05)
    assert sum(m.get("code") == "unsupported_protocol" for m in channel.sent) == 1
    channel.push(None)
    await asyncio.wait_for(task, timeout=2.0)


async def test_voice_messages_from_v1_device_get_unsupported_protocol(seeded_device):
    gateway = DeviceGateway()
    channel = VoiceChannel()
    task = asyncio.create_task(gateway.handle(DEVICE_ID, channel))
    channel.push(
        {
            "v": 1,
            "type": "hello",
            "device_id": DEVICE_ID,
            "capabilities": [],
        }
    )
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    error = await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert error["code"] == "unsupported_protocol"
    assert error["fatal"] is True
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t2"})
    await asyncio.sleep(0.05)
    assert (
        sum(m.get("code") == "unsupported_protocol" for m in channel.sent) == 1
    )
    channel.push(None)
    await asyncio.wait_for(task, timeout=2.0)


async def test_voice_session_closed_on_disconnect(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    voice = session.voice
    channel.push(None)
    await wait_until(lambda: session.voice is None)
    assert voice.state == "closed"


async def test_send_segment_orders_text_before_audio_under_one_lock(gateway_session):
    session, channel = gateway_session
    channel.order.clear()
    await asyncio.gather(
        session.send_segment(
            {"v": 2, "type": "voice.segment", "segment_seq": 0},
            b"a",
            turn_seq=0,
        ),
        session.send_segment(
            {"v": 2, "type": "voice.segment", "segment_seq": 1},
            b"b",
            turn_seq=0,
        ),
    )
    assert [kind for kind, _ in channel.order] == ["json", "bytes", "json", "bytes"]
    assert channel.order[0][1]["segment_seq"] == 0
    assert channel.order[2][1]["segment_seq"] == 1


@pytest.mark.parametrize(
    "payload,turn_seq,flags",
    [
        (b"a", -1, 0),
        (b"a", 0x1_0000_0000, 0),
        ("text", 0, 0),
        (b"a", 0, 0x80),
        (b"x" * (16 * 1024 + 1), 0, 0),
    ],
)
async def test_send_audio_rejects_invalid_arguments(
    gateway_session, payload, turn_seq, flags
):
    session, channel = gateway_session
    assert await session.send_audio(payload, turn_seq=turn_seq, flags=flags) is False
    assert channel.sent_bytes == []


async def test_voice_audio_path_does_not_log_payload(gateway_session, caplog):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    marker = b"SECRET-AUDIO-MARKER"
    channel.push(AudioFrame(KIND_DOWNLINK, 0, 0, 0, marker).encode())
    await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert marker.decode() not in caplog.text
    assert marker.hex() not in caplog.text


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


async def test_send_control_never_interleaves_with_a_segment(
    gateway_session,
):
    session, channel = gateway_session
    channel.order.clear()
    # 段先启动：没有共享发送锁时，control 会插到 segment 的 JSON 与 bytes 之间。
    await asyncio.gather(
        session.send_segment(
            {"v": 2, "type": "voice.segment", "segment_seq": 0},
            b"a",
            turn_seq=0,
        ),
        session.send_control(
            {"v": 2, "type": "voice.cancel_ack", "turn_id": "t1", "reason": "barge_in"}
        ),
    )
    assert [kind for kind, _ in channel.order] == ["json", "bytes", "json"]
    assert channel.order[0][1]["type"] == "voice.segment"
    assert channel.order[2][1]["type"] == "voice.cancel_ack"


async def test_runtime_pipeline_reports_unbound(seeded_device):
    async def resolve(_device_id: str) -> str | None:
        return None

    runtime = VoiceRuntime(
        asr=UnavailableAsrProvider(), resolve_conversation=resolve
    )
    gateway = DeviceGateway(voice_runtime=runtime)
    channel = VoiceChannel()
    task = asyncio.create_task(gateway.handle(DEVICE_ID, channel))
    channel.push(await _hello())
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    session = get_device_link(DEVICE_ID)
    assert session is not None
    await wait_until(lambda: session.ready)

    try:
        channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
        error = await channel.wait_sent(
            lambda m: m.get("type") == "voice.error"
        )
        assert error["code"] == "unbound"
        assert error["fatal"] is True
        await wait_until(lambda: session.voice.state == "idle")
        assert session.voice.turn_id is None
    finally:
        channel.push(None)
        await asyncio.wait_for(task, timeout=2.0)


async def test_runtime_without_asr_reports_asr_unavailable(seeded_device):
    async def resolve(_device_id: str) -> str | None:
        return "conv-voice-transport"

    runtime = VoiceRuntime(asr=None, resolve_conversation=resolve)
    gateway = DeviceGateway(voice_runtime=runtime)
    channel = VoiceChannel()
    task = asyncio.create_task(gateway.handle(DEVICE_ID, channel))
    channel.push(await _hello())
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    session = get_device_link(DEVICE_ID)
    assert session is not None
    await wait_until(lambda: session.ready)

    try:
        channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
        error = await channel.wait_sent(
            lambda m: m.get("type") == "voice.error"
        )
        assert error["code"] == "asr_unavailable"
        assert error["fatal"] is True
        await wait_until(lambda: session.voice.state == "idle")
    finally:
        channel.push(None)
        await asyncio.wait_for(task, timeout=2.0)


class _HangingAsrStream:
    """保持 events() 打开直到 close()，用于断线清理断言。"""

    def __init__(self) -> None:
        self.closed = False
        self.finished = False
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send(self, ogg_bytes: bytes) -> None:
        return None

    async def finish(self) -> None:
        self.finished = True
        self._queue.put_nowait(None)

    async def close(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)

    async def events(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item


async def test_disconnect_shuts_down_voice_and_asr(seeded_device):
    stream = _HangingAsrStream()

    class _AsrProvider:
        def open(self, **_kwargs):
            return stream

    async def resolve(_device_id: str) -> str | None:
        return "conv-voice-transport"

    runtime = VoiceRuntime(asr=_AsrProvider(), resolve_conversation=resolve)
    gateway = DeviceGateway(voice_runtime=runtime)
    channel = VoiceChannel()
    task = asyncio.create_task(gateway.handle(DEVICE_ID, channel))
    channel.push(await _hello())
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    session = get_device_link(DEVICE_ID)
    assert session is not None
    await wait_until(lambda: session.ready)

    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None and session.voice._asr is not None)
    voice = session.voice
    assert voice is not None

    channel.push(None)
    await asyncio.wait_for(task, timeout=2.0)

    await wait_until(lambda: stream.closed)
    assert voice.state == "closed"
    assert session.voice is None


async def test_runtime_cancel_bumps_generation_and_acks(seeded_device):
    release = asyncio.Event()

    async def resolve(_device_id: str) -> str | None:
        await release.wait()
        return None

    runtime = VoiceRuntime(
        asr=UnavailableAsrProvider(), resolve_conversation=resolve
    )
    gateway = DeviceGateway(voice_runtime=runtime)
    channel = VoiceChannel()
    task = asyncio.create_task(gateway.handle(DEVICE_ID, channel))
    channel.push(await _hello())
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    session = get_device_link(DEVICE_ID)
    assert session is not None
    await wait_until(lambda: session.ready)

    try:
        channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
        await wait_until(lambda: session.voice is not None)
        generation = session.voice.generation
        channel.push(
            {"v": 2, "type": "voice.cancel", "turn_id": "t1", "reason": "barge_in"}
        )
        ack = await channel.wait_sent(
            lambda m: m.get("type") == "voice.cancel_ack"
        )
        assert ack["reason"] == "barge_in"
        assert session.voice.generation > generation
        assert session.voice.state == "idle"
    finally:
        release.set()
        channel.push(None)
        await asyncio.wait_for(task, timeout=2.0)
