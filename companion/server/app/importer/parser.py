import json
import sqlite3
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

class BackupParser:
    def __init__(self, settings_path: Path | None, db_path: Path | None):
        self.settings_path = settings_path
        self.db_path = db_path

    def parse_settings(self) -> tuple[list[dict[str, Any]], list[str]]:
        """
        解析 settings.json 中的助手列表与不支持的高级特性
        """
        assistants = []
        unsupported_features = []

        if not self.settings_path or not self.settings_path.exists():
            return assistants, unsupported_features

        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"解析 settings.json 失败: {e}")
            raise ValueError(f"settings.json 格式损坏: {e}")

        raw_assistants = data.get("assistants", [])
        for raw in raw_assistants:
            # 检查高级未支持特性以在报告中提示
            unsupported = []
            if raw.get("mcpServers"):
                unsupported.append("MCP 外部工具扩展")
            if raw.get("lorebookIds"):
                unsupported.append("Lorebook 世界书")
            if raw.get("modeInjectionIds"):
                unsupported.append("模式提示词注入")
            if raw.get("regexes"):
                unsupported.append("正则输出替换规则")

            if unsupported:
                unsupported_features.append(f"角色「{raw.get('name', '未命名')}」包含暂不支持项: {', '.join(unsupported)}")

            assistant = {
                "id": str(raw.get("id")),
                "name": raw.get("name", "未命名角色"),
                "avatar": raw.get("avatar", {}),
                "system_prompt": raw.get("systemPrompt", ""),
                "temperature": raw.get("temperature"),
                "top_p": raw.get("topP"),
                "context_message_limit": raw.get("contextMessageLimit", 0),
                "enable_memory": bool(raw.get("enableMemory", False)),
                "use_global_memory": bool(raw.get("useGlobalMemory", False)),
                "enable_web_search": bool(raw.get("enableWebSearch", False)),
                "model_id": str(raw.get("chatModelId")) if raw.get("chatModelId") else None,
                "raw_snapshot": raw  # 保存完整原始 JSON，避免信息丢失
            }
            assistants.append(assistant)

        return assistants, unsupported_features

    def parse_database(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """
        从 rikka_hub.db 解析会话、消息树节点和长期记忆文件
        """
        conversations = []
        message_nodes = []
        memories = []

        if not self.db_path or not self.db_path.exists():
            return conversations, message_nodes, memories

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            # 1. 查找 ConversationEntity 或 conversation 表
            conv_tables = ["ConversationEntity", "conversation", "conversations"]
            found_conv_table = None
            for tbl in conv_tables:
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{tbl}'")
                if cursor.fetchone():
                    found_conv_table = tbl
                    break

            if found_conv_table:
                cursor.execute(f"SELECT * FROM {found_conv_table}")
                rows = cursor.fetchall()
                for row in rows:
                    row_dict = dict(row)
                    conversations.append({
                        "id": str(row_dict.get("id")),
                        "assistant_id": str(row_dict.get("assistant_id")),
                        "title": row_dict.get("title") or "未命名对话",
                        "is_pinned": bool(row_dict.get("is_pinned", 0)),
                        "custom_system_prompt": row_dict.get("custom_system_prompt") or "",
                        "created_at": row_dict.get("create_at") or 0,
                        "updated_at": row_dict.get("update_at") or 0,
                        "raw_config": row_dict
                    })

            # 2. 查找 message_node 表
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='message_node'")
            if cursor.fetchone():
                cursor.execute("SELECT * FROM message_node ORDER BY conversation_id, node_index ASC")
                rows = cursor.fetchall()
                for row in rows:
                    row_dict = dict(row)
                    raw_messages = row_dict.get("messages", "[]")
                    try:
                        parsed_messages = json.loads(raw_messages) if isinstance(raw_messages, str) else raw_messages
                    except Exception:
                        parsed_messages = []

                    message_nodes.append({
                        "id": str(row_dict.get("id")),
                        "conversation_id": str(row_dict.get("conversation_id")),
                        "node_index": int(row_dict.get("node_index", 0)),
                        "messages": parsed_messages,
                        "select_index": int(row_dict.get("select_index", 0)),
                    })

            # 3. 查找 memory_file 表
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_file'")
            if cursor.fetchone():
                cursor.execute("SELECT * FROM memory_file")
                rows = cursor.fetchall()
                for row in rows:
                    row_dict = dict(row)
                    memories.append({
                        "assistant_id": str(row_dict.get("assistant_id")) if row_dict.get("assistant_id") else None,
                        "path": row_dict.get("path", "default.md"),
                        "content": row_dict.get("content", ""),
                        "updated_at": row_dict.get("updated_at", 0)
                    })

        finally:
            conn.close()

        return conversations, message_nodes, memories
