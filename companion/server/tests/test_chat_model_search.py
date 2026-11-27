"""4B：角色 `model_id` 透传与 `web_search_mode` 三态判定。"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.services import orchestrator as orchestrator_module
from companion.server.app.services import search_provider as search_module
from companion.server.app.services.orchestrator import ChatOrchestrator

ASSISTANT_ID = "asst-model-search"
CONVERSATION_ID = "conv-model-search"


class RecordingClient:
    def __init__(self):
        self.models: list[str | None] = []

    async def stream_chat(self, messages, model=None, temperature=None, top_p=None, tools=None):
        self.models.append(model)
        yield {"type": "content", "delta": "好。"}
        yield {"type": "done", "finish_reason": "stop"}


async def seed(session, *, model_id=None, mode="off", enable_web_search=False):
    session.add(
        AssistantModel(
            id=ASSISTANT_ID,
            name="模型角色",
            system_prompt="你是测试角色。",
            model_id=model_id,
            web_search_mode=mode,
            enable_web_search=enable_web_search,
            enable_action_sync=False,
        )
    )
    session.add(
        ConversationModel(id=CONVERSATION_ID, assistant_id=ASSISTANT_ID, title="模型会话")
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
        yield s
        await cleanup(s)


async def test_assistant_model_id_is_passed_to_client(session):
    await seed(session, model_id="deepseek-reasoner")
    client = RecordingClient()

    _ = [
        e
        async for e in ChatOrchestrator.stream_chat(
            session, CONVERSATION_ID, "你好", client=client
        )
    ]

    assert client.models == ["deepseek-reasoner"]


async def test_empty_model_id_falls_back_to_default(session):
    await seed(session, model_id=None)
    client = RecordingClient()

    _ = [
        e
        async for e in ChatOrchestrator.stream_chat(
            session, CONVERSATION_ID, "你好", client=client
        )
    ]

    assert client.models == [None]


@pytest.mark.parametrize(
    "mode, text, expected_calls",
    [
        ("off", "今天天气怎么样", 0),
        ("auto", "今天天气怎么样", 1),
        ("auto", "讲个故事", 0),
        ("on", "讲个故事", 1),
    ],
)
async def test_web_search_modes(session, monkeypatch, mode, text, expected_calls):
    await seed(session, mode=mode, enable_web_search=(mode != "off"))
    calls: list[str] = []

    async def fake_search(query: str):
        calls.append(query)
        return []

    monkeypatch.setattr(search_module.SearchProvider, "search", fake_search)
    client = RecordingClient()

    _ = [
        e
        async for e in ChatOrchestrator.stream_chat(
            session, CONVERSATION_ID, text, client=client
        )
    ]

    assert len(calls) == expected_calls


async def test_global_search_switch_off_skips_provider(session, monkeypatch):
    await seed(session, mode="on", enable_web_search=True)
    calls: list[str] = []

    async def fake_search(query: str):
        calls.append(query)
        return [{"title": "should not appear", "url": "https://e.test"}]

    monkeypatch.setattr(search_module.SearchProvider, "search", fake_search)
    monkeypatch.setattr(orchestrator_module.settings, "SEARCH_ENABLED", False)

    events = [
        e
        async for e in ChatOrchestrator.stream_chat(
            session, CONVERSATION_ID, "讲个故事", client=RecordingClient()
        )
    ]

    assert calls == []
    # 关闭全局搜索后既不能发起检索，也不能留下搜索状态/系统提示。
    assert not any(
        e.get("type") == "status" and e.get("status") in {"searching", "search_done"}
        for e in events
    )
    assert "【联网实时搜索结果】" not in str(events)
