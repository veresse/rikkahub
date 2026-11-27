"""4D：备份上传/预览/确认/导出的边界与所有权。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from companion.server.app.config import settings
from companion.server.app.importer import service as importer_service


@pytest.fixture(autouse=True)
def _clear_previews():
    importer_service._reset_preview_cache()
    yield
    importer_service._reset_preview_cache()


def _zip_bytes(system_prompt: str = "只应出现在预览外的正文") -> bytes:
    buf = io.BytesIO()
    assistant = {
        "id": "asst-backup",
        "name": "备份角色",
        "systemPrompt": system_prompt,
    }
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("settings.json", json.dumps({"assistants": [assistant]}))
    return buf.getvalue()


async def test_preview_rejects_oversized_upload(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 10)
    files = {"file": ("b.zip", io.BytesIO(b"x" * 100), "application/zip")}
    resp = await client.post("/api/backup/preview", headers=auth_headers, files=files)
    assert resp.status_code == 413
    assert resp.json()["detail"] == "file_too_large"


async def test_preview_rejects_non_zip_filename(client, auth_headers):
    files = {"file": ("b.txt", io.BytesIO(_zip_bytes()), "text/plain")}
    resp = await client.post("/api/backup/preview", headers=auth_headers, files=files)
    assert resp.status_code == 400


async def test_preview_returns_summary_only(client, auth_headers):
    files = {"file": ("b.zip", io.BytesIO(_zip_bytes("秘密正文")), "application/zip")}
    resp = await client.post("/api/backup/preview", headers=auth_headers, files=files)
    assert resp.status_code == 200
    body = resp.json()
    assert body["preview_id"]
    assert body["summary"]["assistant_count"] == 1
    assert body["assistants_sample"] == ["备份角色"]
    # 预览只回名字/计数，不能把角色正文回显出来。
    assert "秘密正文" not in resp.text


async def test_preview_confirm_requires_owner(client, auth_headers):
    files = {"file": ("b.zip", io.BytesIO(_zip_bytes()), "application/zip")}
    resp = await client.post("/api/backup/preview", headers=auth_headers, files=files)
    assert resp.status_code == 200
    preview_id = resp.json()["preview_id"]

    importer_service._PREVIEW_CACHE[preview_id]["owner"] = "another-token"

    confirm = await client.post(
        "/api/backup/confirm",
        headers=auth_headers,
        json={"preview_id": preview_id},
    )
    assert confirm.status_code == 400
    assert confirm.json()["detail"] == "preview_expired"
    # 失败预览必须被丢弃，不能留着重试。
    assert preview_id not in importer_service._PREVIEW_CACHE


async def test_preview_confirm_rejects_expired(client, auth_headers):
    files = {"file": ("b.zip", io.BytesIO(_zip_bytes()), "application/zip")}
    resp = await client.post("/api/backup/preview", headers=auth_headers, files=files)
    preview_id = resp.json()["preview_id"]
    importer_service._PREVIEW_CACHE[preview_id]["expires_at"] = 0

    confirm = await client.post(
        "/api/backup/confirm",
        headers=auth_headers,
        json={"preview_id": preview_id},
    )
    assert confirm.status_code == 400
    assert confirm.json()["detail"] == "preview_expired"


async def test_confirm_unknown_preview_is_preview_expired(client, auth_headers):
    confirm = await client.post(
        "/api/backup/confirm",
        headers=auth_headers,
        json={"preview_id": "missing"},
    )
    assert confirm.status_code == 400
    assert confirm.json()["detail"] == "preview_expired"


def test_preview_cache_caps_entries(monkeypatch):
    import time

    monkeypatch.setattr(importer_service, "MAX_PREVIEWS", 2)
    now = time.time()
    for index in range(4):
        importer_service._PREVIEW_CACHE[f"p{index}"] = {
            "data": {},
            "owner": "owner",
            "expires_at": now + index,
        }
    importer_service._prune()
    assert set(importer_service._PREVIEW_CACHE) == {"p2", "p3"}


def test_preview_cache_cap_holds_after_insert(tmp_path):
    """插入新预览后立即裁剪，缓存不能短暂达到 MAX_PREVIEWS + 1。"""
    from companion.server.app.importer.service import ImportService

    zip_path = tmp_path / "cap.zip"
    zip_path.write_bytes(_zip_bytes())

    ids = [
        ImportService.generate_preview(zip_path, "owner")["preview_id"]
        for _ in range(importer_service.MAX_PREVIEWS + 5)
    ]
    assert len(importer_service._PREVIEW_CACHE) == importer_service.MAX_PREVIEWS
    assert ids[-1] in importer_service._PREVIEW_CACHE
    assert ids[0] not in importer_service._PREVIEW_CACHE


async def test_import_failure_is_not_reported_as_preview_expired(
    client, auth_headers, monkeypatch
):
    async def boom(*args, **kwargs):
        raise ValueError("column exploded")

    monkeypatch.setattr(
        importer_service.ImportService,
        "execute_import",
        staticmethod(boom),
    )
    resp = await client.post(
        "/api/backup/confirm",
        headers=auth_headers,
        json={"preview_id": "whatever"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "import_failed"


async def test_export_failure_closes_db_and_removes_temp_dir(
    client, auth_headers, monkeypatch, tmp_path
):
    from companion.server.app.routers import import_export as router_module

    target = tmp_path / "export_fixed"
    target.mkdir()
    monkeypatch.setattr(router_module.tempfile, "mkdtemp", lambda prefix="": str(target))

    real_connect = router_module.sqlite3.connect
    opened: dict[str, object] = {}

    class FailingConn:
        def __init__(self, conn):
            self._conn = conn
            self.calls = 0

        def cursor(self):
            return self

        def execute(self, *args, **kwargs):
            self.calls += 1
            if self.calls >= 2:
                raise RuntimeError("boom")
            return self._conn.execute(*args, **kwargs)

        def commit(self):
            self._conn.commit()

        def close(self):
            opened["closed"] = True
            self._conn.close()

    def fake_connect(path, *args, **kwargs):
        conn = real_connect(path, *args, **kwargs)
        # 只拦截导出自己的 rikka_hub.db，别碰 aiosqlite 的连接。
        if str(path).endswith("rikka_hub.db"):
            wrapped = FailingConn(conn)
            opened["conn"] = wrapped
            return wrapped
        return conn

    monkeypatch.setattr(router_module.sqlite3, "connect", fake_connect)

    resp = await client.get("/api/backup/export", headers=auth_headers)
    assert resp.status_code == 500
    assert opened.get("closed") is True
    assert not target.exists()


async def test_export_round_trip_and_cleanup(client, auth_headers):
    resp = await client.get("/api/backup/export", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "rikkahub_backup_" in resp.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    assert {"settings.json", "rikka_hub.db"} <= set(z.namelist())
