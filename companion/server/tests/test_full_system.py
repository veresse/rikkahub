import json
import os
import sqlite3
import tempfile
import zipfile
import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport

# 设置测试环境配置
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="test_data_")
from companion.server.app.main import app
from companion.server.app.database import init_db

@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    # 确保测试启动前建立 SQLite 表
    import asyncio
    asyncio.run(init_db())

@pytest.fixture
def mock_backup_zip(tmp_path: Path) -> Path:
    """生成真实的 RikkaHub 备份 ZIP 文件"""
    zip_path = tmp_path / "mock_rikkahub_backup.zip"
    
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
                "chatModelId": "deepseek-chat"
            }
        ]
    }
    
    db_path = tmp_path / "rikka_hub.db"
    conn = sqlite3.connect(db_path)
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
        'conv-test-101', 'c1f7b022-7711-47fb-94f4-5f5c86c12345', '与夏以昼的初次谈话', '[]',
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
        {"id": "m1", "role": "USER", "parts": [{"text": "哥，今天有点累。"}], "createdAt": "2026-11-15T12:00:00"},
        {"id": "m2_v1", "role": "ASSISTANT", "parts": [{"text": "辛苦了，先喝口水休息一下吧。"}], "createdAt": "2026-11-15T12:00:05"},
        {"id": "m2_v2", "role": "ASSISTANT", "parts": [{"text": "傻瓜，快坐下，我给你剥个橘子。"}], "createdAt": "2026-11-15T12:00:10"}
    ]
    cur.execute("""
    INSERT INTO message_node VALUES ('node-test-101', 'conv-test-101', 0, ?, 1)
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
    INSERT INTO memory_file VALUES ('c1f7b022-7711-47fb-94f4-5f5c86c12345', 'default.md', '- 喜欢吃甜食\n- 晚上容易失眠', 1700000200000)
    """)
    conn.commit()
    conn.close()

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("settings.json", json.dumps(settings_data))
        zf.write(db_path, "rikka_hub.db")
        
    return zip_path

@pytest.mark.asyncio
async def test_full_service_pipeline(mock_backup_zip: Path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. 验证健康检查（公开）
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

        # 2. 管理员登录，后续管理接口统一携带 Bearer Token
        login_resp = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-admin-password", "device_name": "pytest"},
        )
        assert login_resp.status_code == 200
        headers = {"Authorization": f"Bearer {login_resp.json()['token']}"}

        # 3. 验证备份上传与预览
        with open(mock_backup_zip, "rb") as f:
            preview_resp = await client.post(
                "/api/backup/preview",
                files={"file": ("backup.zip", f, "application/zip")},
                headers=headers,
            )
        assert preview_resp.status_code == 200
        preview_data = preview_resp.json()
        assert preview_data["summary"]["assistant_count"] == 1
        assert preview_data["summary"]["conversation_count"] == 1
        assert preview_data["summary"]["total_message_versions"] == 3
        preview_id = preview_data["preview_id"]

        # 4. 验证确认入库
        confirm_resp = await client.post(
            "/api/backup/confirm",
            json={"preview_id": preview_id},
            headers=headers,
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["status"] == "success"

        # 5. 验证角色查询
        ast_resp = await client.get("/api/assistants", headers=headers)
        assert ast_resp.status_code == 200
        assistants = ast_resp.json()
        assert any(a["name"] == "夏以昼" for a in assistants)
        # v1 备份未携带 enableActionSync，升级后默认开启动作联动
        assert all(a["enable_action_sync"] is True for a in assistants)

        # 6. 验证会话与多候选分支树获取
        conv_resp = await client.get("/api/conversations", headers=headers)
        assert conv_resp.status_code == 200
        convs = conv_resp.json()
        assert len(convs) >= 1
        conv_id = convs[0]["id"]

        detail_resp = await client.get(f"/api/conversations/{conv_id}", headers=headers)
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert len(detail["nodes"]) >= 1
        assert detail["nodes"][0]["select_index"] == 1
        assert detail["nodes"][0]["total_branches"] == 3

        # 7. 验证切换分支版本
        select_resp = await client.post(
            f"/api/conversations/{conv_id}/nodes/{detail['nodes'][0]['id']}/select?index=0",
            headers=headers,
        )
        assert select_resp.status_code == 200
        assert select_resp.json()["select_index"] == 0

        # 8. 验证长期记忆查询与新增
        mem_resp = await client.get("/api/memories", headers=headers)
        assert mem_resp.status_code == 200
        memories = mem_resp.json()
        assert len(memories) >= 1
        assert "失眠" in memories[0]["content"]

        add_mem_resp = await client.post(
            "/api/memories",
            json={"assistant_id": "c1f7b022-7711-47fb-94f4-5f5c86c12345", "content": "平时喜欢摄影"},
            headers=headers,
        )
        assert add_mem_resp.status_code == 200
        assert add_mem_resp.json()["status"] == "created"

        # 9. 验证实体挂件配对码生成
        code_resp = await client.post("/api/devices/pair-code", json={}, headers=headers)
        assert code_resp.status_code == 200
        code_data = code_resp.json()
        assert len(code_data["code"]) == 6
        assert code_data["expires_in"] == 300

        # 10. 验证全量数据导出为标准 RikkaHub ZIP
        export_resp = await client.get("/api/backup/export", headers=headers)
        if export_resp.status_code != 200:
            print("EXPORT FAILED DETAIL:", export_resp.text)
        assert export_resp.status_code == 200
        assert export_resp.headers["content-type"] == "application/zip"
        assert len(export_resp.content) > 0
