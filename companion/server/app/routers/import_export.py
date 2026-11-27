import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from companion.server.app.database import get_db
from companion.server.app.importer.service import ImportService
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.models.memory import MemoryModel

router = APIRouter(prefix="/backup", tags=["backup"])

class ImportConfirmRequest(BaseModel):
    preview_id: str

@router.post("/preview")
async def preview_backup(file: UploadFile = File(...)):
    """
    上传 RikkaHub 备份包 (ZIP)，生成预览报告，不直接入库
    """
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="必须上传 .zip 格式的备份文件")

    # 存入临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        report = ImportService.generate_preview(tmp_path)
        return report
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析备份文件失败: {str(e)}")
    finally:
        if tmp_path.exists():
            os.remove(tmp_path)

@router.post("/confirm")
async def confirm_import(req: ImportConfirmRequest, db: AsyncSession = Depends(get_db)):
    """
    用户在网页核对预览报告无误后，点击确认，在单个事务内批量落库
    """
    try:
        res = await ImportService.execute_import(req.preview_id, db)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"导入数据失败: {str(e)}")

@router.get("/export")
async def export_backup(db: AsyncSession = Depends(get_db)):
    """
    将当前服务器 SQLite 中的全部角色、会话、分支消息树与记忆打包成标准 RikkaHub 兼容 ZIP 供下载
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="export_"))
    try:
        # 1. 导出 settings.json
        ast_res = await db.execute(select(AssistantModel))
        assistants = ast_res.scalars().all()
        
        settings_assistants = []
        for a in assistants:
            # 优先从 raw_snapshot 中恢复完整数据，并同步更新修改过的核心字段
            data = dict(a.raw_snapshot) if a.raw_snapshot else {}
            data["id"] = a.id
            data["name"] = a.name
            data["systemPrompt"] = a.system_prompt
            data["temperature"] = a.temperature
            data["topP"] = a.top_p
            data["contextMessageLimit"] = a.context_message_limit
            data["enableMemory"] = a.enable_memory
            data["useGlobalMemory"] = a.use_global_memory
            data["enableWebSearch"] = a.enable_web_search
            data["webSearchMode"] = a.web_search_mode
            data["enableActionSync"] = a.enable_action_sync
            data["chatModelId"] = a.model_id
            settings_assistants.append(data)

        settings_json_path = temp_dir / "settings.json"
        with open(settings_json_path, "w", encoding="utf-8") as f:
            json.dump({"assistants": settings_assistants}, f, ensure_ascii=False, indent=2)

        # 2. 导出 rikka_hub.db
        export_db_path = temp_dir / "rikka_hub.db"
        conn = sqlite3.connect(export_db_path)
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
        CREATE TABLE message_node (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            node_index INTEGER,
            messages TEXT,
            select_index INTEGER
        )
        """)
        cur.execute("""
        CREATE TABLE memory_file (
            assistant_id TEXT,
            path TEXT,
            content TEXT,
            updated_at INTEGER,
            PRIMARY KEY (assistant_id, path)
        )
        """)

        conv_res = await db.execute(select(ConversationModel))
        for c in conv_res.scalars().all():
            cur.execute("""
            INSERT INTO ConversationEntity VALUES (?, ?, ?, '[]', ?, ?, '[]', ?, ?)
            """, (c.id, c.assistant_id, c.title, c.created_at, c.updated_at, 1 if c.is_pinned else 0, c.custom_system_prompt))

        node_res = await db.execute(select(MessageNodeModel))
        for n in node_res.scalars().all():
            cur.execute("""
            INSERT INTO message_node VALUES (?, ?, ?, ?, ?)
            """, (n.id, n.conversation_id, n.node_index, json.dumps(n.messages, ensure_ascii=False), n.select_index))

        mem_res = await db.execute(select(MemoryModel))
        mem_map: dict[tuple[str | None, str], list[str]] = {}
        for m in mem_res.scalars().all():
            key = (m.assistant_id, m.path or "default.md")
            if key not in mem_map:
                mem_map[key] = []
            if m.content.strip():
                mem_map[key].append(m.content.strip())

        for (ast_id, path), contents in mem_map.items():
            merged_content = "\n\n".join(contents)
            cur.execute("""
            INSERT OR REPLACE INTO memory_file VALUES (?, ?, ?, ?)
            """, (ast_id, path, merged_content, 1700000200000))

        conn.commit()
        conn.close()

        # 3. 压缩成 ZIP
        zip_output = temp_dir / "rikkahub_backup_export.zip"
        with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(settings_json_path, "settings.json")
            zf.write(export_db_path, "rikka_hub.db")

        return FileResponse(
            zip_output,
            filename="rikkahub_backup_server.zip",
            media_type="application/zip"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出备份失败: {str(e)}")
