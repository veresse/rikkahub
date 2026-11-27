"""管理员登录与 App Token 管理。"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from companion.server.app.config import settings
from companion.server.app.database import get_db
from companion.server.app.models.app_token import AppTokenModel
from companion.server.app.security.dependencies import AuthContext, require_app_token
from companion.server.app.security.password import verify_password
from companion.server.app.security.rate_limit import (
    clear,
    is_limited,
    record,
    retry_after_seconds,
)
from companion.server.app.security.tokens import generate_token, hash_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = "admin"
    password: str = Field(min_length=1)
    device_name: str = ""


class TokenView(BaseModel):
    id: str
    username: str
    device_name: str
    created_at: int
    last_used_at: int | None
    expires_at: int
    revoked_at: int | None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _password_configured() -> bool:
    return bool(settings.ADMIN_PASSWORD or settings.ADMIN_PASSWORD_HASH)


def _verify_admin_password(password: str) -> bool:
    if settings.ADMIN_PASSWORD_HASH:
        return verify_password(password, settings.ADMIN_PASSWORD_HASH)
    if settings.ADMIN_PASSWORD:
        return hmac.compare_digest(password, settings.ADMIN_PASSWORD)
    return False


@router.post("/login")
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    if not _password_configured():
        raise HTTPException(status_code=503, detail="admin_password_not_configured")

    rate_key = f"login:{_client_ip(request)}:{req.username}"
    if is_limited(rate_key, settings.LOGIN_RATE_LIMIT, settings.LOGIN_RATE_WINDOW_SECONDS):
        retry_after = retry_after_seconds(
            rate_key, settings.LOGIN_RATE_WINDOW_SECONDS
        )
        raise HTTPException(
            status_code=429,
            detail="rate_limited",
            headers={"Retry-After": str(retry_after)},
        )

    username_ok = hmac.compare_digest(req.username, settings.ADMIN_USERNAME)
    password_ok = _verify_admin_password(req.password)
    if not (username_ok and password_ok):
        record(rate_key, settings.LOGIN_RATE_WINDOW_SECONDS)
        raise HTTPException(status_code=401, detail="invalid_credentials")

    clear(rate_key)

    raw_token = generate_token()
    now = _now_ms()
    expires_at = now + settings.APP_TOKEN_EXPIRE_DAYS * 86_400_000
    token = AppTokenModel(
        username=req.username,
        device_name=req.device_name,
        token_hash=hash_token(raw_token),
        created_at=now,
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()

    return {
        "token": raw_token,
        "token_id": token.id,
        "username": token.username,
        "expires_at": expires_at,
    }


@router.post("/logout")
async def logout(
    auth: AuthContext = Depends(require_app_token),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(AppTokenModel)
        .where(AppTokenModel.id == auth.token_id, AppTokenModel.revoked_at.is_(None))
        .values(revoked_at=_now_ms())
    )
    await db.commit()
    await _close_ws_connections(auth.token_id)
    return {"status": "logged_out"}


@router.get("/tokens")
async def list_tokens(
    auth: AuthContext = Depends(require_app_token),
    db: AsyncSession = Depends(get_db),
):
    # 只列仍然有效的登录（未撤销、未过期）；撤销/过期的行保留在库里
    # 供审计，但不能再出现在设备管理列表里。
    now = _now_ms()
    result = await db.execute(
        select(AppTokenModel)
        .where(
            AppTokenModel.revoked_at.is_(None),
            AppTokenModel.expires_at > now,
        )
        .order_by(AppTokenModel.created_at.desc())
    )
    tokens = result.scalars().all()
    return [
        TokenView(
            id=t.id,
            username=t.username,
            device_name=t.device_name,
            created_at=t.created_at,
            last_used_at=t.last_used_at,
            expires_at=t.expires_at,
            revoked_at=t.revoked_at,
        ).model_dump()
        for t in tokens
    ]


@router.post("/tokens/{token_id}/revoke")
async def revoke_token(
    token_id: str,
    auth: AuthContext = Depends(require_app_token),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        update(AppTokenModel)
        .where(AppTokenModel.id == token_id, AppTokenModel.revoked_at.is_(None))
        .values(revoked_at=_now_ms())
    )
    if result.rowcount == 0:
        # 已撤销 / 不存在：不能再报成功，否则 App 会显示一次空操作成功。
        await db.rollback()
        raise HTTPException(status_code=404, detail="token_not_found")
    await db.commit()
    await _close_ws_connections(token_id)
    return {"status": "revoked", "token_id": token_id}


async def _close_ws_connections(token_id: str) -> None:
    """撤销 Token 后立刻断开它已建立的 `/app/ws` 连接。"""
    from companion.server.app.routers.ws import app_gateway

    await app_gateway.revoke(token_id)
