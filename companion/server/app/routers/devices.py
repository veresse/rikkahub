import secrets
import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from companion.server.app.database import get_db
from companion.server.app.models.device import DeviceModel

router = APIRouter(prefix="/devices", tags=["devices"])

# 内存短期配对码缓存：pair_code -> {"created_at": ..., "assistant_id": ..., "conversation_id": ...}
_PAIRING_CODES: dict[str, dict] = {}

class DeviceBindRequest(BaseModel):
    device_id: str
    bound_assistant_id: str | None = None
    bound_conversation_id: str | None = None

class PairingCodeRequest(BaseModel):
    assistant_id: str | None = None
    conversation_id: str | None = None

@router.post("/pair-code")
async def generate_pairing_code(req: PairingCodeRequest):
    """
    网页端生成 6 位一次性短期配对码，有效期 5 分钟
    """
    code = f"{secrets.randbelow(900000) + 100000}"
    _PAIRING_CODES[code] = {
        "created_at": time.time(),
        "assistant_id": req.assistant_id,
        "conversation_id": req.conversation_id
    }
    return {"code": code, "expires_in": 300}

@router.get("")
async def list_devices(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(DeviceModel).order_by(DeviceModel.created_at.desc()))
    devices = res.scalars().all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "bound_assistant_id": d.bound_assistant_id,
            "bound_conversation_id": d.bound_conversation_id,
            "is_active": d.is_active,
            "created_at": d.created_at,
            "last_seen_at": d.last_seen_at
        }
        for d in devices
    ]

@router.put("/bind")
async def bind_device(req: DeviceBindRequest, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        dev = await db.get(DeviceModel, req.device_id)
        if not dev:
            raise HTTPException(status_code=404, detail="设备不存在")
        dev.bound_assistant_id = req.bound_assistant_id
        dev.bound_conversation_id = req.bound_conversation_id
    return {"status": "bound"}

@router.delete("/{device_id}")
async def revoke_device(device_id: str, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        dev = await db.get(DeviceModel, device_id)
        if not dev:
            raise HTTPException(status_code=404, detail="设备不存在")
        await db.delete(dev)
    return {"status": "revoked"}
