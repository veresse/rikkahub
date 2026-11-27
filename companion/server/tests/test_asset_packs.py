"""4D：素材包校验、启用、回滚与帧读取。"""

from __future__ import annotations

import asyncio
import json
import shutil
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from sqlalchemy import delete

from companion.server.app.assetpacks import service as asset_service
from companion.server.app.assetpacks.service import AssetService
from companion.server.app.assetpacks.validator import AssetValidationError
from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.asset_package import AssetPackageModel


def _png(width: int = 240, height: int = 240, bit: int = 8, color: int = 6) -> bytes:
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, bit, color, 0, 0, 0)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 1))
        + chunk(b"IEND", b"")
    )


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _manifest(frames: int, *, version: str = "1.0.0", fps: int = 12) -> str:
    return json.dumps(
        {
            "name": "Q版陪伴",
            "version": version,
            "fps": fps,
            "frames": [f"frames/{index:03d}.png" for index in range(frames)],
        },
        ensure_ascii=False,
    )


def _package(tmp_path, frames: int = 2, name: str = "pkg.zip", **kwargs) -> "object":
    entries = {"manifest.json": _manifest(frames, **kwargs).encode("utf-8")}
    for index in range(frames):
        entries[f"frames/{index:03d}.png"] = _png()
    path = tmp_path / name
    path.write_bytes(_zip_bytes(entries))
    return path


def test_malformed_manifest_frame_element_is_rejected(tmp_path):
    # 不能 `set(frames)` 先炸 TypeError：不可哈希元素必须收敛成 400。
    entries = {
        "manifest.json": json.dumps(
            {"name": "x", "version": "1.0.0", "fps": 12, "frames": [{}]}
        ).encode("utf-8"),
    }
    with pytest.raises(AssetValidationError) as exc:
        AssetService.preview(_write(tmp_path / "bad-manifest.zip", entries), "owner")
    assert exc.value.code == "invalid_manifest"


def test_corrupted_deflate_is_invalid_archive(tmp_path):
    """结构合法但 deflate 数据损坏：zlib.error 不能漏成 500。"""
    path = tmp_path / "corrupt.zip"
    entries = {"manifest.json": _manifest(1).encode("utf-8"), "frames/000.png": _png()}
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    raw = bytearray(buf.getvalue())
    path.write_bytes(bytes(raw))

    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo("frames/000.png")
        header = info.header_offset
        name_len = struct.unpack("<H", raw[header + 26 : header + 28])[0]
        extra_len = struct.unpack("<H", raw[header + 28 : header + 30])[0]
        start = header + 30 + name_len + extra_len
        for index in range(start, start + info.compress_size):
            raw[index] ^= 0xFF
    path.write_bytes(bytes(raw))

    with pytest.raises(AssetValidationError) as exc:
        AssetService.preview(path, "owner")
    assert exc.value.code == "invalid_archive"


async def test_pending_cache_caps_and_keeps_newest(db, tmp_path, monkeypatch):
    monkeypatch.setattr(asset_service, "MAX_PENDING", 2)
    # 固定时钟：真实 clock 是微秒级 float，三次不会并列，旧实现按
    # expires_at 排序也能“碰巧”通过。固定后同秒并列，旧实现会淘汰最新一条。
    # 取值须晚于 1980（zipfile 用 time.localtime 写条目时间戳）。
    monkeypatch.setattr(asset_service.time, "time", lambda: 1_700_000_000.0)
    first = AssetService.preview(_package(tmp_path, name="a.zip"), "owner")
    second = AssetService.preview(_package(tmp_path, name="b.zip"), "owner")
    third = AssetService.preview(_package(tmp_path, name="c.zip"), "owner")

    assert len(asset_service._PENDING) == 2
    assert first["upload_id"] not in asset_service._PENDING
    assert set(asset_service._PENDING) == {second["upload_id"], third["upload_id"]}

    # 最新上传必须还能确认（同秒并列 TTL 时不能误淘汰刚插入的条目）。
    view = await AssetService.confirm(third["upload_id"], "owner", db)
    assert view["active"] is not None


@pytest.fixture(autouse=True)
def _isolated_asset_data(tmp_path, monkeypatch):
    """素材目录指向临时目录；行清理由下面依赖它的 async fixture 负责。"""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    AssetService.reset_pending()
    yield
    AssetService.reset_pending()


