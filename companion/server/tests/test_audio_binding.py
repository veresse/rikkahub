"""设备 → 会话绑定与语音运行时装配测试（离线）。"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import delete

from companion.server.app.audio.binding import (
    build_voice_runtime,
    resolve_conversation_for_device,
)
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
from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.device import DeviceModel


class Fixture:
    def __init__(self, session) -> None:
        self.session = session
        self.assistants: list[str] = []
        self.conversations: list[str] = []
        self.devices: list[str] = []

    async def assistant(self, name: str = "绑定测试角色") -> AssistantModel:
        model = AssistantModel(
            id=str(uuid.uuid4()),
            name=name,
            system_prompt="你是测试角色。",
            enable_web_search=False,
            enable_action_sync=False,
        )
        self.session.add(model)
        self.assistants.append(model.id)
        await self.session.flush()
        return model

    async def conversation(
        self,
        assistant_id: str,
        *,
        title: str = "绑定测试会话",
        updated_at: int | None = None,
    ) -> ConversationModel:
        model = ConversationModel(
            id=str(uuid.uuid4()), assistant_id=assistant_id, title=title
        )
        if updated_at is not None:
            model.updated_at = updated_at
        self.session.add(model)
        self.conversations.append(model.id)
        await self.session.flush()
        return model

    async def device(
        self,
        *,
        name: str = "绑定测试机",
        assistant_id: str | None = None,
        conversation_id: str | None = None,
        is_active: bool = True,
    ) -> DeviceModel:
        model = DeviceModel(
            id=str(uuid.uuid4()),
            name=name,
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            bound_assistant_id=assistant_id,
            bound_conversation_id=conversation_id,
            is_active=is_active,
        )
        self.session.add(model)
        self.devices.append(model.id)
        await self.session.flush()
        return model


@pytest.fixture
async def store() -> Any:
    async with AsyncSessionLocal() as session:
        helper = Fixture(session)
        try:
            yield helper
        finally:
            await session.rollback()
            for device_id in helper.devices:
                await session.execute(
                    delete(DeviceModel).where(DeviceModel.id == device_id)
                )
            for conversation_id in helper.conversations:
                await session.execute(
                    delete(ConversationModel).where(
                        ConversationModel.id == conversation_id
                    )
                )
            for assistant_id in helper.assistants:
                await session.execute(
                    delete(AssistantModel).where(AssistantModel.id == assistant_id)
                )
            await session.commit()


async def test_bound_conversation_wins_over_assistant(store):
    assistant = await store.assistant()
    # 显式绑定必须赢过“最近更新”的回退：给回退候选一个更大的 updated_at，
    # 否则两次创建落在同一毫秒时，绑定失效也可能碰巧选中 chosen。
    chosen = await store.conversation(
        assistant.id, title="显式绑定", updated_at=1000
    )
    await store.conversation(assistant.id, title="更新的会话", updated_at=2000)
    device = await store.device(
        assistant_id=assistant.id, conversation_id=chosen.id
    )

    assert await resolve_conversation_for_device(store.session, device) == chosen.id


async def test_assistant_falls_back_to_most_recent_conversation(store):
    assistant = await store.assistant()
    await store.conversation(assistant.id, title="旧会话", updated_at=1000)
    newest = await store.conversation(
        assistant.id, title="新会话", updated_at=2000
    )
    device = await store.device(assistant_id=assistant.id)

    assert await resolve_conversation_for_device(store.session, device) == newest.id


async def test_assistant_without_conversation_creates_a_titled_one(store):
    assistant = await store.assistant()
    device = await store.device(name="客厅机器人", assistant_id=assistant.id)

    conversation_id = await resolve_conversation_for_device(
        store.session, device
    )
    assert conversation_id is not None
    store.conversations.append(conversation_id)

    conversation = await store.session.get(ConversationModel, conversation_id)
    assert conversation is not None
    assert conversation.assistant_id == assistant.id
    assert conversation.title == "与 客厅机器人 的语音对话"

    # 再次解析复用刚创建的会话，不会每轮新建。
    assert (
        await resolve_conversation_for_device(store.session, device)
        == conversation_id
    )


async def test_device_without_binding_returns_none(store):
    device = await store.device()
    assert await resolve_conversation_for_device(store.session, device) is None


async def test_revoked_device_returns_none(store):
    assistant = await store.assistant()
    conversation = await store.conversation(assistant.id)
    device = await store.device(
        assistant_id=assistant.id,
        conversation_id=conversation.id,
        is_active=False,
    )
    assert await resolve_conversation_for_device(store.session, device) is None


async def test_deleted_bound_conversation_falls_back_to_assistant(store):
    assistant = await store.assistant()
    existing = await store.conversation(assistant.id, title="仍然存在")
    # 不加入 session：模拟 DB 里仍保留一个已删除会话 id 的设备行，
    # 解析时应回退到角色最近会话，而不是直接 unbound。
    device = DeviceModel(
        id=str(uuid.uuid4()),
        name="历史绑定机",
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        bound_assistant_id=assistant.id,
        bound_conversation_id=str(uuid.uuid4()),
        is_active=True,
    )
    assert (
        await resolve_conversation_for_device(store.session, device)
        == existing.id
    )


def test_runtime_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_ENABLED", False)
    assert build_voice_runtime() is None


def test_runtime_unavailable_provider(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "unavailable")
    runtime = build_voice_runtime(bus=object())
    assert runtime is not None
    assert isinstance(runtime.asr, UnavailableAsrProvider)
    assert isinstance(runtime.tts, UnavailableTtsProvider)
    assert runtime.config.idle_timeout == settings.VOICE_IDLE_TIMEOUT_SECONDS
    assert runtime.config.tts_container == settings.DASHSCOPE_TTS_CONTAINER


def test_runtime_mock_provider(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "mock")
    runtime = build_voice_runtime(bus=object())
    assert runtime is not None
    assert isinstance(runtime.asr, MockAsrProvider)
    assert isinstance(runtime.tts, MockTtsProvider)


def test_runtime_unknown_provider_falls_back_to_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "not-a-provider")
    runtime = build_voice_runtime(bus=object())
    assert runtime is not None
    assert isinstance(runtime.asr, UnavailableAsrProvider)
    assert isinstance(runtime.tts, UnavailableTtsProvider)


def test_runtime_dashscope_without_key_falls_back(monkeypatch, caplog):
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "dashscope")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")
    with caplog.at_level("WARNING"):
        runtime = build_voice_runtime(bus=object())
    assert runtime is not None
    assert isinstance(runtime.asr, UnavailableAsrProvider)
    assert isinstance(runtime.tts, UnavailableTtsProvider)
    assert "DASHSCOPE_API_KEY" in caplog.text


def test_runtime_dashscope_with_key_builds_adapters(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "dashscope")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "sk-test-key")
    monkeypatch.setattr(settings, "DASHSCOPE_ASR_WS_URL", "ws://127.0.0.1:9001/asr")
    monkeypatch.setattr(settings, "DASHSCOPE_TTS_HTTP_URL", "http://127.0.0.1:9002/tts")

    runtime = build_voice_runtime(bus=object())
    assert runtime is not None
    assert isinstance(runtime.asr, DashScopeAsrProvider)
    assert isinstance(runtime.tts, DashScopeTtsProvider)
    # 构造阶段不联网；override 只影响 URL，不触发连接。
    assert runtime.asr._url == "ws://127.0.0.1:9001/asr"
    assert runtime.tts._url == "http://127.0.0.1:9002/tts"


def test_runtime_dashscope_builds_workspace_url(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "dashscope")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "sk-test-key")
    monkeypatch.setattr(settings, "DASHSCOPE_ASR_WS_URL", "")
    monkeypatch.setattr(settings, "DASHSCOPE_WORKSPACE_ID", "ws-123")
    monkeypatch.setattr(settings, "DASHSCOPE_REGION", "cn-shanghai")

    runtime = build_voice_runtime(bus=object())
    assert runtime is not None
    assert runtime.asr._url == (
        "wss://ws-123.cn-shanghai.maas.aliyuncs.com/api-ws/v1/inference"
    )
