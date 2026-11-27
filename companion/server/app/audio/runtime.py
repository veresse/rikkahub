"""语音管线运行环境：配置、provider 与聊天/会话钩子。

`VoiceSession` 只依赖这里的协议，不直接依赖网关、数据库或 DashScope；
测试用 Mock provider + 脚本化聊天即可离线跑完整轮次。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from companion.server.app.audio.providers import AsrProvider, TtsProvider


@dataclass(frozen=True)
class VoiceConfig:
    """一轮语音的生命周期与节流参数；全部有默认值，便于测试覆盖。"""

    idle_timeout: float = 300.0
    max_turn: float = 60.0
    asr_final_grace: float = 0.3
    tts_first_packet_timeout: float = 5.0
    segment_idle_timeout: float = 15.0
    playback_done_timeout: float = 10.0
    downlink_buffer_seconds: float = 5.0
    uplink_buffer_seconds: float = 5.0
    frame_ms: int = 60
    tts_container: str = "ogg"
    sample_rate: int = 16000


ChatFactory = Callable[..., AsyncIterator[dict[str, Any]]]
ResolveConversation = Callable[[str], Awaitable[str | None]]
ConversationBusy = Callable[[str], bool]


@dataclass
class VoiceRuntime:
    """网关注入给每台设备的共享依赖。

    - `bus`：`EventBus`（必须有可 await 的 `publish`）；事件按顺序发布。
    - `asr` / `tts`：provider；缺失时对应轮次回 `asr_unavailable` /
      `tts_unavailable`。
    - `resolve_conversation(device_id)`：返回会话 id 或 None（未绑定）。
    - `conversation_busy(conversation_id)`：App 文字生成正在占用会话锁。
    - `chat_factory(conversation_id, text, *, cancel_reason=...)`：异步生成器，
      产出 `content/status/tool_result/done/error` 等现有聊天 chunk。
    """

    bus: Any = None
    asr: AsrProvider | None = None
    tts: TtsProvider | None = None
    resolve_conversation: ResolveConversation | None = None
    conversation_busy: ConversationBusy | None = None
    chat_factory: ChatFactory | None = None
    config: VoiceConfig = field(default_factory=VoiceConfig)
