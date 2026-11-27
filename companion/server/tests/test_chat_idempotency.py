"""4B：聊天幂等（client_msg_id）、done 双消息 id、duplicate_in_progress。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.services.orchestrator import ChatOrchestrator

ASSISTANT_ID = "asst-idempotent"
CONVERSATION_ID = "conv-idempotent"


class ScriptedClient:
    def __init__(self, chunks: list[dict] | None = None):
        self.chunks = chunks or [
            {"type": "content", "delta": "回答。"},
            {"type": "done", "finish_reason": "stop"},
        ]
        self.calls = 0

    async def stream_chat(self, messages, model=None, temperature=None, top_p=None, tools=None):
        self.calls += 1
        for chunk in self.chunks:
            yield chunk


class BlockingClient:
    """产出正文后卡住，用于模拟“同 id 生成中”。"""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def stream_chat(self, messages, model=None, temperature=None, top_p=None, tools=None):
        self.calls += 1
        yield {"type": "content", "delta": "生成中"}
        self.started.set()
        await self.release.wait()
        yield {"type": "content", "delta": "完成。"}
        yield {"type": "done", "finish_reason": "stop"}


class HangingClient:
    """产出一段正文后挂起，用于模拟断线/停止。"""

    def __init__(self):
        self.first_content = asyncio.Event()
        self.calls = 0

    async def stream_chat(self, messages, model=None, temperature=None, top_p=None, tools=None):
        self.calls += 1
        yield {"type": "content", "delta": "半句"}
        self.first_content.set()
        await asyncio.Event().wait()


async def seed(session):
    session.add(
        AssistantModel(
            id=ASSISTANT_ID,
            name="幂等角色",
            system_prompt="你是测试角色。",
            enable_action_sync=False,
        )
    )
    session.add(
        ConversationModel(id=CONVERSATION_ID, assistant_id=ASSISTANT_ID, title="幂等会话")
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


async def _collect(session, text, *, client, client_msg_id=None, regenerate_node_id=None):
    return [
        event
        async for event in ChatOrchestrator.stream_chat(
            session,
            CONVERSATION_ID,
            text,
            regenerate_node_id,
            client=client,
            client_msg_id=client_msg_id,
        )
    ]


async def test_replay_same_client_msg_id_does_not_call_model(session):
    client = ScriptedClient()
    first = await _collect(session, "你好", client=client, client_msg_id="m-1")
    first_done = first[-1]
    assert first_done["type"] == "done"
    assert first_done["replayed"] is False

    second = await _collect(session, "你好", client=client, client_msg_id="m-1")
    assert len(second) == 1
    second_done = second[0]
    assert second_done["type"] == "done"
    assert second_done["replayed"] is True
    assert second_done["assistant_message_id"] == first_done["assistant_message_id"]
    assert second_done["user_message_id"] == first_done["user_message_id"]
    assert client.calls == 1

    stored = (
        await session.execute(
            select(MessageNodeModel)
            .where(MessageNodeModel.conversation_id == CONVERSATION_ID)
            .order_by(MessageNodeModel.node_index.asc())
        )
    ).scalars().all()
    assert [n.id for n in stored] == [
        first_done["user_message_id"],
        first_done["assistant_message_id"],
    ]


async def test_different_client_msg_id_generates_new_turn(session):
    client = ScriptedClient()
    first = await _collect(session, "第一问", client=client, client_msg_id="m-1")
    second = await _collect(session, "第二问", client=client, client_msg_id="m-2")

    assert client.calls == 2
    assert first[-1]["assistant_message_id"] != second[-1]["assistant_message_id"]


async def test_same_client_msg_id_while_running_returns_duplicate_in_progress(session):
    client = BlockingClient()
    first_gen = ChatOrchestrator.stream_chat(
        session,
        CONVERSATION_ID,
        "慢慢说",
        client=client,
        client_msg_id="m-dup",
    )

    async def run_first():
        return [event async for event in first_gen]

    task = asyncio.create_task(run_first())
    await asyncio.wait_for(client.started.wait(), timeout=2)

    async with AsyncSessionLocal() as other:
        second = [
            event
            async for event in ChatOrchestrator.stream_chat(
                other,
                CONVERSATION_ID,
                "慢慢说",
                client=client,
                client_msg_id="m-dup",
            )
        ]
    assert second == [{"type": "error", "error": "duplicate_in_progress"}]

    client.release.set()
    first = await asyncio.wait_for(task, timeout=5)
    assert first[-1]["type"] == "done"
    assert first[-1]["replayed"] is False
    assert client.calls == 1


async def test_different_client_msg_id_keeps_legacy_busy_error(session):
    client = BlockingClient()
    first_gen = ChatOrchestrator.stream_chat(
        session,
        CONVERSATION_ID,
        "慢慢说",
        client=client,
        client_msg_id="m-1",
    )

    async def run_first():
        return [event async for event in first_gen]

    task = asyncio.create_task(run_first())
    await asyncio.wait_for(client.started.wait(), timeout=2)

    async with AsyncSessionLocal() as other:
        second = [
            event
            async for event in ChatOrchestrator.stream_chat(
                other,
                CONVERSATION_ID,
                "另一个问题",
                client=client,
                client_msg_id="m-2",
            )
        ]
    assert second[0]["type"] == "error"
    assert "正在生成" in second[0]["error"]

    client.release.set()
    await asyncio.wait_for(task, timeout=5)


async def test_done_always_carries_user_message_id(session):
    client = ScriptedClient()
    events = await _collect(session, "带 id", client=client, client_msg_id="m-3")
    done = events[-1]

    user_node = await session.get(MessageNodeModel, done["user_message_id"])
    assert user_node is not None
    assert user_node.messages[0]["role"] == "USER"
    assert user_node.messages[0]["parts"][0]["text"] == "带 id"


async def _stored_nodes(session) -> list[MessageNodeModel]:
    result = await session.execute(
        select(MessageNodeModel)
        .where(MessageNodeModel.conversation_id == CONVERSATION_ID)
        .order_by(MessageNodeModel.node_index.asc())
    )
    return list(result.scalars().all())


async def test_replay_ignores_regenerate_node_id_mismatch(session):
    """同一个 client_msg_id 即使先重新生成、再普通重试，也只应 replay。

    否则消息 id 会同时以节点和节点内候选的形式出现两次（数据重复）。
    """
    client = ScriptedClient()
    seed_done = (await _collect(
        session, "原始问题", client=client, client_msg_id="m-seed"
    ))[-1]
    user_node_id = seed_done["user_message_id"]

    regenerated = await _collect(
        session,
        "原始问题",
        client=client,
        client_msg_id="m-regen",
        regenerate_node_id=user_node_id,
    )
    regen_done = regenerated[-1]
    assert regen_done["replayed"] is False
    assert regen_done["user_message_id"] is None
    assert len(await _stored_nodes(session)) == 2

    retry = await _collect(session, "原始问题", client=client, client_msg_id="m-regen")
    assert len(retry) == 1
    retry_done = retry[0]
    assert retry_done["type"] == "done"
    assert retry_done["replayed"] is True
    assert retry_done["assistant_message_id"] == regen_done["assistant_message_id"]
    assert client.calls == 2
    # 未把候选消息 id 当成新节点插入。
    assert await session.get(
        MessageNodeModel, regen_done["assistant_message_id"]
    ) is None
    assert len(await _stored_nodes(session)) == 2

    # 反方向：普通轮已落库，再用同一 id 带 regenerate 重试也必须是 replay。
    plain = (await _collect(
        session, "第二问", client=client, client_msg_id="m-plain"
    ))[-1]
    nodes_before = await _stored_nodes(session)
    again = await _collect(
        session,
        "第二问",
        client=client,
        client_msg_id="m-plain",
        regenerate_node_id=plain["user_message_id"],
    )
    assert again[0]["replayed"] is True
    assert again[0]["assistant_message_id"] == plain["assistant_message_id"]
    assert [n.id for n in await _stored_nodes(session)] == [n.id for n in nodes_before]
    target = await session.get(MessageNodeModel, plain["user_message_id"])
    assert len(target.messages) == 1


async def test_interrupted_turn_is_regenerated_and_overwritten(session):
    """断线导致的半句/空回答不能被当作已完成 replay。"""
    hanging = HangingClient()
    generator = ChatOrchestrator.stream_chat(
        session, CONVERSATION_ID, "讲讲", client=hanging, client_msg_id="m-int"
    )

    async def consume() -> None:
        async for _ in generator:
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(hanging.first_content.wait(), timeout=2)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    stored = await _stored_nodes(session)
    assert len(stored) == 2
    assistant_id = stored[1].id
    assert stored[1].messages[0]["interrupted"] is True

    retry_client = ScriptedClient()
    retry = await _collect(session, "讲讲", client=retry_client, client_msg_id="m-int")
    done = retry[-1]
    assert done["type"] == "done"
    assert done["replayed"] is False
    assert done["assistant_message_id"] == assistant_id
    assert retry_client.calls == 1

    stored = await _stored_nodes(session)
    assert len(stored) == 2
    message = stored[1].messages[0]
    assert "interrupted" not in message
    assert message["parts"][0]["text"] == "回答。"