@pytest.fixture(autouse=True)
async def _clean_asset_rows(_isolated_asset_data):
    async def wipe() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(AssetPackageModel))
            await session.commit()

    await wipe()
    yield
    await wipe()
    shutil.rmtree(settings.DATA_DIR / "assets", ignore_errors=True)


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def test_valid_package_preview_and_confirm(db, tmp_path):
    preview = AssetService.preview(_package(tmp_path), "owner-a")
    assert preview["version"] == "1.0.0"
    assert preview["frame_count"] == 2
    assert len(preview["sha256"]) == 64

    view = await AssetService.confirm(preview["upload_id"], "owner-a", db)
    assert view["active"]["name"] == "Q版陪伴"
    assert view["active"]["frame_count"] == 2
    assert view["previous"] is None
    assert view["fallback"]["version"] == "builtin"

    active_id = view["active"]["id"]
    first = await AssetService.frame_bytes(active_id, 0, db)
    second = await AssetService.frame_bytes(active_id, 1, db)
    assert first is not None and first.startswith(b"\x89PNG")
    assert second is not None and second.startswith(b"\x89PNG")
    assert (settings.DATA_DIR / "assets" / active_id / "frames" / "001.png").is_file()
    assert (settings.DATA_DIR / "assets" / active_id / "integrity.json").is_file()


async def test_bad_dimensions_rejected_and_active_untouched(db, tmp_path):
    first = AssetService.preview(_package(tmp_path, name="good.zip"), "owner")
    active = (await AssetService.confirm(first["upload_id"], "owner", db))["active"]

    bad_entries = {"manifest.json": _manifest(1).encode("utf-8"), "frames/000.png": _png(200, 240)}
    bad_path = tmp_path / "bad.zip"
    bad_path.write_bytes(_zip_bytes(bad_entries))

    with pytest.raises(AssetValidationError) as exc:
        AssetService.preview(bad_path, "owner")
    assert exc.value.code == "invalid_frame"
    assert (await AssetService.view(db))["active"]["id"] == active["id"]


async def test_zip_slip_and_extra_entries_rejected(tmp_path):
    slip = {
        "manifest.json": _manifest(1).encode("utf-8"),
        "frames/000.png": _png(),
        "../evil.png": b"x",
    }
    with pytest.raises(AssetValidationError) as exc:
        AssetService.preview(
            _write(tmp_path / "slip.zip", slip),
            "owner",
        )
    assert exc.value.code == "unsafe_path"

    extra = {
        "manifest.json": _manifest(1).encode("utf-8"),
        "frames/000.png": _png(),
        "readme.txt": b"hello",
    }
    with pytest.raises(AssetValidationError) as exc:
        AssetService.preview(
            _write(tmp_path / "extra.zip", extra),
            "owner",
        )
    assert exc.value.code == "unsupported_entry"


async def test_duplicate_entry_rejected(tmp_path):
    frames = {"manifest.json": _manifest(1).encode("utf-8"), "frames/000.png": _png()}
    data = _zip_bytes(frames)
    path = tmp_path / "dupe.zip"
    path.write_bytes(data)
    # 追加一个大小写不同的同名条目，zipfile 允许重复写。
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("FRAMES/000.png", _png())
    with pytest.raises(AssetValidationError) as exc:
        AssetService.preview(path, "owner")
    assert exc.value.code == "duplicate_entry"


