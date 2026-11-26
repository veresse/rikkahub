"""语音轮次状态与音频帧门控。

Block A 只负责「一轮是否开始、哪些帧可以进入下游、何时结束/取消」以及旧帧
过滤；ASR / LLM / TTS 管线在 Block B 接入，因此这里不启动任何外部任务。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from companion.server.app.audio.framing import KIND_UPLINK, AudioFrame

IDLE = "idle"
LISTENING = "listening"
THINKING = "thinking"
SPEAKING = "speaking"
CLOSED = "closed"

ACTIVE_STATES = {LISTENING, THINKING, SPEAKING}

OnFrame = Callable[[AudioFrame], Awaitable[None]]


class VoiceSession:
    """每台设备至多一个；`start()` 复用对象开启新一轮。"""

    def __init__(self, device_id: str, *, on_frame: OnFrame | None = None) -> None:
        self.device_id = device_id
        self.on_frame = on_frame
        self.state = IDLE
        self.turn_id: str | None = None
        # 每次 start/cancel/close 递增；Block B 用它丢弃旧 ASR/TTS/LLM 结果。
        self.generation = 0
        self.frames_received = 0
        self._last_turn_seq = -1
        self._last_segment_seq = -1

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def start(self, turn_id: str) -> bool:
        if self.state != IDLE:
            return False
        if not isinstance(turn_id, str) or not turn_id:
            return False
        self.turn_id = turn_id
        self.generation += 1
        self.frames_received = 0
        self._last_turn_seq = -1
        self._last_segment_seq = -1
        self.state = LISTENING
        return True

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
        if self.on_frame is not None:
            await self.on_frame(frame)
        return True

    def end_user_speech(self) -> bool:
        if self.state != LISTENING:
            return False
        self.state = THINKING
        return True

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
        self._last_segment_seq = segment_seq
        return True

    def cancel(self) -> None:
        if self.state == CLOSED:
            return
        self.generation += 1
        self.state = IDLE
        self.turn_id = None

    def close(self) -> None:
        if self.state == CLOSED:
            return
        self.generation += 1
        self.state = CLOSED
        self.turn_id = None
