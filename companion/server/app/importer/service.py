import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from companion.server.app.importer.validator import validate_and_extract_backup_zip
from companion.server.app.importer.parser import BackupParser
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.models.memory import MemoryModel

# 内存暂存区，用于存放预检结果，用户确认后再写入数据库
_PREVIEW_CACHE: dict[str, dict[str, Any]] = {}

class ImportService:
    @staticmethod
    def generate_preview(zip_path: Path) -> dict[str, Any]:
        """
        阶段 1：解析 ZIP 包生成导入预览报告
        """
        temp_dir = Path(tempfile.mkdtemp(prefix="rikka_import_"))
        try:
            extracted = validate_and_extract_backup_zip(zip_path, temp_dir)
            parser = BackupParser(extracted.get("settings_path"), extracted.get("db_path"))

            assistants, unsupported_features = parser.parse_settings()
            conversations, message_nodes, memories = parser.parse_database()

            # 统计消息候选分支总数
            total_alternatives = sum(len(node.get("messages", [])) for node in message_nodes)

            preview_id = str(uuid.uuid4())
            preview_data = {
                "preview_id": preview_id,
                "summary": {
                    "assistant_count": len(assistants),
                    "conversation_count": len(conversations),
                    "message_node_count": len(message_nodes),
                    "total_message_versions": total_alternatives,
                    "memory_count": len(memories),
                    "unsupported_features": unsupported_features
                },
                "parsed_data": {
                    "assistants": assistants,
                    "conversations": conversations,
                    "message_nodes": message_nodes,
                    "memories": memories
                }
            }

            _PREVIEW_CACHE[preview_id] = preview_data
            return {
                "preview_id": preview_id,
                "summary": preview_data["summary"],
                "assistants_sample": [a["name"] for a in assistants[:5]],
                "conversations_sample": [c["title"] for c in conversations[:5]]
            }
        finally:
            # 清理临时解压出来的文件
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    async def execute_import(preview_id: str, db: AsyncSession) -> dict[str, Any]:
        """
        阶段 2：用户确认后在单个数据库事务中导入所有数据
        """
        if preview_id not in _PREVIEW_CACHE:
            raise ValueError("无效或已过期的导入预览 ID，请重新上传备份")

        data = _PREVIEW_CACHE.pop(preview_id)["parsed_data"]
        assistants_data = data["assistants"]
        conversations_data = data["conversations"]
        nodes_data = data["message_nodes"]
        memories_data = data["memories"]

        imported_assistants = 0
        imported_conversations = 0
        imported_nodes = 0
        imported_memories = 0

        async with db.begin():
            # 1. 批量导入/更新助手
            for a in assistants_data:
                existing = await db.get(AssistantModel, a["id"])
                if existing:
                    existing.name = a["name"]
                    existing.avatar = a["avatar"]
                    existing.system_prompt = a["system_prompt"]
                    existing.temperature = a["temperature"]
                    existing.top_p = a["top_p"]
                    existing.context_message_limit = a["context_message_limit"]
                    existing.enable_memory = a["enable_memory"]
                    existing.use_global_memory = a["use_global_memory"]
                    existing.enable_web_search = a["enable_web_search"]
                    existing.web_search_mode = a["web_search_mode"]
                    existing.enable_action_sync = a["enable_action_sync"]
                    existing.model_id = a["model_id"]
                    existing.raw_snapshot = a["raw_snapshot"]
                else:
                    db.add(AssistantModel(**a))
                imported_assistants += 1

            # 2. 批量导入/更新会话
            for c in conversations_data:
                existing_c = await db.get(ConversationModel, c["id"])
                if existing_c:
                    existing_c.title = c["title"]
                    existing_c.is_pinned = c["is_pinned"]
                    existing_c.custom_system_prompt = c["custom_system_prompt"]
                    existing_c.updated_at = c["updated_at"]
                    existing_c.raw_config = c["raw_config"]
                else:
                    # 确保 assistant_id 合法，避免外键约束失败
                    ast_id = c["assistant_id"]
                    ast_exists = await db.get(AssistantModel, ast_id)
                    if not ast_exists and assistants_data:
                        ast_id = assistants_data[0]["id"]
                    c["assistant_id"] = ast_id
                    db.add(ConversationModel(**c))
                imported_conversations += 1

            # 3. 批量导入消息分支节点
            for n in nodes_data:
                existing_n = await db.get(MessageNodeModel, n["id"])
                if existing_n:
                    existing_n.node_index = n["node_index"]
                    existing_n.messages = n["messages"]
                    existing_n.select_index = n["select_index"]
                else:
                    db.add(MessageNodeModel(**n))
                imported_nodes += 1

            # 4. 批量导入记忆
            for m in memories_data:
                db.add(MemoryModel(**m))
                imported_memories += 1

        return {
            "status": "success",
            "imported_assistants": imported_assistants,
            "imported_conversations": imported_conversations,
            "imported_nodes": imported_nodes,
            "imported_memories": imported_memories
        }
