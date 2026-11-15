import asyncio
import json
import time
import uuid
from typing import AsyncGenerator, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.models.memory import MemoryModel
from companion.server.app.services.deepseek_client import DeepSeekClient
from companion.server.app.services.search_provider import SearchProvider

# 内存会话锁字典，保证同一会话同一时刻单轮生成
_CONVERSATION_LOCKS: dict[str, asyncio.Lock] = {}

def get_conversation_lock(conv_id: str) -> asyncio.Lock:
    if conv_id not in _CONVERSATION_LOCKS:
        _CONVERSATION_LOCKS[conv_id] = asyncio.Lock()
    return _CONVERSATION_LOCKS[conv_id]

class ChatOrchestrator:
    @staticmethod
    async def build_context(
        db: AsyncSession,
        conversation_id: str,
        user_text: str
    ) -> tuple[AssistantModel, list[dict[str, str]], list[MessageNodeModel]]:
        """
        构建用于大模型的上下文消消息列表
        组装顺序: System Prompt + 记忆 -> 选定分支的历史消息 (截断) -> 当前用户提问
        """
        # 1. 查询会话与助手
        conv = await db.get(ConversationModel, conversation_id)
        if not conv:
            raise ValueError(f"会话不存在: {conversation_id}")

        assistant = await db.get(AssistantModel, conv.assistant_id)
        if not assistant:
            raise ValueError(f"关联角色不存在: {conv.assistant_id}")

        # 2. 组装 System Prompt 与记忆
        system_content_parts = []
        if assistant.system_prompt:
            system_content_parts.append(assistant.system_prompt.strip())

        if conv.custom_system_prompt:
            system_content_parts.append(conv.custom_system_prompt.strip())

        # 检索记忆 (若开启)
        if assistant.enable_memory:
            mem_query = select(MemoryModel)
            if assistant.use_global_memory:
                mem_query = mem_query.where((MemoryModel.assistant_id == assistant.id) | (MemoryModel.assistant_id.is_(None)))
            else:
                mem_query = mem_query.where(MemoryModel.assistant_id == assistant.id)

            mem_res = await db.execute(mem_query)
            memories = mem_res.scalars().all()
            if memories:
                memory_texts = "\n".join([f"- {m.content.strip()}" for m in memories if m.content.strip()])
                if memory_texts:
                    system_content_parts.append(f"\n【关于用户的长期记忆】\n{memory_texts}")

        system_message = {"role": "system", "content": "\n\n".join(system_content_parts)}

        # 3. 读取历史消息树节点
        node_query = select(MessageNodeModel).where(
            MessageNodeModel.conversation_id == conversation_id
        ).order_by(MessageNodeModel.node_index.asc())
        node_res = await db.execute(node_query)
        all_nodes = list(node_res.scalars().all())

        # 提取选中的历史消息
        history_messages = []
        for node in all_nodes:
            if not node.messages:
                continue
            idx = min(max(0, node.select_index), len(node.messages) - 1)
            msg = node.messages[idx]
            role = str(msg.get("role", "user")).lower()
            # 提取纯文本内容
            parts = msg.get("parts", [])
            text_content = ""
            for p in parts:
                if isinstance(p, dict) and "text" in p:
                    text_content += p["text"]
            if text_content.strip():
                history_messages.append({"role": role, "content": text_content.strip()})

        # 4. 根据角色的 context_message_limit 进行截断
        if assistant.context_message_limit > 0 and len(history_messages) > assistant.context_message_limit:
            history_messages = history_messages[-assistant.context_message_limit:]

        # 5. 组合全部消息
        full_messages = [system_message] + history_messages + [{"role": "user", "content": user_text}]
        return assistant, full_messages, all_nodes

    @classmethod
    async def stream_chat(
        cls,
        db: AsyncSession,
        conversation_id: str,
        user_text: str,
        regenerate_node_id: str | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        全流程流式生成与落盘逻辑
        """
        lock = get_conversation_lock(conversation_id)
        if lock.locked():
            yield {"type": "error", "error": "该对话当前正在生成回复，请稍候"}
            return

        async with lock:
            assistant, messages, all_nodes = await cls.build_context(db, conversation_id, user_text)

            # 联网搜索支持判断 (简易意图触发)
            if assistant.enable_web_search and any(w in user_text for w in ["今天", "新闻", "天气", "最新", "查一下", "搜索"]):
                yield {"type": "status", "status": "searching", "message": "正在联网搜索实时信息..."}
                search_results = await SearchProvider.search(user_text)
                if search_results:
                    search_context = "\n".join([f"[{i+1}] {r['title']}: {r['snippet']} ({r['url']})" for i, r in enumerate(search_results)])
                    messages.insert(-1, {
                        "role": "system",
                        "content": f"【联网实时搜索结果】\n{search_context}\n请结合以上检索到的实时信息回答，并在合适处引用来源。"
                    })
                    yield {"type": "status", "status": "search_done", "results": search_results}

            client = DeepSeekClient()
            full_response_text = ""
            full_reasoning_text = ""

            yield {"type": "status", "status": "generating"}

            async for chunk in client.stream_chat(
                messages=messages,
                temperature=assistant.temperature,
                top_p=assistant.top_p
            ):
                chunk_type = chunk.get("type")
                if chunk_type == "content":
                    full_response_text += chunk["delta"]
                    yield chunk
                elif chunk_type == "reasoning":
                    full_reasoning_text += chunk["delta"]
                    yield chunk
                elif chunk_type == "error":
                    yield chunk
                    return

            # 生成完成，保存到数据库
            # 构建新的 UIMessage 结构
            user_msg = {
                "id": str(uuid.uuid4()),
                "role": "USER",
                "parts": [{"text": user_text}],
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S")
            }
            assistant_msg = {
                "id": str(uuid.uuid4()),
                "role": "ASSISTANT",
                "parts": [],
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S")
            }
            if full_reasoning_text:
                assistant_msg["parts"].append({"reasoning": full_reasoning_text})
            assistant_msg["parts"].append({"text": full_response_text})

            async with db.begin():
                if regenerate_node_id:
                    # 重新生成某一轮：将新回复追加为候选分支，更新 select_index
                    node = await db.get(MessageNodeModel, regenerate_node_id)
                    if node:
                        node.messages.append(assistant_msg)
                        node.select_index = len(node.messages) - 1
                else:
                    # 新的一轮对话：创建用户节点与助手节点
                    user_node = MessageNodeModel(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation_id,
                        node_index=len(all_nodes),
                        messages=[user_msg],
                        select_index=0
                    )
                    db.add(user_node)

                    assistant_node = MessageNodeModel(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation_id,
                        node_index=len(all_nodes) + 1,
                        messages=[assistant_msg],
                        select_index=0
                    )
                    db.add(assistant_node)

                # 更新会话更新时间
                conv = await db.get(ConversationModel, conversation_id)
                if conv:
                    conv.updated_at = int(time.time() * 1000)

            yield {"type": "done", "finish_reason": "stop", "assistant_message_id": assistant_msg["id"]}
