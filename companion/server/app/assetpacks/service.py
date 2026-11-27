"""素材包暂存、启用、回滚与帧读取。

服务端按单进程部署（设计 §2.5），用模块级 [asyncio.Lock] 串行化
confirm/rollback，保证任意时刻最多一条 active 与一条 previous。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.server.app.assetpacks.validator import (
    AssetValidationError,
    validate_asset_zip,
)
from companion.server.app.config import settings
from companion.server.app.models.asset_package import AssetPackageModel

PENDING_TTL_SECONDS = 600
MAX_PENDING = 4

# 未确认的上传：{upload_id: {"dir": Path, "meta": dict, "owner": str, "expires_at": float}}
_PENDING: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()
# `preview()` 会在线程里跑（ZIP 解压），`confirm()` 在事件循环里跑；两者都要
# 改 `_PENDING`，用线程锁串行化，避免迭代时字典被改。
_pending_lock = threading.RLock()

_PLACEHOLDER_PATH = Path(__file__).resolve().parent / "placeholder_240.png"


class AssetService:
    @staticmethod
    def _assets_root() -> Path:
        return settings.DATA_DIR / "assets"

    @staticmethod
    def placeholder_bytes() -> bytes:
        return _PLACEHOLDER_PATH.read_bytes()

    @staticmethod
    def _prune() -> list[Path]:
        """移除过期/超额条目，返回待删除目录（调用方须持有 `_pending_lock`）。

        目录删除放到锁外、事件循环外（`asyncio.to_thread`）执行：Windows 上
        删一个包含上千帧的包可能耗时几十毫秒，不能在持锁或事件循环线程里做，
        否则会卡住同进程的语音音频帧。
        """
        stale: list[Path] = []
        now = time.time()
        for key in [k for k, v in _PENDING.items() if v["expires_at"] <= now]:
            stale.append(_PENDING.pop(key)["dir"])
        # 按插入顺序淘汰最旧的条目（dict 保序）：同秒创建的上传 TTL 相同，
        # 按 expires_at 排序会在并列时误删刚插入的最新一条。
        while len(_PENDING) > MAX_PENDING:
            key = next(iter(_PENDING))
            stale.append(_PENDING.pop(key)["dir"])
        return stale

    @staticmethod
    async def _remove_dirs(dirs: list[Path]) -> None:
        for path in dirs:
            await asyncio.to_thread(shutil.rmtree, path, True)

    @staticmethod
    def reset_pending() -> None:
        """仅测试使用：丢弃所有未确认上传及其临时目录。"""
        with _pending_lock:
            for entry in _PENDING.values():
                shutil.rmtree(entry["dir"], ignore_errors=True)
            _PENDING.clear()

    @staticmethod
    def preview(zip_path: Path, owner_token_id: str) -> dict[str, Any]:
        """校验并暂存上传；成功返回预览元数据，失败抛 [AssetValidationError]。"""
        temp_dir = Path(tempfile.mkdtemp(prefix="asset_upload_"))
        try:
            meta = validate_asset_zip(zip_path, temp_dir)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

        upload_id = str(uuid.uuid4())
        with _pending_lock:
            _PENDING[upload_id] = {
                "dir": temp_dir,
                "meta": meta,
                "owner": owner_token_id,
                "expires_at": time.time() + PENDING_TTL_SECONDS,
            }
            # 先插入再裁剪：否则同秒并列的 TTL 可能把刚插入的条目当作“最旧”删掉。
            stale = AssetService._prune()
        # preview 本身在线程池里跑，直接删；不能把删除留在锁内。
        for path in stale:
            shutil.rmtree(path, ignore_errors=True)
        return {
            "upload_id": upload_id,
            "name": meta["name"],
            "version": meta["version"],
            "fps": meta["fps"],
            "frame_count": meta["frame_count"],
            "size_bytes": meta["size_bytes"],
            "sha256": meta["sha256"],
        }

    @staticmethod
    async def confirm(
        upload_id: str,
        owner_token_id: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """把暂存包移入正式目录并在单个事务里切换 active / previous。"""
        expired_dir: Path | None = None
        stale_dirs: list[str] = []
        # 待删目录：正常路径的旧包 + 失败路径的临时包。锁内只登记，锁外删除，
        # 避免并发 confirm/rollback 在 `_lock` 上等 rmtree。
        stale: list[Path] = []
        failed: list[Path] = []
        root = AssetService._assets_root()
        result: dict[str, Any] | None = None
        try:
            async with _lock:
                with _pending_lock:
                    stale = AssetService._prune()
                    entry = _PENDING.get(upload_id)
                    if entry is None:
                        expired = True
                    elif entry["expires_at"] <= time.time():
                        _PENDING.pop(upload_id, None)
                        expired_dir = entry["dir"]
                        entry = None
                        expired = True
                    elif entry["owner"] != owner_token_id:
                        # 他人的暂存包不在这里作废（否则一个 Token 能删掉另一个
                        # Token 的未确认上传）；过期后由 _prune 统一清理。
                        expired = True
                    else:
                        _PENDING.pop(upload_id, None)
                        expired = False

                if expired:
                    # 只删自己的过期包；他人的包留给 _prune。
                    if expired_dir is not None:
                        stale.append(expired_dir)
                else:
                    assert entry is not None
                    # pop 之后任何失败都要由 `failed` 负责清理暂存目录；成功移走
                    # 后源路径已不存在，rmtree 是空操作。
                    staged = entry["dir"]
                    failed.append(staged)
                    meta: dict[str, Any] = entry["meta"]
                    root.mkdir(parents=True, exist_ok=True)
                    package_id = str(uuid.uuid4())
                    dest = root / package_id
                    try:
                        shutil.move(str(staged), str(dest))
                    except OSError:
                        # 跨盘时会退化成复制：中途失败可能留下半个正式目录。
                        failed.append(dest)
                        raise

                    now_ms = int(time.time() * 1000)
                    try:
                        rows = (
                            await db.execute(select(AssetPackageModel))
                        ).scalars().all()
                        for row in rows:
                            if row.state == "active":
                                row.state = "previous"
                            elif row.state == "previous":
                                stale_dirs.append(row.storage_path)
                                await db.delete(row)
                        db.add(
                            AssetPackageModel(
                                id=package_id,
                                name=meta["name"],
                                version=meta["version"],
                                fps=meta["fps"],
                                frame_count=meta["frame_count"],
                                size_bytes=meta["size_bytes"],
                                sha256=meta["sha256"],
                                storage_path=package_id,
                                state="active",
                                created_at=now_ms,
                                activated_at=now_ms,
                            )
                        )
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        # 暂存目录已移走，失败时要删的是新正式目录；`staged`
                        # 也在 `failed` 里，路径不存在时 rmtree 是空操作。
                        failed.append(dest)
                        raise

                    stale.extend(root / name for name in stale_dirs)
                    result = await AssetService.view(db)
        finally:
            # 锁外清理：只覆盖已登记目录；rmtree 幂等，重复项无害。
            await AssetService._remove_dirs([*failed, *stale])

        if result is None:
            raise ValueError("upload_expired")
        return result

    @staticmethod
    async def rollback(db: AsyncSession) -> dict[str, Any]:
        """删除当前 active，把 previous 升回 active；没有上一版时 409。"""
        root = AssetService._assets_root()
        async with _lock:
            rows = (await db.execute(select(AssetPackageModel))).scalars().all()
            actives = [row for row in rows if row.state == "active"]
            previous = [row for row in rows if row.state == "previous"]
            if not previous:
                raise ValueError("no_previous_package")

            previous.sort(key=lambda row: row.activated_at, reverse=True)
            keep = previous[0]
            stale_dirs = [row.storage_path for row in actives]
            for row in actives:
                await db.delete(row)
            for row in previous[1:]:
                stale_dirs.append(row.storage_path)
                await db.delete(row)
            keep.state = "active"
            keep.activated_at = int(time.time() * 1000)
            await db.commit()

            result = await AssetService.view(db)

        await AssetService._remove_dirs([root / name for name in stale_dirs])
        return result

    @staticmethod
    async def view(db: AsyncSession) -> dict[str, Any]:
        rows = (await db.execute(select(AssetPackageModel))).scalars().all()
        active = next((row for row in rows if row.state == "active"), None)
        previous = next((row for row in rows if row.state == "previous"), None)
        return {
            "active": _serialize(active),
            "previous": _serialize(previous),
            "fallback": {"name": "占位图", "version": "builtin", "frame_count": 1},
        }

    @staticmethod
    async def frame_bytes(
        package_id: str,
        index: int,
        db: AsyncSession,
    ) -> bytes | None:
        """按 DB 记录读取帧；`fallback` 返回内置占位图，越界返回 None。"""
        if index < 0:
            return None
        if package_id == "fallback":
            return AssetService.placeholder_bytes() if index == 0 else None

        row = await db.get(AssetPackageModel, package_id)
        if row is None or not 0 <= index < row.frame_count:
            return None
        path = (
            AssetService._assets_root()
            / row.storage_path
            / "frames"
            / f"{index:03d}.png"
        )
        if not path.is_file():
            return None
        return path.read_bytes()


def _serialize(row: AssetPackageModel | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "fps": row.fps,
        "frame_count": row.frame_count,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "created_at": row.created_at,
        "activated_at": row.activated_at,
    }


__all__ = ["AssetService", "AssetValidationError"]
