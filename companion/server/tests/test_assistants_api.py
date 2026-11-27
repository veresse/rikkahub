"""4B：角色 `model_id` 与 `web_search_mode` 三态。"""

from __future__ import annotations

import pytest


async def _delete(client, headers, assistant_id: str) -> None:
    await client.delete(f"/api/assistants/{assistant_id}", headers=headers)


async def test_create_and_read_model_and_search_mode(client, auth_headers):
    created = await client.post(
        "/api/assistants",
        json={
            "name": "夏以昼",
            "model_id": "deepseek-chat",
            "web_search_mode": "on",
        },
        headers=auth_headers,
    )
    assert created.status_code == 200, created.text
    assistant_id = created.json()["id"]
    try:
        got = await client.get(
            f"/api/assistants/{assistant_id}", headers=auth_headers
        )
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["model_id"] == "deepseek-chat"
        assert body["web_search_mode"] == "on"
        assert body["enable_web_search"] is True

        listed = await client.get("/api/assistants", headers=auth_headers)
        assert listed.status_code == 200
        assert any(
            a["id"] == assistant_id
            and a["model_id"] == "deepseek-chat"
            and a["web_search_mode"] == "on"
            for a in listed.json()
        )
    finally:
        await _delete(client, auth_headers, assistant_id)


async def test_legacy_enable_web_search_maps_to_auto(client, auth_headers):
    created = await client.post(
        "/api/assistants",
        json={"name": "旧客户端", "enable_web_search": True},
        headers=auth_headers,
    )
    assistant_id = created.json()["id"]
    try:
        got = await client.get(
            f"/api/assistants/{assistant_id}", headers=auth_headers
        )
        assert got.json()["web_search_mode"] == "auto"
        assert got.json()["enable_web_search"] is True
    finally:
        await _delete(client, auth_headers, assistant_id)


async def test_update_to_off_clears_enable_flag(client, auth_headers):
    created = await client.post(
        "/api/assistants",
        json={"name": "改开关", "web_search_mode": "auto"},
        headers=auth_headers,
    )
    assistant_id = created.json()["id"]
    try:
        updated = await client.put(
            f"/api/assistants/{assistant_id}",
            json={"name": "改开关", "web_search_mode": "off"},
            headers=auth_headers,
        )
        assert updated.status_code == 200, updated.text
        got = await client.get(
            f"/api/assistants/{assistant_id}", headers=auth_headers
        )
        assert got.json()["web_search_mode"] == "off"
        assert got.json()["enable_web_search"] is False
    finally:
        await _delete(client, auth_headers, assistant_id)


async def test_invalid_search_mode_is_422(client, auth_headers):
    resp = await client.post(
        "/api/assistants",
        json={"name": "坏值", "web_search_mode": "sometimes"},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("mode", ["off", "auto", "on"])
async def test_all_modes_round_trip(client, auth_headers, mode):
    created = await client.post(
        "/api/assistants",
        json={"name": f"模式-{mode}", "web_search_mode": mode},
        headers=auth_headers,
    )
    assistant_id = created.json()["id"]
    try:
        got = await client.get(
            f"/api/assistants/{assistant_id}", headers=auth_headers
        )
        assert got.json()["web_search_mode"] == mode
    finally:
        await _delete(client, auth_headers, assistant_id)


async def test_legacy_update_without_model_keeps_existing_value(client, auth_headers):
    """旧客户端 PUT 不带 model_id/avatar 时不得把它们清空。"""
    created = await client.post(
        "/api/assistants",
        json={
            "name": "带模型",
            "model_id": "deepseek-chat",
            "avatar": {"type": "emoji", "content": "🐱"},
        },
        headers=auth_headers,
    )
    assistant_id = created.json()["id"]
    try:
        updated = await client.put(
            f"/api/assistants/{assistant_id}",
            json={"name": "带模型", "system_prompt": "新人设"},
            headers=auth_headers,
        )
        assert updated.status_code == 200, updated.text
        body = (
            await client.get(f"/api/assistants/{assistant_id}", headers=auth_headers)
        ).json()
        assert body["model_id"] == "deepseek-chat"
        assert body["avatar"]["content"] == "🐱"

        # 显式传 null 仍然允许清空模型。
        cleared = await client.put(
            f"/api/assistants/{assistant_id}",
            json={"name": "带模型", "model_id": None},
            headers=auth_headers,
        )
        assert cleared.status_code == 200, cleared.text
        body = (
            await client.get(f"/api/assistants/{assistant_id}", headers=auth_headers)
        ).json()
        assert body["model_id"] is None
        assert body["avatar"]["content"] == "🐱"
    finally:
        await _delete(client, auth_headers, assistant_id)
