"""FastAPI 鉴权依赖。

只接受 `Authorization: Bearer <app_token>`。设备 Token 与 App Token 分表存储，
因此设备 Token 在这里查不到，天然不能访问管理接口。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from companion.server.app.database import get_db
from companion.server.app.models.app_token import AppTokenModel
from companion.server.app.security.tokens import hash_token

LAST_USED_WRITE_INTERVAL_MS = 60_000


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(frozen=True)
class AuthContext:
    token_id: str
    username: str


def _parse_bearer(authorization: str | None) -> str:
    if not authorization:
        raise unauthorized()
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise unauthorized()
    return credentials.strip()


async def require_app_token(
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    raw_token = _parse_bearer(authorization)
    result = await db.execute(
        select(
            AppTokenModel.id,
            AppTokenModel.username,
            AppTokenModel.expires_at,
            AppTokenModel.revoked_at,
            AppTokenModel.last_used_at,
        ).where(AppTokenModel.token_hash == hash_token(raw_token))
    )
    row = result.first()
    if row is None:
        raise unauthorized()

    token_id, username, expires_at, revoked_at, last_used_at = row
    now = int(time.time() * 1000)
    if revoked_at is not None or expires_at <= now:
        raise unauthorized()

    if last_used_at is None or now - last_used_at > LAST_USED_WRITE_INTERVAL_MS:
        await db.execute(
            update(AppTokenModel).where(AppTokenModel.id == token_id).values(last_used_at=now)
        )
    # 统一提交：结束依赖内开启的只读事务，避免路由里 `db.begin()` 冲突。
    await db.commit()
    return AuthContext(token_id=token_id, username=username)
