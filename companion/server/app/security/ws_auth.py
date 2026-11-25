"""WebSocket 握手鉴权。

REST 的 `require_app_token` 依赖 FastAPI 的 HTTPException，不能直接用于
WebSocket；这里返回结构化结果，由 `routers/ws.py` 决定关闭码。
设备 Token 与 App Token 分表查询，互相冒用统一返回 4403。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from companion.server.app.models.app_token import AppTokenModel
from companion.server.app.models.device import DeviceModel
from companion.server.app.security.tokens import hash_token

LAST_USED_WRITE_INTERVAL_MS = 60_000

CLOSE_UNAUTHORIZED = 4401
CLOSE_WRONG_TOKEN_TYPE = 4403


@dataclass(frozen=True)
class WSAuthResult:
    ok: bool
    device_id: str | None = None
    token_id: str | None = None
    close_code: int = 0
    reason: str = ""


def parse_bearer_header(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, credentials = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = credentials.strip()
    return token or None


async def _find_device(db: AsyncSession, digest: str, *, active_only: bool):
    query = select(DeviceModel).where(DeviceModel.token_hash == digest)
    if active_only:
        query = query.where(DeviceModel.is_active.is_(True))
    result = await db.execute(query)
    return result.scalars().first()


async def _app_token_row_exists(db: AsyncSession, digest: str) -> bool:
    result = await db.execute(
        select(AppTokenModel.id).where(AppTokenModel.token_hash == digest)
    )
    return result.first() is not None


async def authenticate_ws(
    db: AsyncSession,
    raw_token: str | None,
    *,
    expected: Literal["app", "device"],
) -> WSAuthResult:
    if not raw_token:
        return WSAuthResult(ok=False, close_code=CLOSE_UNAUTHORIZED, reason="unauthorized")

    digest = hash_token(raw_token)

    if expected == "device":
        device = await _find_device(db, digest, active_only=True)
        if device is not None:
            return WSAuthResult(ok=True, device_id=device.id)
        if await _app_token_row_exists(db, digest):
            return WSAuthResult(
                ok=False, close_code=CLOSE_WRONG_TOKEN_TYPE, reason="wrong_token_type"
            )
        return WSAuthResult(ok=False, close_code=CLOSE_UNAUTHORIZED, reason="unauthorized")

    result = await db.execute(
        select(AppTokenModel).where(AppTokenModel.token_hash == digest)
    )
    token = result.scalars().first()
    if token is None:
        if await _find_device(db, digest, active_only=False) is not None:
            return WSAuthResult(
                ok=False, close_code=CLOSE_WRONG_TOKEN_TYPE, reason="wrong_token_type"
            )
        return WSAuthResult(ok=False, close_code=CLOSE_UNAUTHORIZED, reason="unauthorized")

    now = int(time.time() * 1000)
    if token.revoked_at is not None or token.expires_at <= now:
        return WSAuthResult(ok=False, close_code=CLOSE_UNAUTHORIZED, reason="unauthorized")

    if token.last_used_at is None or now - token.last_used_at > LAST_USED_WRITE_INTERVAL_MS:
        await db.execute(
            update(AppTokenModel)
            .where(AppTokenModel.id == token.id)
            .values(last_used_at=now)
        )
        await db.commit()

    return WSAuthResult(ok=True, token_id=token.id)
