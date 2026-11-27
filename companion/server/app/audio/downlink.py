"""语音下行发送器。

一个轮次只允许一个发送器：它保证 `voice.segment` 先于该段音频、`turn_seq`
跨段连续递增、`flags.bit0` 只落在本轮最后一帧，并按包时长做 1x 节流。
队列有界；设备写失败或 `stop()` 后生产者不会被无限阻塞。
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any, Protocol

from companion.server.app.audio.framing import FLAG_LAST
from companion.server.app.services.clock import now_ms


class DownlinkClosed(RuntimeError):
    """下游拒绝写入或发送器已停止。"""


@dataclass(frozen=True)
class SegmentStart:
    segment_seq: int
    text: str
    last: bool
    turn_id: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "v": 2,
            "type": "voice.segment",
            "turn_id": self.turn_id,
            "segment_seq": self.segment_seq,
            "text": self.text,
            "last": self.last,
            "ts": now_ms(),
        }


@dataclass(frozen=True)
class AudioPacket:
    payload: bytes
    duration_ms: float
    last_in_turn: bool = False


@dataclass(frozen=True)
class _Flush:
    """队列哨兵：把仍无音频的段文本发出去（仅末段无音频时需要）。"""


class DownlinkSink(Protocol):
    async def send_control(self, payload: dict[str, Any]) -> bool: ...

    async def send_segment(
        self,
        segment: dict[str, Any],
        first_payload: bytes,
        *,
        turn_seq: int,
        flags: int = 0,
    ) -> bool: ...

    async def send_audio(
        self, payload: bytes, *, turn_seq: int, flags: int = 0
    ) -> bool: ...


class DownlinkSender:
    def __init__(
        self,
        sink: DownlinkSink,
        *,
        buffer_seconds: float = 5.0,
        frame_ms: int = 60,
        generation: int = 0,
    ) -> None:
        capacity = max(1, math.ceil(buffer_seconds * 1000 / max(frame_ms, 1)))
        self._sink = sink
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=capacity)
        self.generation = generation
        self.last_sent_segment: int | None = None
        self.sent_frames = 0
        self.failure: DownlinkClosed | None = None
        self._turn_seq = 0
        self._next_send_at = 0.0
        self._pending_segment: SegmentStart | None = None
        self._worker: asyncio.Task | None = None
        self._closed = False
        self._closed_event = asyncio.Event()
        self._idle_event = asyncio.Event()
        self._idle_event.set()

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            if self._closed:
                return
            self._worker = asyncio.create_task(self._run())

    async def enqueue(self, item: SegmentStart | AudioPacket, *, generation: int) -> bool:
        if self._closed or generation != self.generation:
            return False
        self.start()
        self._idle_event.clear()
        put_task = asyncio.create_task(self._queue.put(item))
        closed_task = asyncio.create_task(self._closed_event.wait())
        try:
            done, _ = await asyncio.wait(
                {put_task, closed_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (put_task, closed_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(put_task, closed_task, return_exceptions=True)
        if put_task in done and not put_task.cancelled():
            error = put_task.exception()
            if error is not None:
                raise error
            return not self._closed
        return False

    async def flush(self, *, generation: int) -> bool:
        """发送尚未收到音频的段元数据，避免末段文本被静默丢弃。"""
        return await self.enqueue(_Flush(), generation=generation)

    def stop(self) -> None:
        if self._closed:
            return
        self._mark_closed()
        worker = self._worker
        if worker is not None and not worker.done():
            worker.cancel()

    async def wait_idle(self, timeout: float | None = None) -> bool:
        if not self._idle_event.is_set():
            try:
                if timeout is None:
                    await self._idle_event.wait()
                else:
                    await asyncio.wait_for(self._idle_event.wait(), timeout)
            except asyncio.TimeoutError:
                return False
        if self.failure is not None:
            raise self.failure
        return True

    def _mark_closed(self) -> None:
        self._closed = True
        self._closed_event.set()
        while not self._queue.empty():
            self._queue.get_nowait()
        self._idle_event.set()

    async def _run(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                try:
                    await self._dispatch(item)
                except DownlinkClosed as exc:
                    if self.failure is None:
                        self.failure = exc
                    self._mark_closed()
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self.failure is None:
                        self.failure = DownlinkClosed("downlink sink failed")
                    self._mark_closed()
                    return
                if self._queue.empty():
                    self._idle_event.set()
        except asyncio.CancelledError:
            raise

    async def _dispatch(self, item: SegmentStart | AudioPacket | _Flush) -> None:
        if isinstance(item, SegmentStart):
            if self._pending_segment is not None:
                # 上一段没有任何音频就结束：至少把文本发给设备，不能静默丢失。
                await self._send_control(self._pending_segment)
            self._pending_segment = item
            return
        if isinstance(item, _Flush):
            segment = self._pending_segment
            self._pending_segment = None
            if segment is not None:
                await self._send_control(segment)
            return
        if not isinstance(item, AudioPacket):
            raise DownlinkClosed(f"unknown downlink item: {type(item).__name__}")

        flags = FLAG_LAST if item.last_in_turn else 0
        await self._pace(item.duration_ms)
        segment = self._pending_segment
        self._pending_segment = None
        if segment is None:
            ok = await self._sink.send_audio(
                item.payload, turn_seq=self._turn_seq, flags=flags
            )
        else:
            ok = await self._sink.send_segment(
                segment.to_payload(),
                item.payload,
                turn_seq=self._turn_seq,
                flags=flags,
            )
            if ok:
                self.last_sent_segment = segment.segment_seq
        if not ok:
            raise DownlinkClosed("downlink sink rejected a packet")
        self._turn_seq = (self._turn_seq + 1) & 0xFFFFFFFF
        self.sent_frames += 1
        self._next_send_at = time.monotonic() + max(item.duration_ms, 0.0) / 1000.0

    async def _send_control(self, segment: SegmentStart) -> None:
        if not await self._sink.send_control(segment.to_payload()):
            raise DownlinkClosed("downlink sink rejected a segment")
        self.last_sent_segment = segment.segment_seq

    async def _pace(self, duration_ms: float) -> None:
        now = time.monotonic()
        if self._next_send_at > now:
            await asyncio.sleep(self._next_send_at - now)
