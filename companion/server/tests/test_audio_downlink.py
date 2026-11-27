"""下行发送器：顺序、turn_seq、last 标志、节流、背压与失败处理。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from companion.server.app.audio.downlink import (
    AudioPacket,
    DownlinkClosed,
    DownlinkSender,
    SegmentStart,
)
from companion.server.app.audio.framing import FLAG_LAST


class RecordingSink:
    def __init__(self, *, audio_ok: bool = True, delay: float = 0.0) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.times: list[float] = []
        self.audio_ok = audio_ok
        self.delay = delay

    async def _sleep(self) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)

    async def send_control(self, payload: dict[str, Any]) -> bool:
        await self._sleep()
        self.calls.append(("control", payload))
        self.times.append(time.monotonic())
        return True

    async def send_segment(
        self,
        segment: dict[str, Any],
        first_payload: bytes,
        *,
        turn_seq: int,
        flags: int = 0,
    ) -> bool:
        await self._sleep()
        self.calls.append(("segment", segment, first_payload, turn_seq, flags))
        self.times.append(time.monotonic())
        return self.audio_ok

    async def send_audio(
        self, payload: bytes, *, turn_seq: int, flags: int = 0
    ) -> bool:
        await self._sleep()
        self.calls.append(("audio", payload, turn_seq, flags))
        self.times.append(time.monotonic())
        return self.audio_ok


async def test_segment_precedes_its_audio_and_turn_seq_is_continuous():
    sink = RecordingSink()
    sender = DownlinkSender(sink, frame_ms=20)
    await sender.enqueue(SegmentStart(0, "第一段", False, "t1"), generation=0)
    await sender.enqueue(AudioPacket(b"p0", 20), generation=0)
    await sender.enqueue(AudioPacket(b"p1", 20, last_in_turn=True), generation=0)
    await sender.enqueue(SegmentStart(1, "第二段", True, "t1"), generation=0)
    await sender.enqueue(AudioPacket(b"p2", 20, last_in_turn=True), generation=0)
    assert await sender.wait_idle(2.0) is True

    assert [call[0] for call in sink.calls] == ["segment", "audio", "segment"]
    first = sink.calls[0]
    assert first[1]["segment_seq"] == 0
    assert first[1]["last"] is False
    assert first[1]["turn_id"] == "t1"
    assert first[2] == b"p0"
    assert first[3] == 0
    assert first[4] == 0

    second = sink.calls[1]
    assert second[1] == b"p1"
    assert second[2] == 1
    assert second[3] == FLAG_LAST

    third = sink.calls[2]
    assert third[1]["segment_seq"] == 1
    assert third[1]["last"] is True
    assert third[2] == b"p2"
    assert third[3] == 2
    assert third[4] == FLAG_LAST

    assert sender.last_sent_segment == 1
    assert sender.sent_frames == 3


async def test_segment_start_waits_for_its_first_packet():
    sink = RecordingSink()
    sender = DownlinkSender(sink)
    await sender.enqueue(SegmentStart(0, "只有文字", True, "t1"), generation=0)
    assert await sender.wait_idle(1.0) is True
    assert sink.calls == []

    await sender.enqueue(AudioPacket(b"p0", 1, last_in_turn=True), generation=0)
    assert await sender.wait_idle(1.0) is True
    assert [call[0] for call in sink.calls] == ["segment"]


async def test_bounded_queue_applies_backpressure_without_deadlock():
    sink = RecordingSink(delay=0.03)
    sender = DownlinkSender(sink, buffer_seconds=0.001, frame_ms=60)
    started = time.monotonic()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                sender.enqueue(AudioPacket(bytes([index]), 1), generation=0)
                for index in range(3)
            )
        ),
        2.0,
    )
    assert results == [True, True, True]
    assert await sender.wait_idle(2.0) is True
    assert len(sink.calls) == 3
    assert time.monotonic() - started >= 0.03


async def test_generation_mismatch_is_rejected():
    sink = RecordingSink()
    sender = DownlinkSender(sink, generation=5)
    assert await sender.enqueue(AudioPacket(b"x", 1), generation=4) is False
    assert await sender.wait_idle(0.05) is True
    assert sink.calls == []


async def test_stop_rejects_enqueue_and_unblocks_producer():
    sink = RecordingSink(delay=0.5)
    sender = DownlinkSender(sink, buffer_seconds=0.001, frame_ms=60)
    producers = [
        asyncio.create_task(sender.enqueue(AudioPacket(bytes([index]), 1), generation=0))
        for index in range(6)
    ]
    await asyncio.sleep(0.05)
    sender.stop()
    results = await asyncio.wait_for(asyncio.gather(*producers), 1.0)
    assert False in results
    assert await sender.enqueue(AudioPacket(b"d", 1), generation=0) is False
    assert await sender.wait_idle(1.0) is True
    await asyncio.sleep(0)


async def test_sink_failure_stops_sender_and_surfaces_error():
    sink = RecordingSink(audio_ok=False)
    sender = DownlinkSender(sink)
    await sender.enqueue(AudioPacket(b"x", 1), generation=0)
    with pytest.raises(DownlinkClosed):
        await sender.wait_idle(1.0)
    assert sender.failure is not None
    assert await sender.enqueue(AudioPacket(b"y", 1), generation=0) is False


async def test_packets_are_throttled_by_duration():
    sink = RecordingSink()
    sender = DownlinkSender(sink, frame_ms=20)
    await sender.enqueue(AudioPacket(b"a", 20), generation=0)
    await sender.enqueue(AudioPacket(b"b", 20), generation=0)
    assert await sender.wait_idle(2.0) is True
    assert len(sink.times) == 2
    assert sink.times[1] - sink.times[0] >= 0.01


async def test_wait_idle_times_out_while_sending():
    sink = RecordingSink(delay=0.2)
    sender = DownlinkSender(sink)
    await sender.enqueue(AudioPacket(b"a", 1), generation=0)
    assert await sender.wait_idle(0.01) is False
    sender.stop()
    await asyncio.sleep(0)


async def test_segment_without_audio_is_flushed_as_control():
    sink = RecordingSink()
    sender = DownlinkSender(sink)
    await sender.enqueue(SegmentStart(0, "空段", False, "t1"), generation=0)
    await sender.enqueue(SegmentStart(1, "下一段", True, "t1"), generation=0)
    await sender.enqueue(AudioPacket(b"p", 1, last_in_turn=True), generation=0)
    assert await sender.wait_idle(1.0) is True
    assert [call[0] for call in sink.calls] == ["control", "segment"]
    assert sink.calls[0][1]["segment_seq"] == 0
    assert sink.calls[1][1]["segment_seq"] == 1


async def test_trailing_segment_without_audio_is_flushed():
    sink = RecordingSink()
    sender = DownlinkSender(sink)
    await sender.enqueue(SegmentStart(0, "只有文字", True, "t1"), generation=0)
    assert await sender.wait_idle(0.05) is True
    assert sink.calls == []

    assert await sender.flush(generation=0) is True
    assert await sender.wait_idle(1.0) is True
    assert [call[0] for call in sink.calls] == ["control"]
    assert sink.calls[0][1]["segment_seq"] == 0
    assert sink.calls[0][1]["last"] is True
