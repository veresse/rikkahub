from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
import time
import uuid

from companion.server.app.database import get_db
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.services.orchestrator import get_persist_lock

router = APIRouter(prefix="/conversations", tags=["conversations"])

class ConversationCreate(BaseModel):
    assistant_id: str
    title: str = "新对话"

class ConversationUpdate(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None
    custom_system_prompt: str | None = None

class MessageTextUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


def _new_api_error(status: int, code: str, message: str) -> JSONResponse:
    """新接口统一错误体 `{detail: code, message: 可读文本}`（旧接口不改）。"""
    return JSONResponse(
        status_code=status, content={"detail": code, "message": message}
    )

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
    new_id = str(uuid.uuid4())
    conv = ConversationModel(
        id=new_id,
        assistant_id=data.assistant_id,
        title=data.title,
        created_at=int(time.time() * 1000),
        updated_at=int(time.time() * 1000)
    )
    async with db.begin():
        # 必须先开启事务再查询：db.get() 会自动开启事务，之后再调 begin() 会抛
        # InvalidRequestError("A transaction is already begun on this Session")。
        assistant = await db.get(AssistantModel, data.assistant_id)
        if not assistant:
            raise HTTPException(status_code=404, detail="关联角色不存在")
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

@router.put("/{conv_id}/nodes/{node_id}/messages/{message_id}")
async def update_message_text(
    conv_id: str,
    node_id: str,
    message_id: str,
    data: MessageTextUpdate,
    db: AsyncSession = Depends(get_db),
):
    """纠正已落库的 USER 消息文本（语音转写纠错用）。

    只替换第一个 text part，其他 part 原样保留；不触发重新生成，
    由 App 提示“回答未更新”。

    读改写与 `_save_turn` 共用会话级落库锁：重新生成会把候选追加到同一
    节点的 JSON 列，无锁并发写会互相覆盖。
    """
    async with get_persist_lock(conv_id):
        node = await db.get(MessageNodeModel, node_id)
        if node is None or node.conversation_id != conv_id:
            return _new_api_error(404, "node_not_found", "节点不存在")

        messages = list(node.messages or [])
        index = next(
            (i for i, m in enumerate(messages) if m.get("id") == message_id), None
        )
        if index is None:
            return _new_api_error(404, "message_not_found", "消息不存在")

        message = dict(messages[index])
        if str(message.get("role") or "").upper() != "USER":
            return _new_api_error(400, "not_user_message", "只能修改用户消息")

        parts = [dict(p) for p in (message.get("parts") or [])]
        replaced = False
        for part in parts:
            if "text" in part:
                part["text"] = data.text
                replaced = True
                break
        if not replaced:
            parts.insert(0, {"text": data.text})

        message["parts"] = parts
        messages[index] = message
        # 整体赋新 list，确保 SQLAlchemy 能检测到 JSON 列变更。
        node.messages = messages
        conv = await db.get(ConversationModel, conv_id)
        if conv is not None:
            conv.updated_at = int(time.time() * 1000)
        await db.commit()

    return {
        "status": "updated",
        "node_id": node_id,
        "message_id": message_id,
        "text": data.text,
    }


@router.delete("/{conv_id}")
async def delete_conversation(conv_id: str, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        conv = await db.get(ConversationModel, conv_id)
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")
        await db.delete(conv)
    return {"status": "deleted"}
