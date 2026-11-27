"""4B：`conversation.turn_state` 广播与旧轮守卫。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete

from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.services import orchestrator as orchestrator_module
from companion.server.app.services.events import drain_background_tasks, get_event_bus
from companion.server.app.services.orchestrator import ChatOrchestrator

ASSISTANT_ID = "asst-turn-state"
CONVERSATION_ID = "conv-turn-state"


class ScriptedClient:
    def __init__(self):
        self.calls = 0

    async def stream_chat(self, messages, model=None, temperature=None, top_p=None, tools=None):
        self.calls += 1
        yield {"type": "content", "delta": "回答。"}
        yield {"type": "done", "finish_reason": "stop"}


async def seed(session):
    session.add(
        AssistantModel(
            id=ASSISTANT_ID,
            name="状态角色",
            system_prompt="你是测试角色。",
            enable_action_sync=False,
        )
    )
    session.add(
        ConversationModel(id=CONVERSATION_ID, assistant_id=ASSISTANT_ID, title="状态会话")
    )
    await session.commit()


async def cleanup(session):
    await session.rollback()
    await session.execute(
        delete(MessageNodeModel).where(MessageNodeModel.conversation_id == CONVERSATION_ID)
    )
    await session.execute(
        delete(ConversationModel).where(ConversationModel.id == CONVERSATION_ID)
    )
    await session.execute(delete(AssistantModel).where(AssistantModel.id == ASSISTANT_ID))
    await session.commit()


@pytest.fixture
async def session():
    async with AsyncSessionLocal() as s:
        await seed(s)
        yield s
        await cleanup(s)
        orchestrator_module._ACTIVE_TURN.clear()


@asynccontextmanager
async def collect_turn_states():
    events: list[dict] = []

    async def handler(event: dict) -> None:
        if event.get("type") == "conversation.turn_state":
            events.append(event)

    bus = get_event_bus()
    bus.subscribe(handler)
    try:
        yield events
    finally:
        # idle 用后台任务广播，必须先等它真正投递完再退订。
        await drain_background_tasks()
        bus.unsubscribe(handler)


async def test_app_turn_broadcasts_generating_then_idle(session):
    async with collect_turn_states() as events:
        _ = [
            e
            async for e in ChatOrchestrator.stream_chat(
                session,
                CONVERSATION_ID,
                "你好",
                client=ScriptedClient(),
                client_msg_id="m-state",
            )
        ]

    states = [e["state"] for e in events]
    assert states == ["generating", "idle"]
    assert all(e["source"] == "app" for e in events)
    assert events[0]["turn_key"] == events[1]["turn_key"]
    assert events[0]["turn_id"] == events[0]["turn_key"]
    assert events[0]["conversation_id"] == CONVERSATION_ID


async def test_replayed_turn_does_not_broadcast(session):
    client = ScriptedClient()
    _ = [
        e
        async for e in ChatOrchestrator.stream_chat(
            session, CONVERSATION_ID, "你好", client=client, client_msg_id="m-replay"
        )
    ]
    # 第一轮 idle 是后台广播，先排空，避免串到第二轮收集窗口。
    await drain_background_tasks()

    async with collect_turn_states() as events:
        _ = [
            e
            async for e in ChatOrchestrator.stream_chat(
                session, CONVERSATION_ID, "你好", client=client, client_msg_id="m-replay"
            )
        ]

    assert events == []
    assert client.calls == 1


async def test_old_turn_idle_does_not_clear_new_turn(session):
    async with collect_turn_states() as events:
        orchestrator_module._ACTIVE_TURN[CONVERSATION_ID] = "new-key"
        published = ChatOrchestrator._publish_turn_idle(
            CONVERSATION_ID, "old-key", "app"
        )

    assert published is False
    assert events == []
    assert orchestrator_module._ACTIVE_TURN[CONVERSATION_ID] == "new-key"

    async with collect_turn_states() as events:
        published = ChatOrchestrator._publish_turn_idle(
            CONVERSATION_ID, "new-key", "app"
        )

    assert published is True
    assert [e["state"] for e in events] == ["idle"]


async def test_voice_turn_reports_source_voice(session):
    async with collect_turn_states() as events:
        _ = [
            e
            async for e in ChatOrchestrator.stream_chat(
                session,
                CONVERSATION_ID,
                "语音问题",
                client=ScriptedClient(),
                source="voice",
            )
        ]

    assert [e["source"] for e in events] == ["voice", "voice"]
