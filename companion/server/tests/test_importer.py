import json
import sqlite3
import zipfile
import pytest
import tempfile
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from companion.server.app.database import Base
from companion.server.app.importer.validator import validate_and_extract_backup_zip, ValidationError
from companion.server.app.importer.parser import BackupParser
from companion.server.app.importer.service import ImportService
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.models.memory import MemoryModel

@pytest.fixture
def sample_backup_zip(tmp_path: Path) -> Path:
    """创建一个符合 RikkaHub 实际规范的模拟备份包"""
    zip_file = tmp_path / "rikkahub_backup_test.zip"
    
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
                "mcpServers": ["some-mcp-uuid"]  # 包含高级未支持项
            }
        ]
    }
    
    # 2. 模拟 rikka_hub.db
    db_file = tmp_path / "rikka_hub.db"
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
    
    # 模拟树状多分支节点：用户问候，助手重试过一次（有两条不同回答，当前选中第2条即 index 1）
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
        'node-001', 'conv-001', 0, ?, 2
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
    
    # 打包成 ZIP
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("settings.json", json.dumps(settings_data))
        zf.write(db_file, "rikka_hub.db")
        
    return zip_file

def test_validator_and_parser(sample_backup_zip: Path, tmp_path: Path):
    extract_dir = tmp_path / "extracted"
    extracted = validate_and_extract_backup_zip(sample_backup_zip, extract_dir)
    
    assert extracted["settings_path"].exists()
    assert extracted["db_path"].exists()
    
    parser = BackupParser(extracted["settings_path"], extracted["db_path"])
    assistants, unsupported = parser.parse_settings()
    assert len(assistants) == 1
    assert assistants[0]["name"] == "夏以昼"
    assert assistants[0]["system_prompt"] == "你是夏以昼，温柔兄长，深情陪伴。"
    assert len(unsupported) == 1
    assert "MCP 外部工具扩展" in unsupported[0]
    
    convs, nodes, memories = parser.parse_database()
    assert len(convs) == 1
    assert convs[0]["title"] == "与夏以昼的初次谈话"
    assert len(nodes) == 1
    assert nodes[0]["select_index"] == 2
    assert len(nodes[0]["messages"]) == 3
    assert len(memories) == 1
    assert "失眠" in memories[0]["content"]

@pytest.mark.asyncio
async def test_import_service(sample_backup_zip: Path, tmp_path: Path):
    # 测试预览报告生成
    preview = ImportService.generate_preview(sample_backup_zip)
    assert preview["summary"]["assistant_count"] == 1
    assert preview["summary"]["conversation_count"] == 1
    assert preview["summary"]["message_node_count"] == 1
    assert preview["summary"]["total_message_versions"] == 3
    assert preview["summary"]["memory_count"] == 1
    
    # 测试异步数据库全量事务导入
    test_db_path = tmp_path / "test_companion.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path.as_posix()}")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await ImportService.execute_import(preview["preview_id"], session)
        assert result["status"] == "success"
        assert result["imported_assistants"] == 1
        assert result["imported_conversations"] == 1
        assert result["imported_nodes"] == 1
        assert result["imported_memories"] == 1
        
        # 查库验证
        ast = await session.get(AssistantModel, "c1f7b022-7711-47fb-94f4-5f5c86c12345")
        assert ast is not None
        assert ast.name == "夏以昼"
        
        node = await session.get(MessageNodeModel, "node-001")
        assert node is not None
        assert node.select_index == 2
        assert len(node.messages) == 3


def _make_settings_backup(path: Path, **assistant_fields) -> Path:
    assistant = {"id": "asst-web", "name": "角色", **assistant_fields}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("settings.json", json.dumps({"assistants": [assistant]}))
    return path


@pytest.mark.asyncio
async def test_web_search_mode_and_model_survive_import(tmp_path: Path):
    """新导出的 webSearchMode=on 不能被回退成 auto，已存在角色的 model_id 也要更新。"""
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'web-mode.db').as_posix()}"
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        first = ImportService.generate_preview(
            _make_settings_backup(
                tmp_path / "first.zip",
                enableWebSearch=False,
                webSearchMode="on",
                chatModelId="deepseek-reasoner",
            )
        )
        await ImportService.execute_import(first["preview_id"], session)

    async with async_session() as session:
        assistant = await session.get(AssistantModel, "asst-web")
        assert assistant is not None
        assert assistant.web_search_mode == "on"
        assert assistant.enable_web_search is True
        assert assistant.model_id == "deepseek-reasoner"

    second = ImportService.generate_preview(
        _make_settings_backup(
            tmp_path / "second.zip",
            webSearchMode="auto",
            chatModelId="deepseek-chat",
        )
    )
    async with async_session() as session:
        await ImportService.execute_import(second["preview_id"], session)

    async with async_session() as session:
        assistant = await session.get(AssistantModel, "asst-web")
        assert assistant.web_search_mode == "auto"
        assert assistant.model_id == "deepseek-chat"
