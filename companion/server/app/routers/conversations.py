from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
import time
import uuid

from companion.server.app.database import get_db
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.models.assistant import AssistantModel

router = APIRouter(prefix="/conversations", tags=["conversations"])

class ConversationCreate(BaseModel):
    assistant_id: str
    title: str = "新对话"

class ConversationUpdate(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None
    custom_system_prompt: str | None = None

@router.get("")
async def list_conversations(
    assistant_id: str | None = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(ConversationModel).order_by(ConversationModel.is_pinned.desc(), ConversationModel.updated_at.desc())
    if assistant_id:
        query = query.where(ConversationModel.assistant_id == assistant_id)
    res = await db.execute(query)
    convs = res.scalars().all()
    return [
        {
            "id": c.id,
            "assistant_id": c.assistant_id,
            "title": c.title,
            "is_pinned": c.is_pinned,
            "custom_system_prompt": c.custom_system_prompt,
            "created_at": c.created_at,
            "updated_at": c.updated_at
        }
        for c in convs
    ]

@router.post("")
async def create_conversation(data: ConversationCreate, db: AsyncSession = Depends(get_db)):
    assistant = await db.get(AssistantModel, data.assistant_id)
    if not assistant:
        raise HTTPException(status_code=404, detail="关联角色不存在")

    new_id = str(uuid.uuid4())
    conv = ConversationModel(
        id=new_id,
        assistant_id=data.assistant_id,
        title=data.title,
        created_at=int(time.time() * 1000),
        updated_at=int(time.time() * 1000)
    )
    async with db.begin():
        db.add(conv)
    return {"id": new_id, "status": "created"}

@router.get("/{conv_id}")
async def get_conversation_detail(conv_id: str, db: AsyncSession = Depends(get_db)):
    conv = await db.get(ConversationModel, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 获取关联节点树
    node_query = select(MessageNodeModel).where(
        MessageNodeModel.conversation_id == conv_id
    ).order_by(MessageNodeModel.node_index.asc())
    node_res = await db.execute(node_query)
    nodes = node_res.scalars().all()

    return {
        "id": conv.id,
        "assistant_id": conv.assistant_id,
        "title": conv.title,
        "is_pinned": conv.is_pinned,
        "custom_system_prompt": conv.custom_system_prompt,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "nodes": [
            {
                "id": n.id,
                "node_index": n.node_index,
                "messages": n.messages,
                "select_index": n.select_index,
                "total_branches": len(n.messages)
            }
            for n in nodes
        ]
    }

@router.post("/{conv_id}/nodes/{node_id}/select")
async def select_node_branch(
    conv_id: str,
    node_id: str,
    index: int = Query(..., ge=0),
    db: AsyncSession = Depends(get_db)
):
    """
    切换某轮消息的分支版本 (例如将当前选中的第 1 条切换为第 2 条候选回答)
    """
    async with db.begin():
        node = await db.get(MessageNodeModel, node_id)
        if not node or node.conversation_id != conv_id:
            raise HTTPException(status_code=404, detail="节点不存在")

        if index >= len(node.messages):
            raise HTTPException(status_code=400, detail="分支索引超出有效范围")

        node.select_index = index
    return {"status": "success", "node_id": node_id, "select_index": index}

@router.delete("/{conv_id}")
async def delete_conversation(conv_id: str, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        conv = await db.get(ConversationModel, conv_id)
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")
        await db.delete(conv)
    return {"status": "deleted"}