async def test_frame_count_and_size_limits(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ASSET_MAX_FRAMES", 2)
    with pytest.raises(AssetValidationError) as exc:
        AssetService.preview(_package(tmp_path, frames=3), "owner")
    assert exc.value.code == "too_many_frames"

    monkeypatch.setattr(settings, "ASSET_MAX_FRAMES", 240)
    monkeypatch.setattr(settings, "ASSET_MAX_FRAME_BYTES", 10)
    with pytest.raises(AssetValidationError) as exc:
        AssetService.preview(_package(tmp_path, frames=1, name="big.zip"), "owner")
    assert exc.value.code == "frame_too_large"


async def test_rollback_restores_previous(db, tmp_path):
    first = AssetService.preview(_package(tmp_path, name="v1.zip", version="1.0.0"), "owner")
    view1 = await AssetService.confirm(first["upload_id"], "owner", db)
    first_id = view1["active"]["id"]

    second = AssetService.preview(_package(tmp_path, name="v2.zip", version="2.0.0"), "owner")
    view2 = await AssetService.confirm(second["upload_id"], "owner", db)
    second_id = view2["active"]["id"]
    assert view2["active"]["version"] == "2.0.0"
    assert view2["previous"]["id"] == first_id

    rolled = await AssetService.rollback(db)
    assert rolled["active"]["id"] == first_id
    assert rolled["previous"] is None
    assert not (settings.DATA_DIR / "assets" / second_id).exists()

    with pytest.raises(ValueError) as exc:
        await AssetService.rollback(db)
    assert str(exc.value) == "no_previous_package"


async def test_confirm_db_failure_cleans_new_dir_and_releases_lock(
    db, tmp_path, monkeypatch
):
    """DB 失败要删掉刚移入的正式目录，且删除发生在 `_lock` 之外。"""
    preview = AssetService.preview(_package(tmp_path), "owner")
    real_commit = db.commit
    state = {"failed": False}

    async def flaky_commit() -> None:
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("db down")
        await real_commit()

    locked_at_delete: list[bool] = []
    real_remove = AssetService._remove_dirs

    async def checked_remove(paths) -> None:
        locked_at_delete.append(asset_service._lock.locked())
        await real_remove(paths)

    monkeypatch.setattr(db, "commit", flaky_commit)
    monkeypatch.setattr(AssetService, "_remove_dirs", checked_remove)
    with pytest.raises(RuntimeError):
        await AssetService.confirm(preview["upload_id"], "owner", db)

    # 删除时锁必须已释放：旧实现（锁内 await 删目录）这条会失败。
    assert locked_at_delete and not any(locked_at_delete)

    assets = settings.DATA_DIR / "assets"
    if assets.exists():
        assert not list(assets.iterdir())

    # 锁已释放：失败后再确认一个新上传不会挂住（超时即失败）。
    again = AssetService.preview(_package(tmp_path, name="again.zip"), "owner")
    view = await asyncio.wait_for(
        AssetService.confirm(again["upload_id"], "owner", db), timeout=5
    )
    assert view["active"] is not None


async def test_confirm_cleans_staged_dir_when_assets_root_unwritable(
    db, tmp_path, monkeypatch
):
    """pop 之后 mkdir 失败：暂存目录要被清掉，不能留在系统临时目录。"""
    preview = AssetService.preview(_package(tmp_path), "owner")
    staged = asset_service._PENDING[preview["upload_id"]]["dir"]
    assert staged.exists()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", boom)
    with pytest.raises(OSError):
        await AssetService.confirm(preview["upload_id"], "owner", db)

    assert not staged.exists()
    assert preview["upload_id"] not in asset_service._PENDING


async def test_confirm_cleans_partial_dest_when_move_fails(db, tmp_path, monkeypatch):
    """跨盘复制中途失败：半个正式目录与暂存目录都要清理。"""
    preview = AssetService.preview(_package(tmp_path), "owner")
    staged = asset_service._PENDING[preview["upload_id"]]["dir"]

    def half_move(src, dst):
        # 模拟 copytree 拷了一部分后失败：dest 已创建且有残留。
        Path(dst).mkdir(parents=True, exist_ok=True)
        (Path(dst) / "partial.png").write_bytes(b"x")
        raise OSError("cross-device copy failed")

    monkeypatch.setattr(asset_service.shutil, "move", half_move)
    with pytest.raises(OSError):
        await AssetService.confirm(preview["upload_id"], "owner", db)

    assets = settings.DATA_DIR / "assets"
    if assets.exists():
        assert not list(assets.iterdir())
    assert not staged.exists()
    assert preview["upload_id"] not in asset_service._PENDING


async def test_expired_and_foreign_upload_rejected(db, tmp_path):
    preview = AssetService.preview(_package(tmp_path), "owner-a")
    asset_service._PENDING[preview["upload_id"]]["expires_at"] = 0
    with pytest.raises(ValueError) as exc:
        await AssetService.confirm(preview["upload_id"], "owner-a", db)
    assert str(exc.value) == "upload_expired"

    other = AssetService.preview(_package(tmp_path, name="other.zip"), "owner-a")
    with pytest.raises(ValueError) as exc:
        await AssetService.confirm(other["upload_id"], "owner-b", db)
    assert str(exc.value) == "upload_expired"
    # 他人的 confirm 不能顺手作废别人的暂存包：entry 与目录都要还在。
    assert other["upload_id"] in asset_service._PENDING
    assert asset_service._PENDING[other["upload_id"]]["dir"].exists()
    assert (await AssetService.confirm(other["upload_id"], "owner-a", db))[
        "active"
    ] is not None


async def test_fallback_frame_and_out_of_range(db, tmp_path):
    preview = AssetService.preview(_package(tmp_path), "owner")
    view = await AssetService.confirm(preview["upload_id"], "owner", db)

    placeholder = await AssetService.frame_bytes("fallback", 0, db)
    assert placeholder is not None
    width, height = struct.unpack(">II", placeholder[16:24])
    assert (width, height) == (240, 240)
    assert await AssetService.frame_bytes("fallback", 1, db) is None

    assert await AssetService.frame_bytes(view["active"]["id"], 2, db) is None
    assert await AssetService.frame_bytes(view["active"]["id"], -1, db) is None
    assert await AssetService.frame_bytes("missing", 0, db) is None


async def test_placeholder_is_valid_240_png():
    data = AssetService.placeholder_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", data[16:24]) == (240, 240)


def _write(path, entries):
    path.write_bytes(_zip_bytes(entries))
    return path
