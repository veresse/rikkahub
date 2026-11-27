import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from companion.server.app.database import get_db
from companion.server.app.services.orchestrator import ChatOrchestrator

router = APIRouter(prefix="/chat", tags=["chat"])

class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    client_msg_id: str | None = Field(default=None, min_length=1, max_length=128)
    regenerate_node_id: str | None = None

@router.post("/completions")
async def chat_completions(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    """
    流式对话接口 (Server-Sent Events 协议)
    前端通过 EventSource 或 fetch 流式读取事件
    """
    async def event_generator():
        try:
            async for chunk in ChatOrchestrator.stream_chat(
                db=db,
                conversation_id=req.conversation_id,
                user_text=req.message,
                regenerate_node_id=req.regenerate_node_id,
                client_msg_id=req.client_msg_id,
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as e:
            err_payload = {"type": "error", "error": str(e)}
            yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用 Nginx 缓冲区，保证逐字实时吐出
        }
    )
