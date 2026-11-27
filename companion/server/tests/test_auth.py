"""App Bearer 鉴权测试（Phase 2 基础）。"""

import pytest

from companion.server.app.config import settings


async def test_health_is_public(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/assistants"),
        ("get", "/api/conversations"),
        ("get", "/api/memories"),
        ("get", "/api/devices"),
        ("get", "/api/actions/catalog"),
    ],
)
async def test_business_endpoints_require_token(client, method, path):
    resp = await getattr(client, method)(path)
    assert resp.status_code == 401


async def test_invalid_token_is_rejected(client):
    resp = await client.get(
        "/api/assistants", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


async def test_login_returns_token_and_expiry(client):
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert body["token_id"]
    assert body["expires_at"] > 0
    assert body["username"] == "admin"


async def test_login_wrong_password(client):
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert "invalid" in resp.json()["detail"]


async def test_login_rate_limit(client):
    for _ in range(5):
        resp = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        assert resp.status_code == 401
    blocked = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "rate_limited"


async def test_login_without_configured_password(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", "")
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "anything"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "admin_password_not_configured"


async def test_logout_revokes_current_token(client, auth_headers):
    ok = await client.get("/api/assistants", headers=auth_headers)
    assert ok.status_code == 200

    logout = await client.post("/api/auth/logout", headers=auth_headers)
    assert logout.status_code == 200

    after = await client.get("/api/assistants", headers=auth_headers)
    assert after.status_code == 401


async def test_list_and_revoke_tokens(client, auth_headers):
    second = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password", "device_name": "phone"},
    )
    assert second.status_code == 200
    second_token = second.json()["token"]

    listing = await client.get("/api/auth/tokens", headers=auth_headers)
    assert listing.status_code == 200
    tokens = listing.json()
    assert any(t["device_name"] == "phone" and t["revoked_at"] is None for t in tokens)
    assert all("token" not in t for t in tokens)
    assert all("token_hash" not in t for t in tokens)

    token_id = next(t["id"] for t in tokens if t["device_name"] == "phone")
    revoke = await client.post(f"/api/auth/tokens/{token_id}/revoke", headers=auth_headers)
    assert revoke.status_code == 200

    after = await client.get(
        "/api/assistants", headers={"Authorization": f"Bearer {second_token}"}
    )
    assert after.status_code == 401


async def test_list_tokens_hides_revoked_and_expired(client, auth_headers):
    from sqlalchemy import delete

    from companion.server.app.database import AsyncSessionLocal
    from companion.server.app.models.app_token import AppTokenModel

    second = await client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-admin-password",
            "device_name": "hidden-phone",
        },
    )
    token_id = second.json()["token_id"]
    revoke = await client.post(
        f"/api/auth/tokens/{token_id}/revoke", headers=auth_headers
    )
    assert revoke.status_code == 200

    listing = (await client.get("/api/auth/tokens", headers=auth_headers)).json()
    assert all(t["id"] != token_id for t in listing)

    # 重复撤销不能再报成功（App 会误显示一次空操作）。
    again = await client.post(
        f"/api/auth/tokens/{token_id}/revoke", headers=auth_headers
    )
    assert again.status_code == 404
    assert again.json()["detail"] == "token_not_found"

    expired_id = "token-expired-for-list-test"
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AppTokenModel).where(AppTokenModel.id == expired_id))
        db.add(
            AppTokenModel(
                id=expired_id,
                username="admin",
                device_name="expired-phone",
                token_hash="expired-list-test-hash",
                created_at=1,
                expires_at=1,
            )
        )
        await db.commit()
    try:
        listing = (await client.get("/api/auth/tokens", headers=auth_headers)).json()
        assert all(t["id"] != expired_id for t in listing)
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AppTokenModel).where(AppTokenModel.id == expired_id))
            await db.commit()


async def test_password_hash_mode(client, monkeypatch):
    from companion.server.app.security.password import hash_password

    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", hash_password("hashed-secret"))
    ok = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "hashed-secret"},
    )
    assert ok.status_code == 200
