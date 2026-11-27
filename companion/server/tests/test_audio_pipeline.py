"""VoiceSession 完整 Mock 管线（离线，无云 Key / 无真机）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from companion.server.app.audio.framing import (
    FLAG_LAST,
    KIND_UPLINK,
    AudioFrame,
)
from companion.server.app.audio.pipeline import VoiceSession
from companion.server.app.audio.providers import MockAsrProvider, MockTtsProvider
from companion.server.app.audio.runtime import VoiceConfig, VoiceRuntime

PACKET = b"\x78" + b"\x00" * 9
SENTENCE_ONE = "第一段足够长。"
SENTENCE_TWO = "第二段也够长了。"


def uplink(turn_seq: int, payload: bytes = PACKET) -> AudioFrame:
    return AudioFrame(
        kind=KIND_UPLINK,
        flags=0,
        seq=turn_seq,
        turn_seq=turn_seq,
        payload=payload,
    )


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    def names(self) -> list[str]:
        return [event.get("event") for event in self.events]

    def only(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("event") == name]


class BlockingBus(RecordingBus):
    """发布指定事件时挂起，用于在 `turn_start` 窗口内制造取消/断开。"""

    def __init__(self, blocked_event: str) -> None:
        super().__init__()
        self.blocked_event = blocked_event
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def publish(self, event: dict[str, Any]) -> None:
        await super().publish(event)
        if event.get("event") == self.blocked_event:
            self.entered.set()
            await self.release.wait()


class RecordingSink:
    def __init__(self) -> None:
        self.controls: list[dict[str, Any]] = []
        self.segments: list[dict[str, Any]] = []
        self.audio: list[dict[str, Any]] = []
        self.calls: list[str] = []

    async def send_control(self, payload: dict[str, Any]) -> bool:
        self.controls.append(dict(payload))
        self.calls.append("control")
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
        self.calls.append("segment")
        return True

    async def send_audio(
        self, payload: bytes, *, turn_seq: int, flags: int = 0
    ) -> bool:
        self.audio.append(
            {"payload": payload, "turn_seq": turn_seq, "flags": flags}
        )
        self.calls.append("audio")
        return True

    @property
    def errors(self) -> list[dict[str, Any]]:
        return [
            item for item in self.controls if item.get("type") == "voice.error"
        ]


def scripted_chat(*sentences: str, error: str | None = None):
    calls: list[tuple[str, str, str]] = []

    async def factory(
        conversation_id: str, text: str, *, cancel_reason: str = "barge_in"
    ):
        calls.append((conversation_id, text, cancel_reason))
        if error is not None:
            yield {"type": "error", "error": error}
            return
        for sentence in sentences:
            yield {"type": "content", "delta": sentence}
        yield {"type": "done", "finish_reason": "stop"}

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


def make_runtime(
    *,
    chat=None,
    asr_script: list[dict] | None = None,
    tts: MockTtsProvider | None = None,
    conversation: str | None = "conv-1",
    busy: bool = False,
    bus: RecordingBus | None = None,
    resolve=None,
) -> tuple[VoiceRuntime, RecordingBus]:
    recording_bus = bus or RecordingBus()

    async def default_resolve(_device_id: str) -> str | None:
        return conversation

    return (
        VoiceRuntime(
            bus=recording_bus,
            asr=MockAsrProvider(
                asr_script if asr_script is not None else [
                    {"final": "请给我讲一个故事。"}
                ]
            ),
            tts=tts or MockTtsProvider(packets_per_segment=2),
            resolve_conversation=resolve or default_resolve,
            conversation_busy=lambda _conversation_id: busy,
            chat_factory=chat
            or scripted_chat(SENTENCE_ONE, SENTENCE_TWO),
        ),
        recording_bus,
    )


def make_session(
    runtime: VoiceRuntime,
    sink: RecordingSink,
    *,
    config: VoiceConfig | None = None,
    turn_id: str = "t1",
) -> VoiceSession:
    session = VoiceSession(
        "dev-1", runtime=runtime, sink=sink, config=config
    )
    assert session.start(turn_id) is True
    return session


async def wait_until(predicate, timeout: float = 3.0) -> None:
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


def fast_config(**overrides) -> VoiceConfig:
    values = {
        "idle_timeout": 5.0,
        "max_turn": 5.0,
        "asr_final_grace": 0.1,
        "tts_first_packet_timeout": 1.0,
        "segment_idle_timeout": 1.0,
        "playback_done_timeout": 1.0,
        "downlink_buffer_seconds": 1.0,
        "uplink_buffer_seconds": 1.0,
        "frame_ms": 20,
    }
    values.update(overrides)
    return VoiceConfig(**values)


async def test_begin_unbound_reports_fatal_and_returns_to_idle():
    runtime, bus = make_runtime(conversation=None)
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()

    assert session.state == "idle"
    assert sink.errors and sink.errors[-1]["code"] == "unbound"
    assert sink.errors[-1]["fatal"] is True
    assert bus.names() == ["turn_end"]
    assert bus.events[0]["finish_reason"] == "error"


async def test_begin_busy_reports_fatal_busy():
    runtime, bus = make_runtime(busy=True)
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()

    assert session.state == "idle"
    assert sink.errors[-1]["code"] == "busy"
    assert sink.errors[-1]["fatal"] is True
    assert bus.only("turn_end")[0]["finish_reason"] == "error"


async def test_end_before_begin_open_waits_for_asr_instead_of_failing():
    """voice.start 后立刻 voice.end（begin 任务尚未执行）不得误报 asr_unavailable。"""

    async def slow_resolve(_device_id: str) -> str | None:
        # 模拟真实绑定：解析会话必然 await（DB），给 end 抢在 begin 前的窗口。
        await asyncio.sleep(0)
        return "conv-1"

    runtime, bus = make_runtime(resolve=slow_resolve)
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    assert await session.accept_frame(uplink(0)) is True
    # 网关先 create_task(begin())，同一 tick 内紧接着处理 voice.end。
    begin_task = asyncio.create_task(session.begin())
    assert session.end_user_speech() is True
    assert session.state == "thinking"
    assert sink.errors == []
    assert "turn_start" not in bus.names()

    # begin 真正执行（并 await resolver）后，应接着打开 ASR 并开轮。
    await begin_task
    await wait_until(lambda: session.state == "speaking")
    await wait_until(lambda: len(sink.segments) == 2 and len(sink.audio) >= 2)
    assert sink.errors == []
    assert "turn_start" in bus.names()
    await session.shutdown()


async def test_duplicate_start_same_turn_is_idempotent_while_speaking():
    runtime, _bus = make_runtime(chat=scripted_chat(SENTENCE_ONE, SENTENCE_TWO))
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()
    session.end_user_speech()
    await wait_until(lambda: session.state == "speaking")
    generation = session.generation

    assert session.start("t1") is True
    assert session.state == "speaking"
    assert session.generation == generation
    await session.shutdown()


async def test_completion_cleanup_never_touches_the_next_turn():
    runtime, _bus = make_runtime()
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())
    await session.begin()
    gen = session.generation
    old_stream = session._asr
    assert old_stream is not None

    closing = asyncio.Event()
    release = asyncio.Event()

    async def slow_close() -> None:
        closing.set()
        await release.wait()

    old_stream.close = slow_close
    completing = asyncio.create_task(session._complete_turn(gen, "t1", "stop"))
    await asyncio.wait_for(closing.wait(), 2.0)

    # 旧轮清理还卡在 ASR.close()，设备已合法开始下一轮。
    assert session.start("t2") is True
    await session.begin()
    next_generation = session.generation
    assert session._pump_task is not None
    assert session._reader_task is not None

    release.set()
    await asyncio.wait_for(completing, 2.0)

    assert session.state == "listening"
    assert session.turn_id == "t2"
    assert session.generation == next_generation
    assert session._pump_task is not None
    assert session._reader_task is not None
    await session.shutdown()


async def test_cancel_cleanup_never_touches_the_next_turn():
    runtime, _bus = make_runtime()
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())
    await session.begin()
    old_stream = session._asr
    assert old_stream is not None

    closing = asyncio.Event()
    release = asyncio.Event()

    async def slow_close() -> None:
        closing.set()
        await release.wait()

    old_stream.close = slow_close
    assert session.cancel_turn("barge_in") is True
    await asyncio.wait_for(closing.wait(), 2.0)

    assert session.start("t2") is True
    await session.begin()
    next_generation = session.generation
    assert session._pump_task is not None
    assert session._reader_task is not None

    release.set()
    assert session._cancel_task is not None
    await asyncio.wait_for(session._cancel_task, 2.0)

    assert session.state == "listening"
    assert session.turn_id == "t2"
    assert session.generation == next_generation
    assert session._pump_task is not None
    assert session._reader_task is not None
    await session.shutdown()


async def test_cancel_in_thinking_does_not_cancel_the_next_turn_speaker():
    """旧轮在 THINKING 被取消后，其 finally 不得取消新轮的 speaker 任务。"""
    first_chat_started = asyncio.Event()
    release_chat = asyncio.Event()
    calls = {"count": 0}

    async def gated_chat(
        conversation_id: str, text: str, *, cancel_reason: str = "barge_in"
    ):
        calls["count"] += 1
        if calls["count"] == 1:
            first_chat_started.set()
            await release_chat.wait()
            return
        yield {"type": "content", "delta": SENTENCE_ONE}
        yield {"type": "done", "finish_reason": "stop"}

    runtime, bus = make_runtime(chat=gated_chat)
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config(), turn_id="t1")
    await session.begin()
    assert await session.accept_frame(uplink(0)) is True
    assert session.end_user_speech() is True
    await asyncio.wait_for(first_chat_started.wait(), 2.0)
    assert session.state == "thinking"

    # 把旧轮清理挂起，等新轮建好自己的 speaker 后再执行旧轮的 finally。
    original_cleanup = session._cleanup_resources
    allow_cleanup = asyncio.Event()

    async def delayed_cleanup(resources):
        await allow_cleanup.wait()
        await original_cleanup(resources)

    session._cleanup_resources = delayed_cleanup  # type: ignore[method-assign]

    assert session.cancel_turn("barge_in") is True
    assert session.start("t2") is True
    await session.begin()
    assert await session.accept_frame(uplink(0)) is True
    assert session.end_user_speech() is True

    await wait_until(lambda: session._speaker_task is not None)
    next_speaker = session._speaker_task
    allow_cleanup.set()
    release_chat.set()
    assert session._cancel_task is not None
    await asyncio.wait_for(session._cancel_task, 2.0)

    # 旧轮 finally 执行完毕后，新轮的 speaker 仍然存在且未被取消。
    assert session._speaker_task is next_speaker
    assert not next_speaker.cancelled()
    assert session.turn_id == "t2"
    assert [e for e in sink.errors if e.get("code") == "internal_error"] == []

    await wait_until(lambda: len(sink.segments) >= 1)
    last_seq = sink.segments[-1]["segment"]["segment_seq"]
    assert session.segment_played(last_seq) is True
    await wait_until(lambda: session.state == "idle")

    assert bus.only("turn_end")[-1]["turn_id"] == "t2"
    assert bus.only("turn_end")[-1]["finish_reason"] == "stop"
    assert calls["count"] == 2
    await session.shutdown()


async def test_cancel_during_turn_start_leaves_no_live_timers():
    bus = BlockingBus("turn_start")
    runtime, _bus = make_runtime(bus=bus)
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    begin = asyncio.create_task(session.begin())
    await asyncio.wait_for(bus.entered.wait(), 2.0)
    assert session.cancel_turn("barge_in") is True
    bus.release.set()
    await asyncio.wait_for(begin, 2.0)

    assert session.state == "idle"
    assert session._timers == set()
    await session.shutdown()


async def test_shutdown_waits_for_the_running_cancel_cleanup():
    runtime, _bus = make_runtime()
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())
    await session.begin()
    old_stream = session._asr
    assert old_stream is not None

    closing = asyncio.Event()
    release = asyncio.Event()

    async def slow_close() -> None:
        closing.set()
        await release.wait()

    old_stream.close = slow_close
    assert session.cancel_turn("barge_in") is True
    await asyncio.wait_for(closing.wait(), 2.0)

    async def release_soon() -> None:
        await asyncio.sleep(0.05)
        release.set()

    releaser = asyncio.create_task(release_soon())
    await session.shutdown()
    await releaser

    assert session._cancel_task is not None
    assert session._cancel_task.done()


async def test_playback_timeout_claims_the_turn_before_sending_the_error():
    runtime, bus = make_runtime()
    sink = RecordingSink()
    session = make_session(
        runtime, sink, config=fast_config(playback_done_timeout=0.0)
    )
    await session.begin()
    session.state = "thinking"
    assert session.mark_speaking() is True
    session.last_segment_seq = 0
    gen = session.generation
    turn_id = session.turn_id
    assert turn_id is not None

    observed: dict[str, object] = {}

    async def fake_error(code, tid, **kwargs):
        # 终态必须已经同步占用：迟到回执不能再完成同一轮。
        observed["state"] = session.state
        observed["turn_id"] = session.turn_id
        observed["segment_played"] = session.segment_played(0)

    session._send_voice_error = fake_error  # type: ignore[method-assign]
    await session._playback_watchdog(gen, turn_id)

    assert observed["state"] == "idle"
    assert observed["turn_id"] is None
    assert observed["segment_played"] is False
    assert len(bus.only("turn_end")) == 1
    await session.shutdown()


async def test_normal_turn_streams_two_segments_and_completes_on_last_played():
    chat = scripted_chat(SENTENCE_ONE, SENTENCE_TWO)
    runtime, bus = make_runtime(chat=chat)
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()
    assert session.conversation_id == "conv-1"
    for index in range(3):
        assert await session.accept_frame(uplink(index)) is True
    assert session.end_user_speech() is True

    await wait_until(lambda: session.state == "speaking")
    await wait_until(lambda: len(sink.segments) == 2 and len(sink.audio) >= 2)

    assert [segment["segment"]["text"] for segment in sink.segments] == [
        SENTENCE_ONE,
        SENTENCE_TWO,
    ]
    assert sink.segments[0]["segment"]["last"] is False
    assert sink.segments[1]["segment"]["last"] is True
    assert sink.segments[0]["segment"]["turn_id"] == "t1"
    assert sink.segments[0]["segment"]["segment_seq"] == 0
    assert sink.segments[1]["segment"]["segment_seq"] == 1
    # 每段第一个包都与 voice.segment 原子发送；turn_seq 跨段连续。
    assert sink.segments[0]["turn_seq"] == 0
    assert sink.segments[1]["turn_seq"] == 2
    assert [item["turn_seq"] for item in sink.audio] == [1, 3]
    assert sink.audio[-1]["flags"] == FLAG_LAST
    assert sink.audio[0]["flags"] == 0

    assert session.segment_played(0) is True
    await wait_until(lambda: len(bus.only("tts_segment_end")) == 1)
    assert session.state == "speaking"

    assert session.segment_played(1) is True
    await wait_until(lambda: session.state == "idle")

    assert chat.calls == [("conv-1", "请给我讲一个故事。", "barge_in")]
    assert bus.names() == [
        "turn_start",
        "asr_final",
        "tts_segment_start",
        "tts_segment_start",
        "tts_segment_end",
        "tts_segment_end",
        "turn_end",
    ]
    end = bus.only("turn_end")[0]
    assert end["finish_reason"] == "stop"
    assert end["interrupted"] is False
    assert session.last_finish_reason == "stop"
    assert session.turn_id is None


async def test_empty_asr_final_is_no_speech_and_never_calls_llm():
    chat = scripted_chat(SENTENCE_ONE)
    runtime, bus = make_runtime(chat=chat, asr_script=[{"final": ""}])
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()
    await session.accept_frame(uplink(0))
    session.end_user_speech()
    await wait_until(lambda: session.state == "idle")

    assert chat.calls == []
    assert sink.errors[-1]["code"] == "no_speech"
    assert sink.errors[-1]["fatal"] is True
    assert bus.only("turn_end")[0]["finish_reason"] == "error"


async def test_asr_error_is_reported_as_unavailable():
    runtime, bus = make_runtime(asr_script=[{"error": "asr_unavailable"}])
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()
    await session.accept_frame(uplink(0))
    session.end_user_speech()
    await wait_until(lambda: session.state == "idle")

    assert sink.errors[-1]["code"] == "asr_unavailable"
    assert bus.only("turn_end")[0]["finish_reason"] == "error"


async def test_tts_first_packet_timeout_reports_unavailable():
    runtime, bus = make_runtime(
        chat=scripted_chat(SENTENCE_ONE),
        tts=MockTtsProvider(packets_per_segment=2, delay=0.2),
    )
    sink = RecordingSink()
    session = make_session(
        runtime,
        sink,
        config=fast_config(tts_first_packet_timeout=0.05),
    )

    await session.begin()
    await session.accept_frame(uplink(0))
    session.end_user_speech()
    await wait_until(lambda: session.state == "idle", timeout=2.0)

    assert sink.errors[-1]["code"] == "tts_unavailable"
    assert sink.errors[-1]["fatal"] is True
    assert bus.only("turn_end")[0]["finish_reason"] == "error"


async def test_tts_without_any_audio_is_unavailable_and_sends_no_segment():
    runtime, bus = make_runtime(
        chat=scripted_chat(SENTENCE_ONE),
        tts=MockTtsProvider(packets_per_segment=0),
    )
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()
    await session.accept_frame(uplink(0))
    session.end_user_speech()
    await wait_until(lambda: session.state == "idle", timeout=2.0)

    # 零音频不是“纯文本段”：设备无法播放/回执 last 段，按 TTS 失败处理。
    assert sink.segments == []
    assert sink.errors[-1]["code"] == "tts_unavailable"
    assert sink.errors[-1]["fatal"] is True
    assert bus.only("turn_end")[0]["finish_reason"] == "error"


async def test_missing_playback_report_times_out():
    runtime, bus = make_runtime(
        chat=scripted_chat(SENTENCE_ONE, SENTENCE_TWO)
    )
    sink = RecordingSink()
    session = make_session(
        runtime,
        sink,
        config=fast_config(playback_done_timeout=0.05),
    )

    await session.begin()
    await session.accept_frame(uplink(0))
    session.end_user_speech()
    await wait_until(lambda: session.state == "idle", timeout=2.0)

    assert session.last_finish_reason == "playback_timeout"
    assert sink.errors[-1]["code"] == "internal_error"
    assert bus.only("turn_end")[0]["finish_reason"] == "playback_timeout"


async def test_idle_timeout_ends_listening_turn():
    runtime, bus = make_runtime()
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config(idle_timeout=0.05))

    await session.begin()
    await wait_until(lambda: session.state == "idle", timeout=2.0)

    assert sink.errors[-1]["code"] == "idle_timeout"
    assert sink.errors[-1]["fatal"] is True
    assert bus.only("turn_end")[0]["finish_reason"] == "timeout"


async def test_max_turn_watchdog_ends_turn_and_bumps_generation():
    runtime, bus = make_runtime()
    sink = RecordingSink()
    session = make_session(
        runtime,
        sink,
        config=fast_config(max_turn=0.05, idle_timeout=5.0),
    )
    generation = session.generation

    await session.begin()
    await session.accept_frame(uplink(0))
    await wait_until(lambda: session.state == "idle", timeout=2.0)

    assert session.generation > generation
    assert sink.errors[-1]["code"] == "internal_error"
    assert bus.only("turn_end")[0]["finish_reason"] == "timeout"


async def test_frames_are_dropped_during_thinking_and_speaking():
    runtime, _bus = make_runtime(
        chat=scripted_chat(SENTENCE_ONE, SENTENCE_TWO),
        asr_script=[{"final": "请给我讲一个故事。", "final_delay": 0.1}],
    )
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()
    await session.accept_frame(uplink(0))
    session.end_user_speech()
    assert session.state == "thinking"
    frames_before = session.frames_received

    assert await session.accept_frame(uplink(1)) is False
    assert session.frames_received == frames_before

    await wait_until(lambda: session.state == "speaking")
    assert await session.accept_frame(uplink(2)) is False
    assert session.frames_received == frames_before
    session.cancel_turn("user_stop")
    await wait_until(lambda: session.state == "idle")


async def test_cancel_turn_marks_interrupted_and_drops_late_segments():
    runtime, bus = make_runtime(
        chat=scripted_chat(SENTENCE_ONE, SENTENCE_TWO)
    )
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()
    await session.accept_frame(uplink(0))
    session.end_user_speech()
    await wait_until(lambda: session.state == "speaking")
    generation = session.generation

    assert session.cancel_turn("barge_in") is True
    assert session.state == "idle"
    assert session.generation > generation
    await wait_until(lambda: len(bus.only("turn_end")) == 1)

    end = bus.only("turn_end")[0]
    assert end["interrupted"] is True
    assert end["finish_reason"] == "interrupted"
    # 本轮已取消，迟到的回执不推进任何状态。
    assert session.segment_played(0) is False


async def test_chat_error_is_reported_and_turn_ends():
    runtime, bus = make_runtime(
        chat=scripted_chat(error="该对话当前正在生成回复，请稍候")
    )
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()
    await session.accept_frame(uplink(0))
    session.end_user_speech()
    await wait_until(lambda: session.state == "idle")

    assert sink.errors[-1]["code"] == "busy"
    assert sink.errors[-1]["fatal"] is True
    assert bus.only("turn_end")[0]["finish_reason"] == "error"


async def test_silence_frames_do_not_break_the_uplink():
    runtime, _bus = make_runtime(asr_script=[{"final": ""}])
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())
    await session.begin()
    silence = AudioFrame(
        kind=KIND_UPLINK,
        flags=0x02,
        seq=0,
        turn_seq=0,
        payload=b"",
    )
    assert await session.accept_frame(silence) is True
    assert session.frames_received == 1
    session.end_user_speech()
    await wait_until(lambda: session.state == "idle")


async def test_shutdown_while_speaking_cancels_the_turn():
    runtime, _bus = make_runtime(
        chat=scripted_chat(SENTENCE_ONE, SENTENCE_TWO)
    )
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    await session.begin()
    await session.accept_frame(uplink(0))
    session.end_user_speech()
    await wait_until(lambda: session.state == "speaking")

    await session.shutdown()

    assert session.state == "closed"
    assert session.turn_id is None


async def test_voice_events_never_log_text(caplog):
    secret_reply = "这是一段绝不能进日志的秘密回复。"
    secret_asr = "这是秘密识别文本。"
    runtime, _bus = make_runtime(
        chat=scripted_chat(secret_reply),
        asr_script=[{"final": secret_asr}],
    )
    sink = RecordingSink()
    session = make_session(runtime, sink, config=fast_config())

    with caplog.at_level("DEBUG"):
        await session.begin()
        await session.accept_frame(uplink(0))
        session.end_user_speech()
        await wait_until(lambda: session.state == "speaking")
        session.cancel_turn("user_stop")
        await wait_until(lambda: session.state == "idle")

    assert secret_reply not in caplog.text
    assert secret_asr not in caplog.text
