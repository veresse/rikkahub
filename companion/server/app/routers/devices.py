"""设备配对与设备管理。

- `POST /api/devices/pair-code` 需要 App Token；
- `POST /api/devices/pair` 是设备侧公开接口，靠一次性配对码换取设备 Token；
- 其余接口需要 App Token。

设备 Token 与 App Token 分表存储，互不通用。
"""

from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.server.app.config import settings
from companion.server.app.database import get_db
from companion.server.app.models.device import DeviceModel
from companion.server.app.models.pairing_code import PairingCodeModel
from companion.server.app.security.dependencies import AuthContext, require_app_token
from companion.server.app.security.rate_limit import clear, is_limited, record
from companion.server.app.security.tokens import generate_token, hash_token
from companion.shared.catalog import get_catalog

router = APIRouter(prefix="/devices", tags=["devices"])

PAIR_CODE_RATE_LIMIT = 5
PAIR_CODE_RATE_WINDOW_SECONDS = 60
PAIR_ATTEMPT_WINDOW_SECONDS = 3600


class DeviceBindRequest(BaseModel):
    device_id: str
    bound_assistant_id: str | None = None
    bound_conversation_id: str | None = None


class PairingCodeRequest(BaseModel):
    assistant_id: str | None = None
    conversation_id: str | None = None


class DevicePairRequest(BaseModel):
    code: str = Field(min_length=6, max_length=12)
    device_name: str = Field(default="桌面机器人", max_length=100)
    firmware_version: str = Field(default="2.0.0", max_length=32)
    protocol_version: int = 2
    capabilities: list[str] = Field(default_factory=lambda: ["action", "volume", "expression"])


class DeviceRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _serialize_device(device: DeviceModel) -> dict:
    return {
        "id": device.id,
        "name": device.name,
        "protocol_version": device.protocol_version,
        "firmware_version": device.firmware_version,
        "capabilities": device.capabilities_json or [],
        "bound_assistant_id": device.bound_assistant_id,
        "bound_conversation_id": device.bound_conversation_id,
        "is_active": device.is_active,
        "volume": device.volume,
        "idle_enabled": device.idle_enabled,
        "pan_offset": device.pan_offset,
        "tilt_offset": device.tilt_offset,
        "state": device.state,
        "last_report": device.last_report_json,
        "created_at": device.created_at,
        "last_seen_at": device.last_seen_at,
    }


@router.post("/pair-code")
async def generate_pairing_code(
    req: PairingCodeRequest,
    auth: AuthContext = Depends(require_app_token),
    db: AsyncSession = Depends(get_db),
):
    """生成 6 位一次性配对码，有效期由 `PAIR_CODE_TTL_SECONDS` 控制。"""
    rate_key = f"pair-code:{auth.token_id}"
    if is_limited(rate_key, PAIR_CODE_RATE_LIMIT, PAIR_CODE_RATE_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="rate_limited")
    record(rate_key, PAIR_CODE_RATE_WINDOW_SECONDS)

    code = f"{secrets.randbelow(900_000) + 100_000}"
    now = _now_ms()
    db.add(
        PairingCodeModel(
            code_hash=hash_token(code),
            assistant_id=req.assistant_id,
            conversation_id=req.conversation_id,
            created_at=now,
            expires_at=now + settings.PAIR_CODE_TTL_SECONDS * 1000,
        )
    )
    await db.commit()
    return {"code": code, "expires_in": settings.PAIR_CODE_TTL_SECONDS}


@router.post("/pair")
async def pair_device(
    req: DevicePairRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """设备用配对码换取长期设备 Token（明文 Token 只返回这一次）。"""
    attempt_key = f"pair-attempt:{_client_ip(request)}"
    if is_limited(attempt_key, settings.PAIR_CODE_MAX_ATTEMPTS, PAIR_ATTEMPT_WINDOW_SECONDS):
        raise HTTPException(status_code=400, detail="invalid_pair_code")

    now = _now_ms()
    result = await db.execute(
        select(PairingCodeModel).where(PairingCodeModel.code_hash == hash_token(req.code))
    )
    pairing = result.scalar_one_or_none()

    if pairing is None or pairing.used_at is not None:
        record(attempt_key, PAIR_ATTEMPT_WINDOW_SECONDS)
        raise HTTPException(status_code=400, detail="invalid_pair_code")

    if pairing.expires_at <= now:
        raise HTTPException(status_code=410, detail="pair_code_expired")

    pairing.used_at = now
    clear(attempt_key)

    raw_token = generate_token()
    device = DeviceModel(
        name=req.device_name,
        token_hash=hash_token(raw_token),
        protocol_version=req.protocol_version,
        firmware_version=req.firmware_version,
        capabilities_json=list(req.capabilities),
        bound_assistant_id=pairing.assistant_id,
        bound_conversation_id=pairing.conversation_id,
        last_seen_at=now,
    )
    db.add(device)
    await db.commit()

    return {
        "device_id": device.id,
        "device_token": raw_token,
        "protocol_version": device.protocol_version,
        "action_catalog_version": get_catalog()["version"],
    }


@router.get("")
async def list_devices(
    auth: AuthContext = Depends(require_app_token),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(DeviceModel).order_by(DeviceModel.created_at.desc()))
    return [_serialize_device(device) for device in result.scalars().all()]


@router.put("/bind")
async def bind_device(
    req: DeviceBindRequest,
    auth: AuthContext = Depends(require_app_token),
    db: AsyncSession = Depends(get_db),
):
    async with db.begin():
        device = await db.get(DeviceModel, req.device_id)
        if not device:
            raise HTTPException(status_code=404, detail="device_not_found")
        device.bound_assistant_id = req.bound_assistant_id
        device.bound_conversation_id = req.bound_conversation_id
    return {"status": "bound"}


@router.put("/{device_id}")
async def rename_device(
    device_id: str,
    req: DeviceRenameRequest,
    auth: AuthContext = Depends(require_app_token),
    db: AsyncSession = Depends(get_db),
):
    async with db.begin():
        device = await db.get(DeviceModel, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="device_not_found")
        device.name = req.name
    return {"status": "updated"}


@router.delete("/{device_id}")
async def revoke_device(
    device_id: str,
    auth: AuthContext = Depends(require_app_token),
    db: AsyncSession = Depends(get_db),
):
    """撤销设备：软删除并让旧 Token 立即失效（保留历史记录）。"""
    async with db.begin():
        device = await db.get(DeviceModel, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="device_not_found")
        device.is_active = False
        # 换成一个不可能再被使用的随机哈希，旧 Token 立即失效。
        device.token_hash = hash_token(generate_token())
        device.state = "revoked"
    return {"status": "revoked"}
