"""`/api/conversations` REST 回归测试。

历史缺陷：`create_conversation()` 先 `await db.get(...)`（自动开启事务），
随后再 `async with db.begin()` 会抛
`InvalidRequestError: A transaction is already begun on this Session`，
导致新建会话接口 500；此前单元测试只直接写库，没发现该问题。
"""

from __future__ import annotations


async def _create_assistant(client, headers, name: str = "会话回归角色") -> str:
    resp = await client.post(
        "/api/assistants",
        json={"name": name, "system_prompt": "测试"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def test_create_conversation_succeeds_and_persists(client, auth_headers):
    assistant_id = await _create_assistant(client, auth_headers)
    conversation_id = None
    try:
        resp = await client.post(
            "/api/conversations",
            json={"assistant_id": assistant_id, "title": "回归会话"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        conversation_id = resp.json()["id"]

        detail = await client.get(
            f"/api/conversations/{conversation_id}", headers=auth_headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["title"] == "回归会话"
        assert detail.json()["assistant_id"] == assistant_id

        listed = await client.get(
            "/api/conversations", params={"assistant_id": assistant_id}, headers=auth_headers
        )
        assert listed.status_code == 200, listed.text
        assert [c["id"] for c in listed.json()] == [conversation_id]
    finally:
        if conversation_id is not None:
            await client.delete(
                f"/api/conversations/{conversation_id}", headers=auth_headers
            )
        await client.delete(f"/api/assistants/{assistant_id}", headers=auth_headers)


async def test_create_conversation_rejects_unknown_assistant(client, auth_headers):
    resp = await client.post(
        "/api/conversations",
        json={"assistant_id": "does-not-exist", "title": "悬空"},
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "关联角色不存在"
