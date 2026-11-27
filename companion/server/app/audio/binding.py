"""设备 → 会话绑定与语音运行时装配。

`resolve_conversation_for_device()` 只使用现有 `devices` / `conversations`
字段，不新增表结构；`build_voice_runtime()` 把配置、Mock/Unavailable provider、
聊天生成器与事件总线组装成 `VoiceRuntime`，供网关注入每台设备。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select

from companion.server.app.audio.dashscope import (
    DashScopeAsrProvider,
    DashScopeTtsProvider,
)
from companion.server.app.audio.providers import (
    MockAsrProvider,
    MockTtsProvider,
    UnavailableAsrProvider,
    UnavailableTtsProvider,
)
from companion.server.app.audio.runtime import VoiceConfig, VoiceRuntime
from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.device import DeviceModel
from companion.server.app.services.events import get_event_bus
from companion.server.app.services.orchestrator import (
    ChatOrchestrator,
    get_conversation_lock,
)

logger = logging.getLogger(__name__)


async def resolve_conversation_for_device(
    db: Any, device: DeviceModel
) -> str | None:
    """按设计 §8.1 解析本轮语音使用的会话。

    1. `bound_conversation_id` 存在且会话仍存在 → 用它；
    2. 否则 `bound_assistant_id` → 该角色最近更新的会话；没有则新建；
    3. 都没有（或设备已撤销）→ None，由管线回 `unbound`。
    """
    if not device.is_active:
        # 撤销中的设备不应在语音路径自动新建会话。
        return None

    if device.bound_conversation_id:
        conversation = await db.get(
            ConversationModel, device.bound_conversation_id
        )
        if conversation is not None:
            return conversation.id

    if not device.bound_assistant_id:
        return None

    assistant = await db.get(AssistantModel, device.bound_assistant_id)
    if assistant is None:
        return None

    result = await db.execute(
        select(ConversationModel)
        .where(ConversationModel.assistant_id == assistant.id)
        .order_by(ConversationModel.updated_at.desc())
        .limit(1)
    )
    conversation = result.scalar_one_or_none()
    if conversation is not None:
        return conversation.id

    conversation = ConversationModel(
        assistant_id=assistant.id,
        title=f"与 {device.name} 的语音对话",
    )
    db.add(conversation)
    await db.commit()
    return conversation.id


def _build_providers() -> tuple[Any, Any]:
    """按 `VOICE_PROVIDER` 选择 provider；未知/未配置 Key 一律按不可用处理。"""
    provider = (settings.VOICE_PROVIDER or "").strip().lower()
    if provider == "mock":
        return (
            MockAsrProvider([{"final": "（mock）你好，我在。"}]),
            MockTtsProvider(),
        )
    if provider == "dashscope":
        if not (settings.DASHSCOPE_API_KEY or "").strip():
            # 缺 Key 时明确降级，绝不尝试连接云服务。
            logger.warning(
                "VOICE_PROVIDER=dashscope but DASHSCOPE_API_KEY is empty; "
                "falling back to unavailable"
            )
            return UnavailableAsrProvider(), UnavailableTtsProvider()
        return (
            DashScopeAsrProvider(
                settings.DASHSCOPE_API_KEY,
                model=settings.DASHSCOPE_ASR_MODEL,
                url=settings.DASHSCOPE_ASR_WS_URL,
                workspace_id=settings.DASHSCOPE_WORKSPACE_ID,
                region=settings.DASHSCOPE_REGION,
            ),
            DashScopeTtsProvider(
                settings.DASHSCOPE_API_KEY,
                model=settings.DASHSCOPE_TTS_MODEL,
                voice=settings.DASHSCOPE_TTS_VOICE,
                url=settings.DASHSCOPE_TTS_HTTP_URL,
            ),
        )
    if provider == "unavailable":
        return UnavailableAsrProvider(), UnavailableTtsProvider()
    logger.warning(
        "voice provider %r not implemented, falling back to unavailable",
        provider,
    )
    return UnavailableAsrProvider(), UnavailableTtsProvider()


def build_voice_runtime(
    *,
    bus: Any = None,
    session_factory=AsyncSessionLocal,
) -> VoiceRuntime | None:
    """构建网关注入的共享运行时；`VOICE_ENABLED=False` 时返回 None。"""
    if not settings.VOICE_ENABLED:
        return None

    asr, tts = _build_providers()
    config = VoiceConfig(
        idle_timeout=settings.VOICE_IDLE_TIMEOUT_SECONDS,
        max_turn=settings.VOICE_MAX_TURN_SECONDS,
        asr_final_grace=settings.VOICE_ASR_FINAL_GRACE_SECONDS,
        tts_first_packet_timeout=settings.VOICE_TTS_FIRST_PACKET_TIMEOUT_SECONDS,
        segment_idle_timeout=settings.VOICE_SEGMENT_IDLE_TIMEOUT_SECONDS,
        playback_done_timeout=settings.VOICE_PLAYBACK_DONE_TIMEOUT_SECONDS,
        downlink_buffer_seconds=settings.VOICE_DOWNLINK_BUFFER_SECONDS,
        frame_ms=settings.VOICE_DOWNLINK_FRAME_MS,
        tts_container=settings.DASHSCOPE_TTS_CONTAINER,
    )

    async def resolve_conversation(device_id: str) -> str | None:
        async with session_factory() as db:
            device = await db.get(DeviceModel, device_id)
            if device is None:
                return None
            return await resolve_conversation_for_device(db, device)

    def conversation_busy(conversation_id: str) -> bool:
        return get_conversation_lock(conversation_id).locked()

    async def chat_factory(
        conversation_id: str,
        text: str,
        *,
        cancel_reason: str = "barge_in",
    ) -> AsyncIterator[dict[str, Any]]:
        # 会话必须在本轮生成器存活期间保持打开：取消落库与正常落库都在
        # ChatOrchestrator 内部使用同一个 db。
        async with session_factory() as db:
            async for chunk in ChatOrchestrator.stream_chat(
                db,
                conversation_id,
                text,
                cancel_reason=cancel_reason,
            ):
                yield chunk

    return VoiceRuntime(
        bus=bus or get_event_bus(),
        asr=asr,
        tts=tts,
        resolve_conversation=resolve_conversation,
        conversation_busy=conversation_busy,
        chat_factory=chat_factory,
        config=config,
    )
