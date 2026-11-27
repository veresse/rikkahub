"""Mock ASR / TTS provider 测试（完全离线）。"""

from __future__ import annotations

import asyncio
import time

import pytest

from companion.server.app.audio.ogg import OggOpusDemuxer, OggOpusMuxer
from companion.server.app.audio.providers import (
    ASR_ERROR,
    ASR_FINAL,
    ASR_PARTIAL,
    AsrEvent,
    MockAsrProvider,
    MockTtsProvider,
    ProviderError,
    UnavailableAsrProvider,
    UnavailableTtsProvider,
)

PACKET_20MS = b"\x78" + b"\x00" * 9


def _audio_pages(count: int, *, serial: int = 7) -> list[bytes]:
    muxer = OggOpusMuxer(serial=serial)
    return [muxer.push(PACKET_20MS) for _ in range(count)]


async def test_mock_asr_emits_partials_by_page_count():
    provider = MockAsrProvider(
        [{"after_pages": 2, "partial": "你"}, {"final": "你好"}]
    )
    stream = provider.open(device_id="dev", turn_id="t1")
    pages = _audio_pages(2)
    for page in pages:
        await stream.send(page)
    await stream.finish()

    events = [event async for event in stream.events()]
    assert [event.kind for event in events] == [ASR_PARTIAL, ASR_FINAL]
    assert events[0].text == "你"
    assert events[1].text == "你好"
    assert stream.received_bytes == sum(len(page) for page in pages)
    assert stream.audio_pages == 2


async def test_mock_asr_default_finish_emits_empty_final():
    stream = MockAsrProvider().open(device_id="dev", turn_id="t1")
    await stream.finish()
    events = [event async for event in stream.events()]
    assert events == [AsrEvent(ASR_FINAL, text="")]


async def test_mock_asr_error_short_circuits_input():
    stream = MockAsrProvider([{"error": "asr_unavailable"}]).open(
        device_id="dev", turn_id="t1"
    )
    await stream.send(b"ignored")
    await stream.finish()
    events = [event async for event in stream.events()]
    assert events == [AsrEvent(ASR_ERROR, code="asr_unavailable")]
    assert stream.received_bytes == 0


async def test_mock_asr_final_delay_is_async():
    stream = MockAsrProvider([{"final": "好", "final_delay": 0.05}]).open(
        device_id="dev", turn_id="t1"
    )
    started = time.monotonic()
    await stream.finish()
    events = [event async for event in stream.events()]
    elapsed = time.monotonic() - started
    assert events == [AsrEvent(ASR_FINAL, text="好")]
    assert elapsed >= 0.03


async def test_mock_asr_close_cancels_pending_final():
    stream = MockAsrProvider([{"final": "好", "final_delay": 0.2}]).open(
        device_id="dev", turn_id="t1"
    )
    await stream.finish()
    await asyncio.sleep(0.01)
    await stream.close()
    events = [event async for event in stream.events()]
    assert events == []


async def test_mock_tts_roundtrips_through_demuxer():
    tts = MockTtsProvider(packets_per_segment=3)
    chunks = [
        chunk
        async for chunk in tts.synthesize("你好", turn_id="t1", segment_seq=0)
    ]
    demuxer = OggOpusDemuxer()
    packets: list[bytes] = []
    for chunk in chunks:
        packets.extend(demuxer.feed(chunk))
    assert len(packets) == 3
    assert all(packet[0] == 0x78 for packet in packets)
    assert tts.synthesized == ["你好"]


async def test_mock_tts_fail_at_raises_provider_error():
    tts = MockTtsProvider(packets_per_segment=2, fail_at=1)
    with pytest.raises(ProviderError) as excinfo:
        async for _ in tts.synthesize("x", turn_id="t1", segment_seq=1):
            pass
    assert excinfo.value.code == "tts_unavailable"


async def test_mock_tts_is_cancellable():
    tts = MockTtsProvider(packets_per_segment=5, delay=0.01)
    generator = tts.synthesize("x", turn_id="t1", segment_seq=0)
    first = await generator.__anext__()
    assert first.startswith(b"OggS")
    await generator.aclose()
    with pytest.raises(StopAsyncIteration):
        await generator.__anext__()
    assert tts.synthesized == ["x"]


async def test_unavailable_providers():
    asr = UnavailableAsrProvider().open(device_id="dev", turn_id="t1")
    events = [event async for event in asr.events()]
    assert events == [AsrEvent(ASR_ERROR, code="asr_unavailable")]

    with pytest.raises(ProviderError) as excinfo:
        async for _ in UnavailableTtsProvider().synthesize(
            "x", turn_id="t1", segment_seq=0
        ):
            pass
    assert excinfo.value.code == "tts_unavailable"


async def test_mock_providers_do_not_log_text(caplog):
    tts = MockTtsProvider(packets_per_segment=1)
    async for _ in tts.synthesize("秘密文本", turn_id="t1", segment_seq=0):
        pass
    stream = MockAsrProvider([{"final": "秘密识别"}]).open(
        device_id="dev", turn_id="t1"
    )
    await stream.finish()
    _ = [event async for event in stream.events()]
    assert "秘密文本" not in caplog.text
    assert "秘密识别" not in caplog.text
