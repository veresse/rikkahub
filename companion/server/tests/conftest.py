import asyncio
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from companion.server.app.config import settings
from companion.server.app.database import init_db
from companion.server.app.main import app
from companion.server.app.security.rate_limit import reset_rate_limits

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def _setup_database():
    asyncio.run(init_db())


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture(autouse=True)
async def _restore_app_settings():
    """测试不得改写本地开发库的设置覆盖，也不得污染后续用例。

    先快照 `app_settings` 现有行，用例结束后清空并原样写回：不能直接
    `DELETE`，否则开发者通过 App 保存的 Key 会在一次 `pytest` 后就消失。
    迁移尚未应用（表不存在）时快照/清理都会报错，记录后跳过。
    """
    from sqlalchemy import delete, select

    from companion.server.app.database import AsyncSessionLocal
    from companion.server.app.models.app_setting import AppSettingModel
    from companion.server.app.services.app_settings import (
        FIELD_BY_KEY,
        AppSettingsService,
    )

    snapshot = {field: getattr(settings, field) for field, _ in FIELD_BY_KEY.values()}
    AppSettingsService.reset_cache()

    rows: list[tuple[str, str, bool, int | None]] = []
    try:
        async with AsyncSessionLocal() as session:
            rows = [
                (row.key, row.value, bool(row.is_secret), row.updated_at)
                for row in (await session.execute(select(AppSettingModel))).scalars()
            ]
    except Exception:
        logger.warning("app_settings snapshot skipped (table unavailable)")

    yield

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(AppSettingModel))
            for key, value, is_secret, updated_at in rows:
                session.add(
                    AppSettingModel(
                        key=key,
                        value=value,
                        is_secret=is_secret,
                        updated_at=updated_at,
                    )
                )
            await session.commit()
    except Exception:
        logger.warning("app_settings restore skipped (table unavailable)")

    AppSettingsService.reset_cache()
    for field, value in snapshot.items():
        setattr(settings, field, value)

    # 设置写入会重建语音运行时；恢复 `settings` 后同步重建，避免把用例里的
    # 语音 provider/开关泄漏给后续用例。
    try:
        from companion.server.app.routers import ws as ws_router

        ws_router.rebuild_voice_runtime()
    except Exception:
        logger.warning("voice runtime rebuild after test skipped")


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
