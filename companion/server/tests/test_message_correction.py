"""4B：纠正已落库 USER 消息文本（只改文本，不重新生成）。"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel

ASSISTANT_ID = "asst-correction"
CONVERSATION_ID = "conv-correction"
OTHER_CONVERSATION_ID = "conv-correction-other"
NODE_ID = "node-correction"
USER_MESSAGE_ID = "msg-user"
ASSISTANT_MESSAGE_ID = "msg-assistant"


async def _seed():
    async with AsyncSessionLocal() as session:
        session.add(
            AssistantModel(id=ASSISTANT_ID, name="纠正角色", system_prompt="测试")
        )
        session.add(
            ConversationModel(
                id=CONVERSATION_ID, assistant_id=ASSISTANT_ID, title="纠正会话"
            )
        )
        session.add(
            ConversationModel(
                id=OTHER_CONVERSATION_ID,
                assistant_id=ASSISTANT_ID,
                title="另一个会话",
            )
        )
        session.add(
            MessageNodeModel(
                id=NODE_ID,
                conversation_id=CONVERSATION_ID,
                node_index=0,
                select_index=0,
                messages=[
                    {
                        "id": USER_MESSAGE_ID,
                        "role": "USER",
                        "parts": [
                            {"text": "旧的转写"},
                            {"image": {"mime": "image/png", "data": "AA=="}},
                        ],
                    },
                    {
                        "id": ASSISTANT_MESSAGE_ID,
                        "role": "ASSISTANT",
                        "parts": [{"text": "旧回答"}],
                    },
                ],
            )
        )
        await session.commit()


async def _cleanup():
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(MessageNodeModel).where(
                MessageNodeModel.conversation_id.in_(
                    [CONVERSATION_ID, OTHER_CONVERSATION_ID]
                )
            )
        )
        await session.execute(
            delete(ConversationModel).where(
                ConversationModel.id.in_([CONVERSATION_ID, OTHER_CONVERSATION_ID])
            )
        )
        await session.execute(
            delete(AssistantModel).where(AssistantModel.id == ASSISTANT_ID)
        )
        await session.commit()


@pytest.fixture
async def seeded():
    await _seed()
    yield
    await _cleanup()


def _url(conv_id=CONVERSATION_ID, node_id=NODE_ID, message_id=USER_MESSAGE_ID):
    return f"/api/conversations/{conv_id}/nodes/{node_id}/messages/{message_id}"


async def test_update_user_message_text_only(client, auth_headers, seeded):
    resp = await client.put(
        _url(), json={"text": "新的转写"}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "updated"
    assert body["message_id"] == USER_MESSAGE_ID
    assert body["text"] == "新的转写"

    detail = await client.get(
        f"/api/conversations/{CONVERSATION_ID}", headers=auth_headers
    )
    assert detail.status_code == 200
    node = next(n for n in detail.json()["nodes"] if n["id"] == NODE_ID)
    user = next(m for m in node["messages"] if m["id"] == USER_MESSAGE_ID)
    assert user["parts"][0]["text"] == "新的转写"
    # 其他 part 保留，消息/节点数量不变。
    assert user["parts"][1] == {"image": {"mime": "image/png", "data": "AA=="}}
    assert len(node["messages"]) == 2
    assistant = next(m for m in node["messages"] if m["id"] == ASSISTANT_MESSAGE_ID)
    assert assistant["parts"][0]["text"] == "旧回答"


async def test_rejects_non_user_message(client, auth_headers, seeded):
    resp = await client.put(
        _url(message_id=ASSISTANT_MESSAGE_ID),
        json={"text": "改回答"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "not_user_message"


async def test_rejects_wrong_conversation_or_node(client, auth_headers, seeded):
    wrong_conv = await client.put(
        _url(conv_id=OTHER_CONVERSATION_ID),
        json={"text": "越权"},
        headers=auth_headers,
    )
    assert wrong_conv.status_code == 404
    assert wrong_conv.json()["detail"] == "node_not_found"

    missing_node = await client.put(
        _url(node_id="node-missing"),
        json={"text": "不存在"},
        headers=auth_headers,
    )
    assert missing_node.status_code == 404
    assert missing_node.json()["detail"] == "node_not_found"

    missing_message = await client.put(
        _url(message_id="msg-missing"),
        json={"text": "不存在"},
        headers=auth_headers,
    )
    assert missing_message.status_code == 404
    assert missing_message.json()["detail"] == "message_not_found"


async def test_empty_text_is_422(client, auth_headers, seeded):
    resp = await client.put(_url(), json={"text": ""}, headers=auth_headers)
    assert resp.status_code == 422, resp.text
