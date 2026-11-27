"""真实 DashScope 适配器 + 本地假 WS/SSE + `VoiceSession` 的完整轮次。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from companion.server.app.audio.dashscope import (
    DashScopeAsrProvider,
    DashScopeTtsProvider,
)
from companion.server.app.audio.framing import (
    FLAG_LAST,
    KIND_UPLINK,
    AudioFrame,
)
from companion.server.app.audio.ogg import OggOpusMuxer
from companion.server.app.audio.pipeline import VoiceSession
from companion.server.app.audio.runtime import VoiceConfig, VoiceRuntime
from companion.server.tests.dashscope_fakes import (
    FakeAsrServer,
    audio_chunk,
    sentence,
    sse_bytes,
)

API_KEY = "sk-test-key"
ASR_TEXT = "请给我讲一个故事。"
SENTENCE_ONE = "第一段足够长。"
SENTENCE_TWO = "第二段也够长了。"
PACKET = b"\x78" + b"\x00" * 9


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    def only(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("event") == name]


class RecordingSink:
    def __init__(self) -> None:
        self.controls: list[dict[str, Any]] = []
        self.segments: list[dict[str, Any]] = []
        self.audio: list[dict[str, Any]] = []

    async def send_control(self, payload: dict[str, Any]) -> bool:
        self.controls.append(dict(payload))
        return True

    async def send_segment(
        self,
        segment: dict[str, Any],
        first_payload: bytes,
        *,
        turn_seq: int,
        flags: int = 0,
    ) -> bool:
        self.segments.append(
            {
                "segment": dict(segment),
                "payload": first_payload,
                "turn_seq": turn_seq,
                "flags": flags,
            }
        )
        return True

    async def send_audio(
        self, payload: bytes, *, turn_seq: int, flags: int = 0
    ) -> bool:
        self.audio.append(
            {"payload": payload, "turn_seq": turn_seq, "flags": flags}
        )
        return True

    @property
    def errors(self) -> list[dict[str, Any]]:
        return [
            item for item in self.controls if item.get("type") == "voice.error"
        ]


class FakeTts:
    """`httpx.MockTransport` 假 SSE；每次请求按序返回一段合法 Ogg Opus。"""

    def __init__(self, *, url_error: bool = False, packets: int = 2) -> None:
        self.url_error = url_error
        self.packets = packets
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        headers = {"content-type": "text/event-stream"}
        if self.url_error:
            return httpx.Response(
                200,
                headers=headers,
                content=sse_bytes(
                    {"output": {"audio": {"url": "https://example.invalid/a.ogg"}}}
                ),
            )
        pages = _ogg_pages(serial=len(self.requests), packets=self.packets)
        return httpx.Response(
            200,
            headers=headers,
            content=sse_bytes(
                audio_chunk(pages),
                {"output": {"finish_reason": "stop"}},
            ),
        )

    def provider(self) -> DashScopeTtsProvider:
        transport = httpx.MockTransport(self.handler)
        return DashScopeTtsProvider(
            API_KEY,
            url="http://127.0.0.1:9002/tts",
            client_factory=lambda: httpx.AsyncClient(transport=transport),
        )


def _ogg_pages(serial: int, *, packets: int) -> bytes:
    muxer = OggOpusMuxer(sample_rate=24000, serial=serial)
    data = b""
    for index in range(packets):
        payload = bytes([0x78, (serial * 31 + index) & 0xFF]) + b"\x00" * 8
        data += muxer.push(payload)
    return data + muxer.finish()


def uplink(turn_seq: int, payload: bytes = PACKET) -> AudioFrame:
    return AudioFrame(
        kind=KIND_UPLINK,
        flags=0,
        seq=turn_seq,
        turn_seq=turn_seq,
        payload=payload,
    )


def fast_config() -> VoiceConfig:
    return VoiceConfig(
        idle_timeout=5.0,
        max_turn=5.0,
        asr_final_grace=0.5,
        tts_first_packet_timeout=2.0,
        segment_idle_timeout=2.0,
        playback_done_timeout=2.0,
        downlink_buffer_seconds=1.0,
        uplink_buffer_seconds=1.0,
        frame_ms=20,
    )


def scripted_chat(*sentences: str):
    calls: list[tuple[str, str]] = []

    async def factory(
        conversation_id: str, text: str, *, cancel_reason: str = "barge_in"
    ):
        calls.append((conversation_id, text))
        for item in sentences:
            yield {"type": "content", "delta": item}
        yield {"type": "done", "finish_reason": "stop"}

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


async def wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def build_runtime(asr_fake: FakeAsrServer, tts: FakeTts, chat) -> tuple[VoiceRuntime, RecordingBus]:
    bus = RecordingBus()

    async def resolve(_device_id: str) -> str | None:
        return "conv-1"

    runtime = VoiceRuntime(
        bus=bus,
        asr=DashScopeAsrProvider(
            API_KEY,
            url=asr_fake.url,
            handshake_timeout=2.0,
            finish_timeout=1.0,
        ),
        tts=tts.provider(),
        resolve_conversation=resolve,
        conversation_busy=lambda _conversation_id: False,
        chat_factory=chat,
    )
    return runtime, bus


async def test_full_turn_with_real_adapters_against_fake_servers():
    chat = scripted_chat(SENTENCE_ONE, SENTENCE_TWO)
    tts = FakeTts()
    async with FakeAsrServer(
        finish_results=(sentence(ASR_TEXT, end=True),)
    ) as asr_fake:
        runtime, bus = build_runtime(asr_fake, tts, chat)
        sink = RecordingSink()
        session = VoiceSession(
            "dev-1", runtime=runtime, sink=sink, config=fast_config()
        )
        assert session.start("t1") is True
        await session.begin()
        assert await session.accept_frame(uplink(0)) is True
        assert session.end_user_speech() is True

        await wait_until(lambda: len(sink.segments) >= 2)
        assert sink.errors == []
        # 末段最后一个音频包带 FLAG_LAST；设备据此判定整轮播放完成。
        await wait_until(
            lambda: any(item["flags"] & FLAG_LAST for item in sink.audio)
        )
        assert sink.audio[-1]["flags"] & FLAG_LAST
        last = sink.segments[-1]
        assert session.segment_played(last["segment"]["segment_seq"]) is True
        await wait_until(lambda: bool(bus.only("turn_end")))

        await session.shutdown()

    turn_end = bus.only("turn_end")[-1]
    assert turn_end["finish_reason"] == "stop"
    assert chat.calls == [("conv-1", ASR_TEXT)]

    assert asr_fake.run_task["payload"]["model"] == "fun-asr-realtime"
    assert asr_fake.audio  # 上行 Ogg 页确实转发给了 ASR
    assert asr_fake.finish_count == 1

    assert [item["input"]["text"] for item in tts.requests] == [
        SENTENCE_ONE,
        SENTENCE_TWO,
    ]
    for item in tts.requests:
        assert item["input"]["format"] == "opus"
        assert item["input"]["sample_rate"] == 24000


async def test_tts_audio_url_ends_turn_with_fatal_tts_unavailable():
    chat = scripted_chat(SENTENCE_ONE)
    tts = FakeTts(url_error=True)
    async with FakeAsrServer(
        finish_results=(sentence(ASR_TEXT, end=True),)
    ) as asr_fake:
        runtime, bus = build_runtime(asr_fake, tts, chat)
        sink = RecordingSink()
        session = VoiceSession(
            "dev-1", runtime=runtime, sink=sink, config=fast_config()
        )
        assert session.start("t1") is True
        await session.begin()
        assert await session.accept_frame(uplink(0)) is True
        assert session.end_user_speech() is True

        await wait_until(lambda: bool(sink.errors))
        await wait_until(lambda: bool(bus.only("turn_end")))
        await session.shutdown()

    assert sink.errors[-1]["code"] == "tts_unavailable"
    assert sink.errors[-1]["fatal"] is True
    assert bus.only("turn_end")[-1]["finish_reason"] == "error"
