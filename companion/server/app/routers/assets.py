"""4D：素材包管理 API（全部需要 App Token）。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from companion.server.app.assetpacks.service import AssetService
from companion.server.app.assetpacks.validator import AssetValidationError
from companion.server.app.config import settings
from companion.server.app.database import get_db
from companion.server.app.security.dependencies import AuthContext, require_app_token

router = APIRouter(prefix="/assets", tags=["assets"])

_UPLOAD_CHUNK_BYTES = 64 * 1024


class AssetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str = Field(min_length=1, max_length=64)


@router.get("")
async def get_assets(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_app_token),
):
    """当前 active / previous 与内置占位图信息。"""
    return await AssetService.view(db)


@router.post("/preview")
async def preview_asset(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_app_token),
):
    """按块写入临时文件并校验；失败只回稳定错误码，不回显包内容。"""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="invalid_archive")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = Path(tmp.name)
    try:
        copied = 0
        while True:
            chunk = file.file.read(_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            copied += len(chunk)
            if copied > settings.ASSET_MAX_ARCHIVE_BYTES:
                raise HTTPException(status_code=413, detail="archive_too_large")
            tmp.write(chunk)
        tmp.close()

        try:
            # ZIP 解压/哈希/写盘是同步 CPU+磁盘活，最多 20 MiB；放到线程里，
            # 避免阻塞同一事件循环上的语音音频帧。
            return await asyncio.to_thread(
                AssetService.preview, tmp_path, auth.token_id
            )
        except AssetValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.code)
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            os.remove(tmp_path)
        except OSError:
            # 取消/停机时工作线程可能还开着这个 zip（Windows 不允许删除），
            # 不能让清理异常覆盖原始取消信号；残留文件由系统临时目录回收。
            pass


@router.post("/confirm")
async def confirm_asset(
    req: AssetConfirmRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_app_token),
):
    try:
        return await AssetService.confirm(req.upload_id, auth.token_id, db)
    except ValueError:
        # 不存在 / 过期 / 属于别的 Token 统一处理，避免探测他人上传。
        raise HTTPException(status_code=400, detail="upload_expired")


@router.post("/rollback")
async def rollback_asset(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_app_token),
):
    try:
        return await AssetService.rollback(db)
    except ValueError:
        raise HTTPException(status_code=409, detail="no_previous_package")


@router.get("/{package_id}/frame/{index}")
async def get_frame(
    package_id: str,
    index: int,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_app_token),
):
    """按整数索引读帧；`package_id` 只用于 DB 查询，绝不拼接路径。"""
    data = await AssetService.frame_bytes(package_id, index, db)
    if data is None:
        raise HTTPException(status_code=404, detail="frame_not_found")
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )
