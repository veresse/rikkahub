"""WebSocket 握手鉴权测试：设备/App Token 分表隔离与撤销/过期校验。"""

import time

import pytest
from sqlalchemy import delete

from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.app_token import AppTokenModel
from companion.server.app.models.device import DeviceModel
from companion.server.app.security.tokens import hash_token
from companion.server.app.security.ws_auth import authenticate_ws, parse_bearer_header

APP_TOKEN = "app-token-plain"
APP_TOKEN_ID = "tok-ws-auth"
DEVICE_TOKEN = "device-token-plain"
DEVICE_ID = "dev-ws-auth"


@pytest.fixture
async def seeded_tokens():
    now = int(time.time() * 1000)
    async with AsyncSessionLocal() as session:
        session.add(
            AppTokenModel(
                id=APP_TOKEN_ID,
                username="admin",
                device_name="pytest",
                token_hash=hash_token(APP_TOKEN),
                expires_at=now + 3_600_000,
            )
        )
        session.add(
            DeviceModel(
                id=DEVICE_ID,
                name="鉴权测试机",
                token_hash=hash_token(DEVICE_TOKEN),
            )
        )
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AppTokenModel).where(AppTokenModel.id == APP_TOKEN_ID))
        await session.execute(delete(DeviceModel).where(DeviceModel.id == DEVICE_ID))
        await session.commit()


def test_parse_bearer_header():
    assert parse_bearer_header("Bearer abc") == "abc"
    assert parse_bearer_header("bearer  abc ") == "abc"
    assert parse_bearer_header("Basic abc") is None
    assert parse_bearer_header("Bearer ") is None
    assert parse_bearer_header(None) is None


async def test_device_token_on_device_channel(seeded_tokens):
    async with AsyncSessionLocal() as db:
        result = await authenticate_ws(db, DEVICE_TOKEN, expected="device")
    assert result.ok is True
    assert result.device_id == DEVICE_ID


async def test_app_token_cannot_open_device_channel(seeded_tokens):
    async with AsyncSessionLocal() as db:
        result = await authenticate_ws(db, APP_TOKEN, expected="device")
    assert result.ok is False
    assert result.close_code == 4403
    assert result.reason == "wrong_token_type"


async def test_unknown_token_on_device_channel(seeded_tokens):
    async with AsyncSessionLocal() as db:
        result = await authenticate_ws(db, "nope", expected="device")
    assert result.ok is False
    assert result.close_code == 4401


async def test_app_token_on_app_channel_and_last_used(seeded_tokens):
    async with AsyncSessionLocal() as db:
        result = await authenticate_ws(db, APP_TOKEN, expected="app")
    assert result.ok is True
    assert result.token_id == APP_TOKEN_ID

    async with AsyncSessionLocal() as db:
        token = await db.get(AppTokenModel, APP_TOKEN_ID)
        assert token.last_used_at is not None


async def test_device_token_cannot_open_app_channel(seeded_tokens):
    async with AsyncSessionLocal() as db:
        result = await authenticate_ws(db, DEVICE_TOKEN, expected="app")
    assert result.ok is False
    assert result.close_code == 4403
    assert result.reason == "wrong_token_type"


async def test_revoked_app_token_is_rejected(seeded_tokens):
    async with AsyncSessionLocal() as db:
        token = await db.get(AppTokenModel, APP_TOKEN_ID)
        token.revoked_at = int(time.time() * 1000)
        await db.commit()
    async with AsyncSessionLocal() as db:
        result = await authenticate_ws(db, APP_TOKEN, expected="app")
    assert result.ok is False
    assert result.close_code == 4401


async def test_expired_app_token_is_rejected(seeded_tokens):
    async with AsyncSessionLocal() as db:
        token = await db.get(AppTokenModel, APP_TOKEN_ID)
        token.expires_at = 1
        await db.commit()
    async with AsyncSessionLocal() as db:
        result = await authenticate_ws(db, APP_TOKEN, expected="app")
    assert result.ok is False
    assert result.close_code == 4401


async def test_revoked_device_is_rejected(seeded_tokens):
    async with AsyncSessionLocal() as db:
        device = await db.get(DeviceModel, DEVICE_ID)
        device.is_active = False
        await db.commit()
    async with AsyncSessionLocal() as db:
        result = await authenticate_ws(db, DEVICE_TOKEN, expected="device")
    assert result.ok is False
    assert result.close_code == 4401


async def test_missing_token(seeded_tokens):
    async with AsyncSessionLocal() as db:
        result = await authenticate_ws(db, None, expected="app")
    assert result.ok is False
    assert result.close_code == 4401
    assert result.reason == "unauthorized"
