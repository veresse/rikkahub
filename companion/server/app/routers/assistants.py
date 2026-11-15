from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Any
import uuid

from companion.server.app.database import get_db
from companion.server.app.models.assistant import AssistantModel

router = APIRouter(prefix="/assistants", tags=["assistants"])

class AssistantCreateOrUpdate(BaseModel):
    name: str
    system_prompt: str = ""
    temperature: float | None = 0.7
    top_p: float | None = 0.9
    context_message_limit: int = 0
    enable_memory: bool = False
    use_global_memory: bool = False
    enable_web_search: bool = False
    avatar: dict[str, Any] = {}

@router.get("")
async def list_assistants(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(AssistantModel))
    assistants = res.scalars().all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "system_prompt": a.system_prompt,
            "temperature": a.temperature,
            "top_p": a.top_p,
            "context_message_limit": a.context_message_limit,
            "enable_memory": a.enable_memory,
            "use_global_memory": a.use_global_memory,
            "enable_web_search": a.enable_web_search,
            "avatar": a.avatar
        }
        for a in assistants
    ]

@router.get("/{assistant_id}")
async def get_assistant(assistant_id: str, db: AsyncSession = Depends(get_db)):
    a = await db.get(AssistantModel, assistant_id)
    if not a:
        raise HTTPException(status_code=404, detail="角色不存在")
    return {
        "id": a.id,
        "name": a.name,
        "system_prompt": a.system_prompt,
        "temperature": a.temperature,
        "top_p": a.top_p,
        "context_message_limit": a.context_message_limit,
        "enable_memory": a.enable_memory,
        "use_global_memory": a.use_global_memory,
        "enable_web_search": a.enable_web_search,
        "avatar": a.avatar
    }

@router.post("")
async def create_assistant(data: AssistantCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    new_id = str(uuid.uuid4())
    assistant = AssistantModel(
        id=new_id,
        name=data.name,
        system_prompt=data.system_prompt,
        temperature=data.temperature,
        top_p=data.top_p,
        context_message_limit=data.context_message_limit,
        enable_memory=data.enable_memory,
        use_global_memory=data.use_global_memory,
        enable_web_search=data.enable_web_search,
        avatar=data.avatar
    )
    async with db.begin():
        db.add(assistant)
    return {"id": new_id, "status": "created"}

@router.put("/{assistant_id}")
async def update_assistant(assistant_id: str, data: AssistantCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        a = await db.get(AssistantModel, assistant_id)
        if not a:
            raise HTTPException(status_code=404, detail="角色不存在")
        a.name = data.name
        a.system_prompt = data.system_prompt
        a.temperature = data.temperature
        a.top_p = data.top_p
        a.context_message_limit = data.context_message_limit
        a.enable_memory = data.enable_memory
        a.use_global_memory = data.use_global_memory
        a.enable_web_search = data.enable_web_search
        a.avatar = data.avatar
    return {"status": "updated"}

@router.delete("/{assistant_id}")
async def delete_assistant(assistant_id: str, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        a = await db.get(AssistantModel, assistant_id)
        if not a:
            raise HTTPException(status_code=404, detail="角色不存在")
        await db.delete(a)
    return {"status": "deleted"}
