from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Any, Literal
import uuid

from companion.server.app.database import get_db
from companion.server.app.models.assistant import AssistantModel

router = APIRouter(prefix="/assistants", tags=["assistants"])

WebSearchMode = Literal["off", "auto", "on"]

class AssistantCreateOrUpdate(BaseModel):
    name: str
    system_prompt: str = ""
    temperature: float | None = 0.7
    top_p: float | None = 0.9
    context_message_limit: int = 0
    enable_memory: bool = False
    use_global_memory: bool = False
    # 兼容旧客户端：未带 web_search_mode 时按该布尔值推导（true -> auto, false -> off）。
    enable_web_search: bool = False
    web_search_mode: WebSearchMode | None = None
    enable_action_sync: bool = True
    model_id: str | None = None
    avatar: dict[str, Any] = {}


def _search_mode(data: AssistantCreateOrUpdate) -> str:
    if data.web_search_mode is not None:
        return data.web_search_mode
    return "auto" if data.enable_web_search else "off"


def _payload(a: AssistantModel) -> dict[str, Any]:
    mode = getattr(a, "web_search_mode", None) or (
        "auto" if a.enable_web_search else "off"
    )
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
        "web_search_mode": mode,
        "enable_action_sync": a.enable_action_sync,
        "model_id": a.model_id,
        "avatar": a.avatar,
    }


@router.get("")
async def list_assistants(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(AssistantModel))
    assistants = res.scalars().all()
    return [_payload(a) for a in assistants]

@router.get("/{assistant_id}")
async def get_assistant(assistant_id: str, db: AsyncSession = Depends(get_db)):
    a = await db.get(AssistantModel, assistant_id)
    if not a:
        raise HTTPException(status_code=404, detail="角色不存在")
    return _payload(a)

@router.post("")
async def create_assistant(data: AssistantCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    new_id = str(uuid.uuid4())
    mode = _search_mode(data)
    assistant = AssistantModel(
        id=new_id,
        name=data.name,
        system_prompt=data.system_prompt,
        temperature=data.temperature,
        top_p=data.top_p,
        context_message_limit=data.context_message_limit,
        enable_memory=data.enable_memory,
        use_global_memory=data.use_global_memory,
        enable_web_search=mode != "off",
        web_search_mode=mode,
        enable_action_sync=data.enable_action_sync,
        model_id=data.model_id,
        avatar=data.avatar
    )
    async with db.begin():
        db.add(assistant)
    return {"id": new_id, "status": "created"}

@router.put("/{assistant_id}")
async def update_assistant(assistant_id: str, data: AssistantCreateOrUpdate, db: AsyncSession = Depends(get_db)):
    mode = _search_mode(data)
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
        a.enable_web_search = mode != "off"
        a.web_search_mode = mode
        a.enable_action_sync = data.enable_action_sync
        # 旧客户端不认识这两个字段：未显式提供时保持原值，避免编辑一次就被清空。
        if "model_id" in data.model_fields_set:
            a.model_id = data.model_id
        if "avatar" in data.model_fields_set:
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
