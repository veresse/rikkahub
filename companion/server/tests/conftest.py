import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from companion.server.app.database import init_db
from companion.server.app.main import app
from companion.server.app.security.rate_limit import reset_rate_limits


@pytest.fixture(scope="session", autouse=True)
def _setup_database():
    asyncio.run(init_db())


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client):
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}
