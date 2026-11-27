"""ASR / TTS provider 抽象与离线 Mock。

真实 DashScope 适配器放在 Block C；本模块只定义管线依赖的最小接口，
并提供可在测试里完全离线运行的 Mock。任何实现都不得把识别/合成文本
写入日志，也不得把原始音频落到磁盘。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from companion.server.app.audio.ogg import (
    OPUS_TAGS_MAGIC,
    OggError,
    OggOpusMuxer,
    parse_ogg_page,
)

ASR_PARTIAL = "partial"
ASR_FINAL = "final"
ASR_ERROR = "error"


class ProviderError(RuntimeError):
    """Provider 失败；`code` 直接映射到 `voice.error.code`。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AsrEvent:
    kind: str
    text: str = ""
    code: str = ""


class AsrStream(Protocol):
    async def send(self, ogg_bytes: bytes) -> None: ...

    async def finish(self) -> None: ...

    async def close(self) -> None: ...

    def events(self) -> AsyncIterator[AsrEvent]: ...


class AsrProvider(Protocol):
    def open(
        self, *, device_id: str, turn_id: str, sample_rate: int = 16000
    ) -> AsrStream: ...


class TtsProvider(Protocol):
    def synthesize(
        self, text: str, *, turn_id: str, segment_seq: int
    ) -> AsyncIterator[bytes]: ...


class MockAsrProvider:
    """按脚本产出 partial/final/error。

    脚本项：
    - `{"after_pages": 1, "partial": "..."}`：收到 N 个音频页后发一次 partial。
    - `{"final": "...", "final_delay": 0.0}`：`finish()` 后发 final。
    - `{"error": "asr_unavailable"}`：立即发 error，并忽略后续输入。
    """

    def __init__(
        self, script: list[dict] | None = None, *, final_delay: float = 0.0
    ) -> None:
        self.script = list(script or [])
        self.final_delay = final_delay

    def open(
        self, *, device_id: str, turn_id: str, sample_rate: int = 16000
    ) -> "MockAsrStream":
        return MockAsrStream(self.script, final_delay=self.final_delay)


class MockAsrStream:
    def __init__(self, script: list[dict], *, final_delay: float = 0.0) -> None:
        self.received_bytes = 0
        self.audio_pages = 0
        self._script = list(script)
        self._queue: asyncio.Queue[AsrEvent | None] = asyncio.Queue()
        self._parse_buffer = b""
        self._closed = False
        self._finished = False
        self._error_code = ""
        self._final_text = ""
        self._final_delay = final_delay
        self._final_task: asyncio.Task | None = None
        self._partials: list[tuple[int, str]] = []
        self._fired: set[int] = set()

        for index, item in enumerate(self._script):
            if "error" in item:
                self._error_code = str(item["error"])
            elif "final" in item:
                self._final_text = str(item["final"])
                self._final_delay = float(item.get("final_delay", final_delay))
            elif "partial" in item:
                self._partials.append((int(item.get("after_pages", 1)), str(item["partial"])))

    async def send(self, ogg_bytes: bytes) -> None:
        if self._closed or self._finished or self._error_code:
            return
        if not ogg_bytes:
            return
        self.received_bytes += len(ogg_bytes)
        self.audio_pages += self._count_audio_pages(ogg_bytes)
        for index, (threshold, text) in enumerate(self._partials):
            if index in self._fired or self.audio_pages < threshold:
                continue
            self._fired.add(index)
            self._queue.put_nowait(AsrEvent(ASR_PARTIAL, text=text))

    async def finish(self) -> None:
        if self._closed or self._finished or self._error_code:
            return
        self._finished = True
        self._final_task = asyncio.create_task(self._emit_final())

    async def _emit_final(self) -> None:
        try:
            if self._final_delay:
                await asyncio.sleep(self._final_delay)
            if not self._closed:
                self._queue.put_nowait(AsrEvent(ASR_FINAL, text=self._final_text))
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._final_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._queue.put_nowait(None)

    async def events(self) -> AsyncIterator[AsrEvent]:
        if self._error_code:
            yield AsrEvent(ASR_ERROR, code=self._error_code)
            return
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event
            if event.kind in (ASR_FINAL, ASR_ERROR):
                return

    def _count_audio_pages(self, data: bytes) -> int:
        self._parse_buffer += data
        pages = 0
        while True:
            try:
                page, consumed = parse_ogg_page(self._parse_buffer)
            except OggError:
                self._parse_buffer = b""
                break
            if page is None:
                break
            self._parse_buffer = self._parse_buffer[consumed:]
            if page.bos:
                continue
            if any(seg.startswith(OPUS_TAGS_MAGIC) for seg in page.segments):
                continue
            pages += 1
        return pages


class MockTtsProvider:
    """为每段文本产出 `packets_per_segment` 个 20ms 假 Opus 包（Ogg 封装）。"""

    def __init__(
        self,
        *,
        packets_per_segment: int = 4,
        delay: float = 0.0,
        fail_at: int | None = None,
        error: str = "tts_unavailable",
        sample_rate: int = 24000,
    ) -> None:
        self.packets_per_segment = packets_per_segment
        self.delay = delay
        self.fail_at = fail_at
        self.error = error
        self.sample_rate = sample_rate
        self.synthesized: list[str] = []

    async def synthesize(
        self, text: str, *, turn_id: str, segment_seq: int
    ) -> AsyncIterator[bytes]:
        self.synthesized.append(text)
        if self.fail_at is not None and segment_seq == self.fail_at:
            raise ProviderError(self.error)
        muxer = OggOpusMuxer(sample_rate=self.sample_rate, serial=segment_seq + 1)
        for index in range(self.packets_per_segment):
            if self.delay:
                await asyncio.sleep(self.delay)
            packet = bytes([0x78, (segment_seq * 31 + index) & 0xFF]) + b"\x00" * 8
            yield muxer.push(packet)


class _UnavailableAsrStream:
    async def send(self, ogg_bytes: bytes) -> None:
        return None

    async def finish(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def events(self) -> AsyncIterator[AsrEvent]:
        yield AsrEvent(ASR_ERROR, code="asr_unavailable")


class UnavailableAsrProvider:
    def open(
        self, *, device_id: str, turn_id: str, sample_rate: int = 16000
    ) -> AsrStream:
        return _UnavailableAsrStream()


class UnavailableTtsProvider:
    async def synthesize(
        self, text: str, *, turn_id: str, segment_seq: int
    ) -> AsyncIterator[bytes]:
        raise ProviderError("tts_unavailable")
        yield b""  # pragma: no cover
