from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
import time
import uuid

from companion.server.app.database import get_db
from companion.server.app.models.memory import MemoryModel

router = APIRouter(prefix="/memories", tags=["memories"])

class MemoryCreateOrUpdate(BaseModel):
    assistant_id: str | None = None
    content: str
    path: str = "default.md"

@router.get("")
async def list_memories(
    assistant_id: str | None = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(MemoryModel).order_by(MemoryModel.updated_at.desc())
    if assistant_id:
        query = query.where((MemoryModel.assistant_id == assistant_id) | (MemoryModel.assistant_id.is_(None)))
    res = await db.execute(query)
    memories = res.scalars().all()
    return [
        {
            "id": m.id,
            "assistant_id": m.assistant_id,
            "path": m.path,
            "content": m.content,
            "updated_at": m.updated_at
        }
        for m in memories
    ]

@router.post("")
async def create_memory(data: MemoryCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    new_id = str(uuid.uuid4())
    mem = MemoryModel(
        id=new_id,
        assistant_id=data.assistant_id,
        content=data.content,
        path=data.path,
        updated_at=int(time.time() * 1000)
    )
    async with db.begin():
        db.add(mem)
    return {"id": new_id, "status": "created"}

@router.put("/{memory_id}")
async def update_memory(memory_id: str, data: MemoryCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        m = await db.get(MemoryModel, memory_id)
        if not m:
            raise HTTPException(status_code=404, detail="记忆不存在")
        m.content = data.content
        m.updated_at = int(time.time() * 1000)
    return {"status": "updated"}

@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        m = await db.get(MemoryModel, memory_id)
        if not m:
            raise HTTPException(status_code=404, detail="记忆不存在")
        await db.delete(m)
    return {"status": "deleted"}
