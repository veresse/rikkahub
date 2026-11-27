"""聊天取消落库：部分回复必须保存并标记 interrupted。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.services.orchestrator import ChatOrchestrator

ASSISTANT_ID = "asst-chat-cancel"
CONVERSATION_ID = "conv-chat-cancel"


class HangingClient:
    """先产出一段正文，然后永远挂起，模拟取消时的部分回复。"""

    def __init__(self) -> None:
        self.first_content = asyncio.Event()

    async def stream_chat(
        self, messages, model=None, temperature=None, top_p=None, tools=None
    ):
        yield {"type": "content", "delta": "你好，我是"}
        self.first_content.set()
        await asyncio.Event().wait()
        yield {"type": "done", "finish_reason": "stop"}  # pragma: no cover


class ScriptedClient:
    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks

    async def stream_chat(
        self, messages, model=None, temperature=None, top_p=None, tools=None
    ):
        for chunk in self.chunks:
            yield chunk


async def seed(session) -> None:
    session.add(
        AssistantModel(
            id=ASSISTANT_ID,
            name="取消测试角色",
            system_prompt="你是测试角色。",
            enable_web_search=False,
            enable_action_sync=False,
        )
    )
    session.add(
        ConversationModel(
            id=CONVERSATION_ID, assistant_id=ASSISTANT_ID, title="取消测试"
        )
    )
    await session.commit()


async def cleanup(session) -> None:
    await session.rollback()
    await session.execute(
        delete(MessageNodeModel).where(
            MessageNodeModel.conversation_id == CONVERSATION_ID
        )
    )
    await session.execute(
        delete(ConversationModel).where(ConversationModel.id == CONVERSATION_ID)
    )
    await session.execute(
        delete(AssistantModel).where(AssistantModel.id == ASSISTANT_ID)
    )
    await session.commit()


@pytest.fixture
async def session():
    async with AsyncSessionLocal() as s:
        yield s
        await cleanup(s)


async def nodes(session) -> list[MessageNodeModel]:
    result = await session.execute(
        select(MessageNodeModel)
        .where(MessageNodeModel.conversation_id == CONVERSATION_ID)
        .order_by(MessageNodeModel.node_index)
    )
    return list(result.scalars().all())


async def test_cancel_persists_partial_turn_as_interrupted(session):
    await seed(session)
    client = HangingClient()
    generator = ChatOrchestrator.stream_chat(
        session, CONVERSATION_ID, "请开始回答", client=client
    )

    async def consume() -> None:
        async for _ in generator:
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(client.first_content.wait(), 2.0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    stored = await nodes(session)
    assert len(stored) == 2
    user_message = stored[0].messages[0]
    assert user_message["role"] == "USER"
    assert user_message["parts"][0]["text"] == "请开始回答"

    assistant_message = stored[1].messages[0]
    assert assistant_message["role"] == "ASSISTANT"
    assert assistant_message["interrupted"] is True
    assert assistant_message["finishReason"] == "interrupted"
    assert assistant_message["parts"][0]["text"] == "你好，我是"

    conversation = await session.get(ConversationModel, CONVERSATION_ID)
    assert conversation is not None and conversation.updated_at > 0

    # 关闭已结束的生成器不会重复落库。
    await generator.aclose()
    assert len(await nodes(session)) == 2


async def test_normal_completion_does_not_mark_interrupted(session):
    await seed(session)
    client = ScriptedClient(
        [
            {"type": "content", "delta": "完整回答。"},
            {"type": "done", "finish_reason": "stop"},
        ]
    )

    events = [
        event
        async for event in ChatOrchestrator.stream_chat(
            session, CONVERSATION_ID, "正常问题", client=client
        )
    ]

    assert events[-1]["type"] == "done"
    stored = await nodes(session)
    assert len(stored) == 2
    assistant_message = stored[1].messages[0]
    assert "interrupted" not in assistant_message
    assert assistant_message["parts"][0]["text"] == "完整回答。"


async def test_cancel_during_commit_retries_and_keeps_the_user_node(session, monkeypatch):
    await seed(session)
    client = ScriptedClient(
        [
            {"type": "content", "delta": "已经生成的部分。"},
            {"type": "done", "finish_reason": "stop"},
        ]
    )
    original_save = ChatOrchestrator._save_turn
    entered = asyncio.Event()
    calls = {"count": 0}

    async def save_blocked_once(cls, db, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            # 模拟取消恰好落在 _save_turn() 的 commit 中。
            entered.set()
            await asyncio.Event().wait()
        return await original_save(db, **kwargs)

    monkeypatch.setattr(
        ChatOrchestrator, "_save_turn", classmethod(save_blocked_once)
    )

    generator = ChatOrchestrator.stream_chat(
        session, CONVERSATION_ID, "提交中取消", client=client
    )

    async def consume() -> None:
        async for _ in generator:
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 2.0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    stored = await nodes(session)
    assert len(stored) == 2
    assert stored[0].messages[0]["parts"][0]["text"] == "提交中取消"
    assert stored[1].messages[0]["interrupted"] is True
    assert stored[1].messages[0]["parts"][0]["text"] == "已经生成的部分。"
    assert calls["count"] == 2


async def test_cancel_after_commit_does_not_duplicate_the_turn(session, monkeypatch):
    await seed(session)
    client = ScriptedClient(
        [
            {"type": "content", "delta": "提交后才能看到。"},
            {"type": "done", "finish_reason": "stop"},
        ]
    )
    original_save = ChatOrchestrator._save_turn
    committed = asyncio.Event()
    release = asyncio.Event()
    calls = {"count": 0}

    async def save_commits_then_stalls(cls, db, **kwargs):
        calls["count"] += 1
        result = await original_save(db, **kwargs)
        # commit 已成功，但协程尚未返回：取消恰好落在这个窗口。
        committed.set()
        await release.wait()
        return result

    monkeypatch.setattr(
        ChatOrchestrator, "_save_turn", classmethod(save_commits_then_stalls)
    )

    generator = ChatOrchestrator.stream_chat(
        session, CONVERSATION_ID, "提交后取消", client=client
    )

    async def consume() -> None:
        async for _ in generator:
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(committed.wait(), 2.0)
    consumer.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    stored = await nodes(session)
    assert len(stored) == 2
    assert stored[0].messages[0]["parts"][0]["text"] == "提交后取消"
    assert "interrupted" not in stored[1].messages[0]
    # 已提交的轮次不能被取消路径重复写一遍。
    assert calls["count"] == 1


async def test_generator_close_frees_conversation_lock_before_persisting(
    session, monkeypatch
):
    """`aclose()`/GC 关闭生成器时，落库不得仍占用会话锁（否则新轮 fatal busy）。"""
    from companion.server.app.services.orchestrator import get_conversation_lock

    await seed(session)
    client = HangingClient()
    generator = ChatOrchestrator.stream_chat(
        session, CONVERSATION_ID, "被关闭的一轮", client=client
    )
    first = await asyncio.wait_for(generator.__anext__(), 2.0)
    assert first["type"] == "status"
    # 继续读到第一段正文，确保取消时已有可落库的部分回复。
    content = await asyncio.wait_for(generator.__anext__(), 2.0)
    assert content["type"] == "content"

    original_save = ChatOrchestrator._save_turn
    saving = asyncio.Event()
    release = asyncio.Event()

    async def slow_interrupted_save(cls, db, **kwargs):
        if kwargs.get("interrupted"):
            saving.set()
            await release.wait()
        return await original_save(db, **kwargs)

    monkeypatch.setattr(
        ChatOrchestrator, "_save_turn", classmethod(slow_interrupted_save)
    )

    closer = asyncio.create_task(generator.aclose())
    await asyncio.wait_for(saving.wait(), 2.0)
    # 内层生成器已随外层 aclose() 关闭：落库期间会话锁必须已释放。
    assert not get_conversation_lock(CONVERSATION_ID).locked()

    second = ScriptedClient(
        [
            {"type": "content", "delta": "第二轮完整回答。"},
            {"type": "done", "finish_reason": "stop"},
        ]
    )
    chunks = [
        chunk
        async for chunk in ChatOrchestrator.stream_chat(
            session, CONVERSATION_ID, "第二轮提问", client=second
        )
    ]
    assert not any(chunk.get("type") == "error" for chunk in chunks)
    assert chunks[-1]["type"] == "done"

    release.set()
    await closer

    stored = await nodes(session)
    assert len(stored) == 4
    assert [node.node_index for node in stored] == [0, 1, 2, 3]
    assistants = [
        node.messages[0]
        for node in stored
        if node.messages[0]["role"] == "ASSISTANT"
    ]
    interrupted = [m for m in assistants if m.get("interrupted")]
    assert len(interrupted) == 1
    assert interrupted[0]["parts"][0]["text"] == "你好，我是"
    assert any(
        m["parts"][0]["text"] == "第二轮完整回答。"
        for m in assistants
        if not m.get("interrupted")
    )


async def test_save_turn_is_idempotent_for_the_same_turn_key(session):
    await seed(session)
    before = await nodes(session)
    kwargs = dict(
        conversation_id=CONVERSATION_ID,
        user_text="幂等重试",
        full_response_text="只应写一次。",
        full_reasoning_text="",
        latest_usage=None,
        all_nodes=before,
        regenerate_node_id=None,
        turn_key="turn-idempotent",
    )

    first = await ChatOrchestrator._save_turn(session, **kwargs)
    second = await ChatOrchestrator._save_turn(session, **kwargs)

    assert first["id"] == second["id"]
    stored = await nodes(session)
    assert len(stored) == len(before) + 2
    assert stored[0].messages[0]["parts"][0]["text"] == "幂等重试"


async def test_concurrent_turns_with_stale_context_do_not_collide(session):
    """取消轮落库未完成时开始的新一轮，不能复用旧节点下标或 ID。"""
    await seed(session)
    kwargs = dict(
        conversation_id=CONVERSATION_ID,
        full_reasoning_text="",
        latest_usage=None,
        all_nodes=[],
        regenerate_node_id=None,
    )

    async with AsyncSessionLocal() as first_db, AsyncSessionLocal() as second_db:
        await asyncio.gather(
            ChatOrchestrator._save_turn(
                first_db,
                user_text="被取消的一轮",
                full_response_text="部分回答",
                interrupted=True,
                finish_reason="interrupted",
                turn_key="turn-cancelled",
                **kwargs,
            ),
            ChatOrchestrator._save_turn(
                second_db,
                user_text="紧接着的新一轮",
                full_response_text="完整回答",
                turn_key="turn-next",
                **kwargs,
            ),
        )

    stored = await nodes(session)
    assert [node.node_index for node in stored] == [0, 1, 2, 3]
    user_texts = {
        node.messages[0]["parts"][0]["text"]
        for node in stored
        if node.messages[0]["role"] == "USER"
    }
    assert user_texts == {"被取消的一轮", "紧接着的新一轮"}


async def test_cancel_after_commit_timeout_does_not_duplicate(
    session, monkeypatch
):
    """commit 成功但落库协程 2s 内未返回：超时取消后重试仍只能有一轮。"""
    from companion.server.app.services import orchestrator as orchestrator_module

    await seed(session)
    client = ScriptedClient(
        [
            {"type": "content", "delta": "提交后卡住的回答。"},
            {"type": "done", "finish_reason": "stop"},
        ]
    )
    original_save = ChatOrchestrator._save_turn
    committed = asyncio.Event()
    calls = {"count": 0}

    async def save_commits_then_hangs(cls, db, **kwargs):
        calls["count"] += 1
        result = await original_save(db, **kwargs)
        if calls["count"] == 1:
            # commit 已完成，但协程迟迟不返回，迫使取消路径走超时分支。
            committed.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(
        ChatOrchestrator, "_save_turn", classmethod(save_commits_then_hangs)
    )
    monkeypatch.setattr(orchestrator_module, "_CANCEL_SETTLE_TIMEOUT", 0.05)

    generator = ChatOrchestrator.stream_chat(
        session, CONVERSATION_ID, "提交后超时取消", client=client
    )

    async def consume() -> None:
        async for _ in generator:
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(committed.wait(), 2.0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    stored = await nodes(session)
    assert len(stored) == 2
    assert calls["count"] == 2
    assert stored[0].messages[0]["parts"][0]["text"] == "提交后超时取消"


async def test_regenerate_retry_does_not_duplicate_candidates(session):
    await seed(session)
    await ChatOrchestrator._save_turn(
        session,
        conversation_id=CONVERSATION_ID,
        user_text="原始提问",
        full_response_text="原始回答",
        full_reasoning_text="",
        latest_usage=None,
        all_nodes=[],
        regenerate_node_id=None,
        turn_key="turn-base",
    )
    stored = await nodes(session)
    assistant_node_id = stored[1].id

    kwargs = dict(
        conversation_id=CONVERSATION_ID,
        user_text="原始提问",
        full_response_text="重新生成的回答",
        full_reasoning_text="",
        latest_usage=None,
        all_nodes=stored,
        regenerate_node_id=assistant_node_id,
        turn_key="turn-regenerate",
    )
    first = await ChatOrchestrator._save_turn(session, **kwargs)
    second = await ChatOrchestrator._save_turn(session, **kwargs)

    assert first["id"] == second["id"]
    refreshed = await session.get(MessageNodeModel, assistant_node_id)
    assert refreshed is not None
    assert len(refreshed.messages) == 2


async def test_cancel_survives_persistence_failure(session, monkeypatch, caplog):
    await seed(session)

    async def failing_save(cls, db, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        ChatOrchestrator, "_save_turn", classmethod(failing_save)
    )
    client = HangingClient()
    generator = ChatOrchestrator.stream_chat(
        session, CONVERSATION_ID, "取消", client=client
    )

    async def consume() -> None:
        async for _ in generator:
            pass

    with caplog.at_level("DEBUG"):
        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(client.first_content.wait(), 2.0)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

    assert len(await nodes(session)) == 0
