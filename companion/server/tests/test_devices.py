"""设备配对、Token 哈希与设备管理测试（Phase 2 基础）。"""

import sqlite3

import pytest
from sqlalchemy import select

from companion.server.app.database import AsyncSessionLocal
from companion.server.app.config import settings
from companion.server.app.models.device import DeviceModel


async def _new_device(client, auth_headers, name="测试机器人"):
    code = await client.post("/api/devices/pair-code", json={}, headers=auth_headers)
    assert code.status_code == 200, code.text
    pair = await client.post(
        "/api/devices/pair",
        json={
            "code": code.json()["code"],
            "device_name": name,
            "firmware_version": "2.0.0",
            "protocol_version": 2,
            "capabilities": ["action", "volume", "expression"],
        },
    )
    return code, pair


async def test_pair_code_requires_app_token(client):
    resp = await client.post("/api/devices/pair-code", json={})
    assert resp.status_code == 401


async def test_pair_creates_device_once(client, auth_headers):
    code, pair = await _new_device(client, auth_headers)
    assert pair.status_code == 200, pair.text
    body = pair.json()
    assert body["device_id"]
    assert len(body["device_token"]) >= 32
    assert body["protocol_version"] == 2

    # 配对码一次性
    again = await client.post(
        "/api/devices/pair",
        json={"code": code.json()["code"], "device_name": "x", "protocol_version": 2},
    )
    assert again.status_code == 400
    assert again.json()["detail"] == "invalid_pair_code"

    # 数据库只存哈希，不存明文
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(DeviceModel).where(DeviceModel.id == body["device_id"]))
        device = res.scalar_one()
        assert device.token_hash
        assert device.token_hash != body["device_token"]
        assert not hasattr(device, "token")


async def test_pair_invalid_code(client):
    resp = await client.post(
        "/api/devices/pair",
        json={"code": "000000", "device_name": "x", "protocol_version": 2},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_pair_code"


async def test_pair_code_expires(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "PAIR_CODE_TTL_SECONDS", 0)
    code = await client.post("/api/devices/pair-code", json={}, headers=auth_headers)
    resp = await client.post(
        "/api/devices/pair",
        json={"code": code.json()["code"], "device_name": "x", "protocol_version": 2},
    )
    assert resp.status_code == 410
    assert resp.json()["detail"] == "pair_code_expired"


async def test_pair_code_attempt_limit(client, auth_headers):
    code = await client.post("/api/devices/pair-code", json={}, headers=auth_headers)
    real_code = code.json()["code"]
    wrong = "999999" if real_code != "999999" else "888888"
    for _ in range(5):
        resp = await client.post(
            "/api/devices/pair",
            json={"code": wrong, "device_name": "x", "protocol_version": 2},
        )
        assert resp.status_code == 400
    locked = await client.post(
        "/api/devices/pair",
        json={"code": real_code, "device_name": "x", "protocol_version": 2},
    )
    assert locked.status_code == 400
    assert locked.json()["detail"] == "invalid_pair_code"


async def test_device_list_exposes_status_without_token(client, auth_headers):
    _, pair = await _new_device(client, auth_headers, name="客厅机器人")
    device_id = pair.json()["device_id"]

    listing = await client.get("/api/devices", headers=auth_headers)
    assert listing.status_code == 200
    device = next(d for d in listing.json() if d["id"] == device_id)
    assert device["name"] == "客厅机器人"
    assert device["protocol_version"] == 2
    assert device["volume"] == 70
    assert device["idle_enabled"] is True
    assert "device_token" not in device
    assert "token_hash" not in device


async def test_device_rename_and_bind(client, auth_headers):
    _, pair = await _new_device(client, auth_headers)
    device_id = pair.json()["device_id"]

    rename = await client.put(
        f"/api/devices/{device_id}",
        json={"name": "书房机器人"},
        headers=auth_headers,
    )
    assert rename.status_code == 200

    binding = await client.put(
        "/api/devices/bind",
        json={"device_id": device_id, "bound_assistant_id": None, "bound_conversation_id": None},
        headers=auth_headers,
    )
    assert binding.status_code == 200


async def test_device_revoke_disables_it(client, auth_headers):
    _, pair = await _new_device(client, auth_headers)
    device_id = pair.json()["device_id"]

    resp = await client.delete(f"/api/devices/{device_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"

    listing = await client.get("/api/devices", headers=auth_headers)
    device = next(d for d in listing.json() if d["id"] == device_id)
    assert device["is_active"] is False


async def test_device_token_cannot_be_used_as_app_token(client, auth_headers):
    _, pair = await _new_device(client, auth_headers)
    device_token = pair.json()["device_token"]
    resp = await client.get(
        "/api/assistants", headers={"Authorization": f"Bearer {device_token}"}
    )
    assert resp.status_code == 401
