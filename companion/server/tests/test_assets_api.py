"""4D：素材包 API 边界。"""

from __future__ import annotations

import io
import json
import shutil
import struct
import zipfile
import zlib

import pytest
from sqlalchemy import delete

from companion.server.app.assetpacks.service import AssetService
from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.asset_package import AssetPackageModel


def _png(width: int = 240, height: int = 240, color: int = 6) -> bytes:
    raw = b"".join(b"\x00" + b"\x00" * (4 if color == 6 else 3) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)

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


def _zip(frames: int = 1, *, width: int = 240, version: str = "1.0.0") -> bytes:
    manifest = {
        "name": "Q版陪伴",
        "version": version,
        "fps": 12,
        "frames": [f"frames/{index:03d}.png" for index in range(frames)],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for index in range(frames):
            zf.writestr(f"frames/{index:03d}.png", _png(width=width))
    return buf.getvalue()


def _files(data: bytes, name: str = "pkg.zip") -> dict:
    return {"file": (name, io.BytesIO(data), "application/zip")}


@pytest.fixture(autouse=True)
def _isolated_asset_data(tmp_path, monkeypatch):
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


async def _upload(client, auth_headers, data: bytes, name: str = "pkg.zip") -> dict:
    resp = await client.post(
        "/api/assets/preview",
        headers=auth_headers,
        files=_files(data, name),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _confirm(client, auth_headers, upload_id: str) -> dict:
    resp = await client.post(
        "/api/assets/confirm",
        headers=auth_headers,
        json={"upload_id": upload_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_preview_confirm_and_fetch_frame(client, auth_headers):
    preview = await _upload(client, auth_headers, _zip(2))
    assert preview["frame_count"] == 2
    assert preview["version"] == "1.0.0"

    view = await _confirm(client, auth_headers, preview["upload_id"])
    active = view["active"]
    assert active["name"] == "Q版陪伴"
    assert view["previous"] is None

    frame = await client.get(
        f"/api/assets/{active['id']}/frame/1",
        headers=auth_headers,
    )
    assert frame.status_code == 200
    assert frame.headers["content-type"] == "image/png"
    assert frame.headers["cache-control"] == "private, max-age=300"
    assert frame.content.startswith(b"\x89PNG")

    listing = await client.get("/api/assets", headers=auth_headers)
    assert listing.json()["active"]["id"] == active["id"]


async def test_invalid_package_returns_400_and_keeps_active(client, auth_headers):
    first = await _upload(client, auth_headers, _zip(1, version="1.0.0"))
    active = (await _confirm(client, auth_headers, first["upload_id"]))["active"]

    bad = await client.post(
        "/api/assets/preview",
        headers=auth_headers,
        files=_files(_zip(1, width=200), "bad.zip"),
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "invalid_frame"

    listing = await client.get("/api/assets", headers=auth_headers)
    assert listing.json()["active"]["id"] == active["id"]


async def test_rollback_without_previous_returns_409(client, auth_headers):
    resp = await client.post("/api/assets/rollback", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "no_previous_package"


async def test_rollback_restores_previous(client, auth_headers):
    first = await _upload(client, auth_headers, _zip(1, version="1.0.0"))
    first_id = (await _confirm(client, auth_headers, first["upload_id"]))["active"]["id"]
    second = await _upload(client, auth_headers, _zip(1, version="2.0.0"))
    second_id = (await _confirm(client, auth_headers, second["upload_id"]))["active"]["id"]

    resp = await client.post("/api/assets/rollback", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active"]["id"] == first_id
    assert body["previous"] is None
    assert body["active"]["id"] != second_id


async def test_frame_index_out_of_range_returns_404(client, auth_headers):
    preview = await _upload(client, auth_headers, _zip(1))
    active = (await _confirm(client, auth_headers, preview["upload_id"]))["active"]

    resp = await client.get(f"/api/assets/{active['id']}/frame/5", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "frame_not_found"

    missing = await client.get("/api/assets/not-a-package/frame/0", headers=auth_headers)
    assert missing.status_code == 404


async def test_fallback_frame_is_served(client, auth_headers):
    resp = await client.get("/api/assets/fallback/frame/0", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.content.startswith(b"\x89PNG")
    assert struct.unpack(">II", resp.content[16:24]) == (240, 240)

    out = await client.get("/api/assets/fallback/frame/1", headers=auth_headers)
    assert out.status_code == 404


async def test_upload_larger_than_limit_returns_413(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "ASSET_MAX_ARCHIVE_BYTES", 10)
    resp = await client.post(
        "/api/assets/preview",
        headers=auth_headers,
        files=_files(_zip(1)),
    )
    assert resp.status_code == 413
    assert resp.json()["detail"] == "archive_too_large"


async def test_chunked_upload_over_limit_returns_413(
    client, auth_headers, monkeypatch
):
    """App 流式上传没有 Content-Length：也要在读完整个请求体前 413。"""
    monkeypatch.setattr(settings, "ASSET_MAX_ARCHIVE_BYTES", 1024)
    boundary = "chunked-boundary"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="pkg.zip"\r\n'
        "Content-Type: application/zip\r\n\r\n"
    ).encode()

    async def chunks():
        yield prefix
        for _ in range(10):
            yield b"x" * 10_000
        yield f"\r\n--{boundary}--\r\n".encode()

    resp = await client.post(
        "/api/assets/preview",
        headers={
            **auth_headers,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        content=chunks(),
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"] == "archive_too_large"


async def test_non_zip_upload_rejected(client, auth_headers):
    resp = await client.post(
        "/api/assets/preview",
        headers=auth_headers,
        files=_files(b"not a zip", "pkg.txt"),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_archive"


async def test_assets_require_app_token(client):
    resp = await client.get("/api/assets")
    assert resp.status_code == 401

    assert (await client.post("/api/assets/preview", files=_files(_zip(1)))).status_code == 401
    assert (
        await client.post("/api/assets/confirm", json={"upload_id": "x"})
    ).status_code == 401
    assert (await client.post("/api/assets/rollback")).status_code == 401
    assert (await client.get("/api/assets/fallback/frame/0")).status_code == 401


async def test_confirm_unknown_upload_and_extra_fields(client, auth_headers):
    resp = await client.post(
        "/api/assets/confirm",
        headers=auth_headers,
        json={"upload_id": "does-not-exist"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "upload_expired"

    # 多余字段不能被静默忽略（避免客户端误传路径/所有者）。
    extra = await client.post(
        "/api/assets/confirm",
        headers=auth_headers,
        json={"upload_id": "x", "owner": "someone"},
    )
    assert extra.status_code == 422
