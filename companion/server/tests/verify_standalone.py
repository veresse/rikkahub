import json
import sqlite3
import zipfile
import unittest
import tempfile
import sys
from pathlib import Path

# 添加项目路径到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

# 直接导入单文件，避免触发 __init__.py 中的 sqlalchemy 依赖
import companion.server.app.importer.validator as validator_module
import companion.server.app.importer.parser as parser_module

validate_and_extract_backup_zip = validator_module.validate_and_extract_backup_zip
BackupParser = parser_module.BackupParser

class TestImporterStandalone(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_backup_parser(self):
        zip_file = self.tmp_path / "test_backup.zip"
        
        # 1. 模拟 settings.json
        settings_data = {
            "assistants": [
                {
                    "id": "c1f7b022-7711-47fb-94f4-5f5c86c12345",
                    "name": "夏以昼",
                    "systemPrompt": "你是夏以昼，温柔兄长，深情陪伴。",
                    "temperature": 0.7,
                    "topP": 0.9,
                    "contextMessageLimit": 20,
                    "enableMemory": True,
                    "useGlobalMemory": False,
                    "enableWebSearch": True,
                    "chatModelId": "deepseek-chat",
                    "mcpServers": ["mock-mcp-uuid"]
                }
            ]
        }
        
        # 2. 模拟 rikka_hub.db
        db_file = self.tmp_path / "rikka_hub.db"
        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        
        cur.execute("""
        CREATE TABLE ConversationEntity (
            id TEXT PRIMARY KEY,
            assistant_id TEXT,
            title TEXT,
            nodes TEXT,
            create_at INTEGER,
            update_at INTEGER,
            suggestions TEXT,
            is_pinned INTEGER,
            custom_system_prompt TEXT
        )
        """)
        cur.execute("""
        INSERT INTO ConversationEntity VALUES (
            'conv-001', 'c1f7b022-7711-47fb-94f4-5f5c86c12345', '与夏以昼的初次谈话', '[]',
            1700000000000, 1700000100000, '[]', 1, ''
        )
        """)
        
        cur.execute("""
        CREATE TABLE message_node (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            node_index INTEGER,
            messages TEXT,
            select_index INTEGER
        )
        """)
        
        node_messages = [
            {
                "id": "msg-001",
                "role": "USER",
                "parts": [{"text": "哥，今天有点累。"}],
                "createdAt": "2026-11-15T12:00:00"
            },
            {
                "id": "msg-002-v1",
                "role": "ASSISTANT",
                "parts": [{"text": "辛苦了，先喝口水休息一下吧。"}],
                "createdAt": "2026-11-15T12:00:05"
            },
            {
                "id": "msg-002-v2",
                "role": "ASSISTANT",
                "parts": [{"text": "傻瓜，工作再忙也要顾好自己，快坐下，我给你剥个橘子。"}],
                "createdAt": "2026-11-15T12:00:10"
            }
        ]
        cur.execute("""
        INSERT INTO message_node VALUES (
            'node-001', 'conv-001', 0, ?, 1
        )
        """, (json.dumps(node_messages),))
        
        cur.execute("""
        CREATE TABLE memory_file (
            assistant_id TEXT,
            path TEXT,
            content TEXT,
            updated_at INTEGER,
            PRIMARY KEY (assistant_id, path)
        )
        """)
        cur.execute("""
        INSERT INTO memory_file VALUES (
            'c1f7b022-7711-47fb-94f4-5f5c86c12345', 'default.md', '- 喜欢吃甜食\n- 晚上容易失眠', 1700000200000
        )
        """)
        conn.commit()
        conn.close()
        
        with zipfile.ZipFile(zip_file, "w") as zf:
            zf.writestr("settings.json", json.dumps(settings_data))
            zf.write(db_file, "rikka_hub.db")
            
        # 验证解压与解析
        extract_dir = self.tmp_path / "extracted"
        extracted = validate_and_extract_backup_zip(zip_file, extract_dir)
        self.assertTrue(extracted["settings_path"].exists())
        self.assertTrue(extracted["db_path"].exists())
        
        parser = BackupParser(extracted["settings_path"], extracted["db_path"])
        assistants, unsupported = parser.parse_settings()
        self.assertEqual(len(assistants), 1)
        self.assertEqual(assistants[0]["name"], "夏以昼")
        self.assertEqual(assistants[0]["system_prompt"], "你是夏以昼，温柔兄长，深情陪伴。")
        self.assertTrue("MCP 外部工具扩展" in unsupported[0])
        
        convs, nodes, memories = parser.parse_database()
        self.assertEqual(len(convs), 1)
        self.assertEqual(convs[0]["title"], "与夏以昼的初次谈话")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["select_index"], 1)
        self.assertEqual(len(nodes[0]["messages"]), 3)
        self.assertEqual(len(memories), 1)
        self.assertTrue("失眠" in memories[0]["content"])
        print("\n[OK] 备份文件解压、角色人设提取、树状分支消息还原、记忆映射全部通过验证！")

if __name__ == "__main__":
    unittest.main()
