"""语音轮次状态、帧门控与完整 Mock 管线。

Block A 只负责「一轮是否开始、哪些帧可以进入下游、何时结束/取消」；
本块在同一对象上增加可选的 `VoiceRuntime`：ASR → 聊天生成 → 流式分句 →
TTS → 节流下行 → `segment_played`。没有 runtime 时完全保持 Block A 的同步语义。
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from companion.server.app.audio.downlink import (
    AudioPacket,
    DownlinkClosed,
    DownlinkSender,
    SegmentStart,
)
from companion.server.app.audio.framing import (
    KIND_UPLINK,
    AudioFrame,
)
from companion.server.app.audio.ogg import (
    OggError,
    OggOpusDemuxer,
    OggOpusMuxer,
    opus_packet_duration_ms,
)
from companion.server.app.audio.providers import (
    ASR_ERROR,
    ASR_FINAL,
    ASR_PARTIAL,
    AsrEvent,
    ProviderError,
)
from companion.server.app.audio.runtime import VoiceConfig, VoiceRuntime
from companion.server.app.audio.sentences import SentenceSegmenter
from companion.server.app.services.clock import now_ms

logger = logging.getLogger(__name__)

IDLE = "idle"
LISTENING = "listening"
THINKING = "thinking"
SPEAKING = "speaking"
CLOSED = "closed"

ACTIVE_STATES = {LISTENING, THINKING, SPEAKING}


@dataclass
class _TurnResources:
    """一轮持有的异步资源快照。

    结束/取消时先同步摘除，再在后台清理：这样设备可以在清理尚未完成时
    合法开启下一轮，旧清理只操作自己捕获的对象，不会关掉新轮的 ASR/sender。
    """

    asr: Any
    pump: asyncio.Task | None
    reader: asyncio.Task | None
    speaker: asyncio.Task | None
    turn: asyncio.Task | None
    downlink: DownlinkSender | None
    timers: list[asyncio.Task]

NON_FATAL_ERRORS = {"busy", "invalid_turn", "invalid_frame"}

OnFrame = Callable[[AudioFrame], Awaitable[None]]


class VoiceSession:
    """每台设备至多一个；`start()` 复用对象开启新一轮。"""

    def __init__(
        self,
        device_id: str,
        *,
        on_frame: OnFrame | None = None,
        runtime: VoiceRuntime | None = None,
        config: VoiceConfig | None = None,
        sink: Any | None = None,
    ) -> None:
        self.device_id = device_id
        self.on_frame = on_frame
        self._runtime = runtime
        self._config = config or (runtime.config if runtime is not None else VoiceConfig())
        # 下行 sink 只在有 runtime 时使用；测试传 RecordingSink。
        self._sink = sink
        self.state = IDLE
        self.turn_id: str | None = None
        # 最近结束/取消的轮次：设备迟到的 segment_played 据此静默忽略，
        # 而不是误报 invalid_turn（协议要求同一 (turn_id, segment_seq) 幂等）。
        self.last_turn_id: str | None = None
        # 每次 start/cancel/close 递增；旧 ASR/TTS/LLM 结果据此丢弃。
        self.generation = 0
        self.frames_received = 0
        self.conversation_id: str | None = None
        # 本轮最后一个 TTS 段的序号；设备回执到这个序号才结束轮次。
        self.last_segment_seq: int | None = None
        self.last_finish_reason: str | None = None
        self._last_turn_seq = -1
        self._last_segment_seq = -1

        self._begin_started = False
        # `voice.end` 早于 `begin()` 打开 ASR 时置位：等 begin 完成后立即开轮，
        # 否则会误报 asr_unavailable。Block A（无 runtime）永远为 False。
        self._end_pending = False
        self._muxer: OggOpusMuxer | None = None
        self._uplink_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._uplink_drained = asyncio.Event()
        self._activity_event = asyncio.Event()
        self._asr: Any = None
        self._asr_done = asyncio.Event()
        self._asr_last_text = ""
        self._asr_error = ""
        self._pump_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._speaker_task: asyncio.Task | None = None
        self._turn_task: asyncio.Task | None = None
        self._cancel_task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None
        self._downlink: DownlinkSender | None = None
        self._timers: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Block A 同步语义（无 runtime 时完全不变）
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def start(self, turn_id: str) -> bool:
        if not isinstance(turn_id, str) or not turn_id:
            return False
        # 同一 turn_id 的重复 start 视为重传：任意活跃状态下幂等成功，
        # 不重置 generation，避免设备丢 ack 后重试把已收/已播状态清空。
        if self.turn_id == turn_id and self.state in ACTIVE_STATES:
            return True
        if self.state != IDLE:
            return False
        self.turn_id = turn_id
        self.generation += 1
        self.frames_received = 0
        self._last_turn_seq = -1
        self._last_segment_seq = -1
        self.conversation_id = None
        self.last_segment_seq = None
        self.last_finish_reason = None
        self._reset_turn_buffers()
        self.state = LISTENING
        return True

    def _reset_turn_buffers(self) -> None:
        self._begin_started = False
        self._end_pending = False
        # 防御：任何遗留计时器都要显式取消，不能只丢弃 set 引用。
        for timer in list(self._timers):
            timer.cancel()
        self._timers = set()
        self._asr = None
        self._asr_done = asyncio.Event()
        self._asr_last_text = ""
        self._asr_error = ""
        self._uplink_queue = asyncio.Queue(
            maxsize=max(1, math.ceil(self._config.uplink_buffer_seconds * 1000 / max(self._config.frame_ms, 1)))
        )
        self._uplink_drained = asyncio.Event()
        self._activity_event = asyncio.Event()
        self._pump_task = None
        self._reader_task = None
        self._speaker_task = None
        self._turn_task = None
        self._downlink = None
        self._timers = set()
        self._muxer = (
            OggOpusMuxer(sample_rate=self._config.sample_rate)
            if self._runtime is not None
            else None
        )

    async def accept_frame(self, frame: AudioFrame) -> bool:
        if self.state != LISTENING:
            return False
        if frame.kind != KIND_UPLINK:
            return False
        # 同一轮内只接受严格递增的 turn_seq：重复/回退帧会让 ASR 重复识别。
        if frame.turn_seq <= self._last_turn_seq:
            return False
        self._last_turn_seq = frame.turn_seq
        self.frames_received += 1
        self._activity_event.set()

        if self._runtime is not None:
            if frame.silence and not frame.payload:
                # 静音帧只用于告诉服务端“用户还在说话”，没有 Opus 包可送。
                return True
            muxer = self._muxer
            if muxer is None:
                return False
            try:
                page = muxer.push(frame.payload)
            except (OggError, ValueError):
                # 帧头合法但 Opus TOC 不可解析：不送 ASR，也不再回错误（网关
                # 已对畸形帧限流），避免坏包污染识别。
                return False
            if page:
                self._put_uplink(page)
            return True

        if self.on_frame is not None:
            await self.on_frame(frame)
        return True

    def end_user_speech(self) -> bool:
        if self.state != LISTENING:
            return False
        self.state = THINKING
        # 唤醒 idle watchdog，让它在下一轮循环看到状态已变并退出。
        self._activity_event.set()
        if self._runtime is not None:
            if self._asr is None:
                # `voice.end` 早于 `begin()` 打开 ASR（网关只 create_task，
                # 真实现还要 await DB）：挂起，由 begin() 打开 ASR 后补开轮次。
                # 只有 begin() 失败（unbound/busy 等）会把 state 置回 IDLE。
                self._end_pending = True
                return True
            self._start_turn_task()
        return True

    def _start_turn_task(self) -> None:
        if (
            self.state != THINKING
            or self.turn_id is None
            or self._turn_task is not None
        ):
            return
        gen = self.generation
        turn_id = self.turn_id
        self._turn_task = asyncio.create_task(self._run_turn(gen, turn_id))

    def mark_speaking(self) -> bool:
        if self.state != THINKING:
            return False
        self.state = SPEAKING
        return True

    def segment_played(self, segment_seq: int) -> bool:
        if self.state != SPEAKING:
            return False
        if not isinstance(segment_seq, int) or isinstance(segment_seq, bool):
            return False
        if segment_seq <= self._last_segment_seq:
            return False
        if (
            self._runtime is not None
            and self.last_segment_seq is not None
            and segment_seq > self.last_segment_seq
        ):
            # 设备不可能播放尚未下发的段；越界回执视为无效，不能提前结束轮次。
            return False
        self._last_segment_seq = segment_seq
        if self._runtime is not None:
            gen = self.generation
            turn_id = self.turn_id
            if turn_id is not None:
                asyncio.create_task(
                    self._on_segment_played(gen, turn_id, segment_seq)
                )
        return True

    def cancel(self) -> None:
        if self._runtime is not None:
            if self.cancel_turn("user_stop"):
                return
        if self.state == CLOSED:
            return
        self.generation += 1
        self.state = IDLE
        self.last_turn_id = self.turn_id
        self.turn_id = None

    def close(self) -> None:
        if self.state == CLOSED:
            return
        turn_id = self.turn_id
        self.generation += 1
        self.state = CLOSED
        self.last_turn_id = turn_id
        self.turn_id = None
        # 先同步摘除本轮资源，避免后台清理期间新连接/新轮次被误伤。
        resources = self._detach_turn_resources()
        if self._runtime is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._close_task = loop.create_task(self._close_cleanup(resources))

    async def shutdown(self) -> None:
        """有界关停：取消管线任务、关闭 ASR，不向已断开的链路发消息。"""
        self.close()
        current = asyncio.current_task()
        tasks = [
            task
            for task in (self._close_task, self._cancel_task)
            if task is not None and task is not current
        ]
        for task in tasks:
            try:
                await asyncio.wait_for(asyncio.shield(task), 2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    # ------------------------------------------------------------------
    # Block B：完整轮次
    # ------------------------------------------------------------------

    async def begin(self) -> None:
        """由网关在 `voice.start` 后调度；解析会话、打开 ASR、启动计时。"""
        runtime = self._runtime
        if runtime is None or self.turn_id is None:
            return
        # 允许 THINKING：`voice.end` 可能已经先到，此时由 `_end_pending`
        # 在 ASR 打开后接续开轮。
        if self.state not in (LISTENING, THINKING):
            return
        if self._asr is not None or self._begin_started:
            # 同一 turn_id 的重复 voice.start 是幂等的；不要重复打开 ASR。
            return
        self._begin_started = True
        gen = self.generation
        turn_id = self.turn_id

        if self._sink is None:
            await self._fail("internal_error", gen, turn_id)
            return

        conversation_id: str | None = None
        if runtime.resolve_conversation is not None:
            try:
                conversation_id = await runtime.resolve_conversation(self.device_id)
            except Exception:
                logger.exception("voice resolve_conversation failed: %s", self.device_id)
                conversation_id = None
        if not self._turn_ok(gen, turn_id):
            return
        if not conversation_id:
            await self._fail("unbound", gen, turn_id)
            return
        if runtime.conversation_busy is not None:
            busy = False
            try:
                busy = bool(runtime.conversation_busy(conversation_id))
            except Exception:
                logger.exception("voice conversation_busy failed: %s", self.device_id)
            if not self._turn_ok(gen, turn_id):
                return
            if busy:
                # 对话锁被 App 占用：致命，设备回 IDLE；不排队、不打断 App。
                await self._fail("busy", gen, turn_id, fatal=True)
                return

        if runtime.asr is None:
            await self._fail("asr_unavailable", gen, turn_id)
            return
        try:
            stream = runtime.asr.open(
                device_id=self.device_id,
                turn_id=turn_id,
                sample_rate=self._config.sample_rate,
            )
        except Exception:
            logger.exception("voice open ASR failed: %s", self.device_id)
            await self._fail("asr_unavailable", gen, turn_id)
            return

        if not self._turn_ok(gen, turn_id):
            await _close_quietly(stream)
            return

        self.conversation_id = conversation_id
        self._asr = stream
        # 队列与事件按代次捕获：旧泵即使清理稍晚，也不会读写下一轮的队列/事件。
        self._pump_task = asyncio.create_task(
            self._uplink_pump(
                gen, stream, self._uplink_queue, self._uplink_drained
            )
        )
        self._reader_task = asyncio.create_task(self._read_asr(gen, turn_id, stream))
        await self._emit("turn_start", turn_id=turn_id, conversation_id=conversation_id)
        if not self._turn_ok(gen, turn_id):
            # `turn_start` 发布期间被取消/断开：本轮资源已被快照清理接管，
            # 不能再用当前（可能已属于新轮）的 self 引用创建计时器。
            return
        self._spawn_timer(self._idle_watchdog(gen, turn_id))
        self._spawn_timer(self._max_turn_watchdog(gen, turn_id))
        if self._end_pending:
            # `voice.end` 在 ASR 打开前就到了：现在补开轮次。
            self._end_pending = False
            if self.state == THINKING:
                self._start_turn_task()

    # -- uplink / ASR ---------------------------------------------------

    def _put_uplink(self, item: bytes | None) -> None:
        queue = self._uplink_queue
        if item is None:
            # 结束标记必须入队：队列满时丢弃最旧的音频页，不能丢哨兵。
            while True:
                try:
                    queue.put_nowait(None)
                    return
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
        while True:
            try:
                queue.put_nowait(item)
                return
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

    def _touch_activity(self) -> None:
        self._activity_event.set()

    async def _uplink_pump(
        self,
        gen: int,
        stream: Any,
        queue: asyncio.Queue[bytes | None],
        drained: asyncio.Event,
    ) -> None:
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if gen != self.generation:
                    break
                try:
                    await stream.send(item)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("voice ASR send failed: %s", self.device_id)
                    self._asr_error = "asr_unavailable"
                    self._asr_done.set()
                    break
        except asyncio.CancelledError:
            raise
        finally:
            drained.set()

    async def _read_asr(self, gen: int, turn_id: str, stream: Any) -> None:
        try:
            async for event in stream.events():
                if gen != self.generation:
                    return
                if not isinstance(event, AsrEvent):
                    continue
                if event.kind == ASR_PARTIAL:
                    self._asr_last_text = event.text
                    self._touch_activity()
                    await self._emit(
                        "asr_partial", turn_id=turn_id, text=event.text
                    )
                elif event.kind == ASR_FINAL:
                    self._asr_last_text = event.text
                    self._asr_done.set()
                    return
                elif event.kind == ASR_ERROR:
                    self._asr_error = event.code or "asr_unavailable"
                    self._asr_done.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            if gen == self.generation:
                logger.exception("voice ASR stream failed: %s", self.device_id)
                self._asr_error = "asr_unavailable"
        finally:
            if gen == self.generation:
                self._asr_done.set()

    # -- turn driver ----------------------------------------------------

    async def _run_turn(self, gen: int, turn_id: str) -> None:
        try:
            text = await self._finish_asr(gen, turn_id)
            if text is None:
                return
            await self._generate_and_speak(gen, turn_id, text)
        except ProviderError as exc:
            await self._fail(exc.code, gen, turn_id)
        except DownlinkClosed:
            # 设备链路已不可写：本轮有界结束，不广播“离线性”错误。
            logger.debug("voice downlink closed: %s", self.device_id)
            await self._complete_turn(gen, turn_id, "error")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("voice turn failed: %s", self.device_id)
            await self._fail("internal_error", gen, turn_id)

    async def _finish_asr(self, gen: int, turn_id: str) -> str | None:
        stream = self._asr
        if stream is None:
            raise ProviderError("asr_unavailable")

        muxer = self._muxer
        if muxer is not None and self.frames_received:
            tail = muxer.finish()
            if tail:
                self._put_uplink(tail)
        self._put_uplink(None)
        try:
            await asyncio.wait_for(self._uplink_drained.wait(), 2.0)
        except asyncio.TimeoutError:
            logger.debug("voice uplink drain timed out: %s", self.device_id)

        if not self._turn_ok(gen, turn_id):
            return None
        try:
            await stream.finish()
        except Exception:
            logger.exception("voice ASR finish failed: %s", self.device_id)
            raise ProviderError("asr_unavailable")

        try:
            await asyncio.wait_for(
                self._asr_done.wait(), self._config.asr_final_grace
            )
        except asyncio.TimeoutError:
            # 用已有的 partial 收尾；没有 partial 就是 no_speech。
            pass
        if not self._turn_ok(gen, turn_id):
            return None
        if self._asr_error:
            raise ProviderError(self._asr_error)

        text = (self._asr_last_text or "").strip()
        if not text:
            raise ProviderError("no_speech")
        await self._emit("asr_final", turn_id=turn_id, text=text)
        return text

    async def _generate_and_speak(
        self, gen: int, turn_id: str, text: str
    ) -> None:
        runtime = self._runtime
        conversation_id = self.conversation_id
        if runtime is None or runtime.chat_factory is None or not conversation_id:
            raise ProviderError("internal_error")
        if self._sink is None:
            raise ProviderError("internal_error")

        sender = DownlinkSender(
            self._sink,
            buffer_seconds=self._config.downlink_buffer_seconds,
            frame_ms=self._config.frame_ms,
            generation=gen,
        )
        self._downlink = sender
        sender.start()
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=8)
        # speaker 任务必须用局部变量持有：旧轮的 finally 可能在新轮启动后才
        # 执行，若直接操作 `self._speaker_task`，会把新轮刚建好的 speaker
        # 取消/清空（下一轮随后 `await None` → fatal internal_error）。
        speaker_task = asyncio.create_task(
            self._speaker(gen, turn_id, queue, sender)
        )
        self._speaker_task = speaker_task

        segmenter = SentenceSegmenter()
        held: str | None = None
        try:
            async for chunk in runtime.chat_factory(
                conversation_id, text, cancel_reason="barge_in"
            ):
                if not self._turn_ok(gen, turn_id):
                    return
                chunk_type = chunk.get("type") if isinstance(chunk, dict) else None
                if chunk_type == "content":
                    delta = chunk.get("delta") or ""
                    if not isinstance(delta, str):
                        continue
                    for sentence in segmenter.feed(delta):
                        held = await self._hold_then_push(held, sentence, queue)
                elif chunk_type == "error":
                    message = str(chunk.get("error") or "")
                    # 会话锁竞态：ChatOrchestrator 的 lock.locked() 分支。
                    code = "busy" if "正在生成" in message else "internal_error"
                    fatal = code == "busy"
                    await self._fail(code, gen, turn_id, fatal=fatal)
                    return
            if not self._turn_ok(gen, turn_id):
                return
            for sentence in segmenter.flush():
                held = await self._hold_then_push(held, sentence, queue)
            if held is None:
                raise ProviderError("internal_error")
            await queue.put(("last", held))
            await queue.put(("end", None))
            await speaker_task
        finally:
            # speaker 异常时也要让 chat 生产者退出，避免队列永久阻塞。
            # 只清理本轮自己的任务；旧轮不得碰新轮的 `self._speaker_task`。
            if not speaker_task.done():
                speaker_task.cancel()
                await asyncio.gather(speaker_task, return_exceptions=True)
            if self._speaker_task is speaker_task:
                self._speaker_task = None

        if not self._turn_ok(gen, turn_id):
            return
        self._spawn_timer(self._playback_watchdog(gen, turn_id))

    async def _hold_then_push(
        self, held: str | None, sentence: str, queue: asyncio.Queue[Any]
    ) -> str:
        """保留最后一句，确保最后一段能带 `last=True`。"""
        if held is not None:
            await queue.put(("segment", held))
        return sentence

    async def _speaker(
        self,
        gen: int,
        turn_id: str,
        queue: asyncio.Queue[Any],
        sender: DownlinkSender,
    ) -> None:
        seq = 0
        while True:
            kind, text = await queue.get()
            if kind == "end":
                break
            if not self._turn_ok(gen, turn_id):
                return
            if self.state == THINKING:
                self.mark_speaking()
            is_last = kind == "last"
            if is_last:
                self.last_segment_seq = seq
            await self._emit(
                "tts_segment_start",
                turn_id=turn_id,
                segment_seq=seq,
                text=text,
            )
            if not await sender.enqueue(
                SegmentStart(seq, text, is_last, turn_id), generation=gen
            ):
                raise DownlinkClosed("downlink closed")
            await self._synthesize(gen, turn_id, text, seq, is_last, sender)
            seq += 1

        try:
            # 末段没有任何音频时也要把它的文本/`last` 发给设备。
            if not await sender.flush(generation=gen):
                raise DownlinkClosed("downlink closed")
            # 下行的“发送队列排空”与设备播放完成是两件事：前者只受链路
            # 写阻塞影响，不能复用很短的 playback_done_timeout。
            idle = await sender.wait_idle(
                max(self._config.playback_done_timeout, 2.0)
            )
        except DownlinkClosed as exc:
            raise exc
        if not idle:
            raise ProviderError("internal_error")

    async def _synthesize(
        self,
        gen: int,
        turn_id: str,
        text: str,
        segment_seq: int,
        is_last: bool,
        sender: DownlinkSender,
    ) -> None:
        runtime = self._runtime
        if runtime is None or runtime.tts is None:
            raise ProviderError("tts_unavailable")
        iterator = runtime.tts.synthesize(
            text, turn_id=turn_id, segment_seq=segment_seq
        ).__aiter__()
        demuxer = (
            OggOpusDemuxer()
            if self._config.tts_container == "ogg"
            else None
        )
        previous: bytes | None = None
        packets = 0
        while True:
            timeout = (
                self._config.tts_first_packet_timeout
                if packets == 0
                else self._config.segment_idle_timeout
            )
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                raise ProviderError("tts_unavailable")
            except ProviderError:
                raise
            except Exception:
                logger.exception("voice TTS stream failed: %s", self.device_id)
                raise ProviderError("tts_unavailable")
            if not self._turn_ok(gen, turn_id):
                return
            raw_packets: list[bytes]
            if demuxer is None:
                raw_packets = [chunk] if chunk else []
            else:
                try:
                    raw_packets = demuxer.feed(chunk)
                except OggError:
                    continue
            for packet in raw_packets:
                if not packet:
                    continue
                packets += 1
                if previous is not None:
                    await self._enqueue_packet(
                        previous, sender, gen=gen, last=False
                    )
                previous = packet
        if previous is None:
            raise ProviderError("tts_unavailable")
        await self._enqueue_packet(previous, sender, gen=gen, last=is_last)

    async def _enqueue_packet(
        self,
        packet: bytes,
        sender: DownlinkSender,
        *,
        gen: int,
        last: bool,
    ) -> None:
        duration = opus_packet_duration_ms(packet)
        if duration is None:
            duration = float(self._config.frame_ms)
        ok = await sender.enqueue(
            AudioPacket(packet, duration, last_in_turn=last), generation=gen
        )
        if not ok:
            raise DownlinkClosed("downlink closed")

    # -- device reports / completion ------------------------------------

    async def _on_segment_played(
        self, gen: int, turn_id: str, segment_seq: int
    ) -> None:
        if not self._turn_ok(gen, turn_id) or self.state != SPEAKING:
            return
        await self._emit(
            "tts_segment_end", turn_id=turn_id, segment_seq=segment_seq
        )
        if self.last_segment_seq is not None and segment_seq >= self.last_segment_seq:
            await self._complete_turn(gen, turn_id, "stop")

    def cancel_turn(self, reason: str = "user_stop") -> bool:
        """同步置 IDLE 并递增代次；清理在后台有界进行。"""
        if self.state not in ACTIVE_STATES or self.turn_id is None:
            return False
        gen = self.generation
        turn_id = self.turn_id
        self.generation += 1
        self.state = IDLE
        self.last_turn_id = turn_id
        self.turn_id = None
        self.last_finish_reason = "interrupted"
        # 同步摘除：即使旧 ASR.close() 很慢，新轮次也能安全 start()。
        resources = self._detach_turn_resources()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return True
        self._cancel_task = loop.create_task(
            self._cancel_cleanup(turn_id, reason, resources)
        )
        return True

    async def _cancel_cleanup(
        self, turn_id: str, reason: str, resources: _TurnResources
    ) -> None:
        await self._cleanup_resources(resources)
        await self._emit(
            "turn_end",
            turn_id=turn_id,
            interrupted=True,
            finish_reason="interrupted",
        )
        logger.debug(
            "voice turn cancelled: %s reason=%s", self.device_id, reason
        )

    async def _complete_turn(
        self,
        gen: int,
        turn_id: str,
        finish_reason: str,
        *,
        interrupted: bool = False,
        error_code: str | None = None,
        error_fatal: bool = True,
    ) -> None:
        if not self._turn_ok(gen, turn_id):
            return
        # 终态必须同步占用（置 IDLE、递增代次），否则 `await` 期间到达的
        # 末段回执/看门狗可能把同一轮完成两次或对成功轮次发致命错误。
        self.generation += 1
        self.state = IDLE
        self.last_turn_id = turn_id
        self.turn_id = None
        self.last_finish_reason = finish_reason
        resources = self._detach_turn_resources()
        if error_code is not None:
            await self._send_voice_error(
                error_code, turn_id, fatal=error_fatal
            )
        await self._emit(
            "turn_end",
            turn_id=turn_id,
            interrupted=interrupted,
            finish_reason=finish_reason,
        )
        await self._cleanup_resources(resources)

    async def _close_cleanup(self, resources: _TurnResources) -> None:
        await self._cleanup_resources(resources)
        logger.debug("voice session closed: %s", self.device_id)

    def _detach_turn_resources(self) -> _TurnResources:
        """同步摘除当前轮资源；后续清理只操作这份快照。"""
        current = asyncio.current_task()
        resources = _TurnResources(
            asr=self._asr,
            pump=self._pump_task,
            reader=self._reader_task,
            speaker=self._speaker_task,
            turn=self._turn_task,
            downlink=self._downlink,
            timers=list(self._timers),
        )
        self._asr = None
        self._pump_task = None
        self._reader_task = None
        self._speaker_task = None
        self._turn_task = None
        self._downlink = None
        self._timers = set()
        self._begin_started = False
        self._end_pending = False
        if resources.downlink is not None:
            resources.downlink.stop()
        for timer in resources.timers:
            if timer is not current:
                timer.cancel()
        return resources

    async def _cleanup_resources(self, resources: _TurnResources) -> None:
        current = asyncio.current_task()
        tasks: list[asyncio.Task] = []
        for task in (
            resources.pump,
            resources.reader,
            resources.speaker,
            resources.turn,
        ):
            if task is not None and task is not current and not task.done():
                tasks.append(task)
        for task in resources.timers:
            if task is not current and not task.done():
                tasks.append(task)
        for task in tasks:
            task.cancel()
        stream = resources.asr
        if stream is not None:
            try:
                await asyncio.wait_for(stream.close(), 1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                logger.debug("voice ASR close failed: %s", self.device_id)
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), 2.0
                )
            except asyncio.TimeoutError:
                logger.debug("voice task cleanup timed out: %s", self.device_id)

    # -- failures / events / timers -------------------------------------

    async def _fail(
        self,
        code: str,
        gen: int,
        turn_id: str,
        *,
        finish_reason: str = "error",
        fatal: bool | None = None,
    ) -> None:
        if not self._turn_ok(gen, turn_id):
            return
        if fatal is None:
            fatal = code not in NON_FATAL_ERRORS
        # 与看门狗一致：先同步占用终态，再 await 发送错误，避免双发 fatal。
        await self._complete_turn(
            gen,
            turn_id,
            finish_reason,
            error_code=code,
            error_fatal=fatal,
        )

    async def _send_voice_error(
        self, code: str, turn_id: str | None, *, fatal: bool | None = None
    ) -> None:
        if fatal is None:
            fatal = code not in NON_FATAL_ERRORS
        await self._control(
            {
                "v": 2,
                "type": "voice.error",
                "turn_id": turn_id or "",
                "code": code,
                "message": "",
                "fatal": fatal,
                "ts": now_ms(),
            }
        )

    async def _control(self, payload: dict[str, Any]) -> bool:
        sink = self._sink
        if sink is None:
            return False
        try:
            return bool(await sink.send_control(payload))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("voice control send failed: %s", self.device_id)
            return False

    async def _emit(self, event: str, **fields: Any) -> None:
        runtime = self._runtime
        bus = getattr(runtime, "bus", None) if runtime is not None else None
        if bus is None:
            return
        payload: dict[str, Any] = {
            "v": 2,
            "type": "voice.event",
            "device_id": self.device_id,
            "event": event,
            "ts": now_ms(),
        }
        payload.update(fields)
        try:
            result = bus.publish(payload)
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            # 事件里可能含 ASR/TTS 文本，绝不能把 payload 写进日志。
            logger.debug("voice event publish failed: %s event=%s", self.device_id, event)

    def _spawn_timer(self, coro: Awaitable[None]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._timers.add(task)
        task.add_done_callback(self._timers.discard)
        return task

    async def _idle_watchdog(self, gen: int, turn_id: str) -> None:
        while self._turn_ok(gen, turn_id) and self.state == LISTENING:
            self._activity_event.clear()
            try:
                await asyncio.wait_for(
                    self._activity_event.wait(), self._config.idle_timeout
                )
            except asyncio.TimeoutError:
                if self._turn_ok(gen, turn_id) and self.state == LISTENING:
                    await self._fail(
                        "idle_timeout", gen, turn_id, finish_reason="timeout"
                    )
                return
            except asyncio.CancelledError:
                raise

    async def _max_turn_watchdog(self, gen: int, turn_id: str) -> None:
        try:
            await asyncio.sleep(self._config.max_turn)
        except asyncio.CancelledError:
            raise
        if not self._turn_ok(gen, turn_id):
            return
        # 设备不会从 turn_end 得知服务端状态，用致命错误驱动它回 IDLE。
        await self._complete_turn(
            gen, turn_id, "timeout", error_code="internal_error"
        )

    async def _playback_watchdog(self, gen: int, turn_id: str) -> None:
        try:
            await asyncio.sleep(self._config.playback_done_timeout)
        except asyncio.CancelledError:
            raise
        if not self._turn_ok(gen, turn_id) or self.state != SPEAKING:
            return
        # `_complete_turn` 先同步占用终态再 await 发送错误：与末段回执并发时
        # 只有一方能完成轮次，不会给成功轮次补发 fatal internal_error。
        await self._complete_turn(
            gen, turn_id, "playback_timeout", error_code="internal_error"
        )

    def _turn_ok(self, gen: int, turn_id: str) -> bool:
        return (
            self.generation == gen
            and self.turn_id == turn_id
            and self.state != CLOSED
        )


async def _close_quietly(stream: Any) -> None:
    try:
        await asyncio.wait_for(stream.close(), 1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    except Exception:
        pass
