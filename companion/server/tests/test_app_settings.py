import pytest
from sqlalchemy import select, text

from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.app_setting import AppSettingModel
from companion.server.app.services.app_settings import AppSettingsService
from companion.server.app.services.secret_box import decrypt_secret, encrypt_secret


def test_secret_box_hides_plaintext():
    token = encrypt_secret("sk-unit-test")
    assert token.startswith("v1:")
    assert "sk-unit-test" not in token
    assert decrypt_secret(token) == "sk-unit-test"


def test_secret_box_rejects_garbage():
    with pytest.raises(ValueError):
        decrypt_secret("v1:not-base64")


def test_secret_box_binds_ciphertext_to_setting_key():
    token = encrypt_secret("sk-aad", aad="deepseek_api_key")
    assert decrypt_secret(token, aad="deepseek_api_key") == "sk-aad"
    # 同一密文贴到另一行必须解不开，避免两把 Key 互换。
    with pytest.raises(ValueError):
        decrypt_secret(token, aad="dashscope_api_key")


async def test_update_applies_values_immediately():
    async with AsyncSessionLocal() as db:
        await AppSettingsService.update(
            db,
            plain={
                "deepseek_base_url": "https://example.test",
                "voice_provider": "dashscope",
            },
            secrets={"deepseek_api_key": "sk-unit-test"},
        )
        assert settings.DEEPSEEK_BASE_URL == "https://example.test"
        assert settings.VOICE_PROVIDER == "dashscope"
        assert settings.DEEPSEEK_API_KEY == "sk-unit-test"
        raw = (await db.execute(select(AppSettingModel))).scalars().all()
        assert all("sk-unit-test" not in (row.value or "") for row in raw)


async def test_view_reports_source_and_never_echoes_secret():
    async with AsyncSessionLocal() as db:
        await AppSettingsService.update(db, secrets={"dashscope_api_key": "sk-unit-test"})
        view = await AppSettingsService.view(db)
        assert view["dashscope"]["configured"] is True
        assert view["dashscope"]["source"] == "database"
        assert "sk-unit-test" not in str(view)


async def test_secret_upsert_reencrypts_and_stays_secret():
    async with AsyncSessionLocal() as db:
        await AppSettingsService.update(db, secrets={"deepseek_api_key": "sk-first"})
        first = (await db.get(AppSettingModel, "deepseek_api_key")).value
        await AppSettingsService.update(db, secrets={"deepseek_api_key": "sk-second"})
        row = await db.get(AppSettingModel, "deepseek_api_key")
        assert row.is_secret is True
        assert row.value != first
        assert "sk-second" not in (row.value or "")
        assert settings.DEEPSEEK_API_KEY == "sk-second"


async def test_clear_falls_back_to_environment(monkeypatch):
    # 模拟进程启动时从环境变量读到了 Key：清掉 DB 覆盖后应回落到它。
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "env-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-key")
    AppSettingsService.reset_cache()
    async with AsyncSessionLocal() as db:
        await AppSettingsService.update(db, secrets={"deepseek_api_key": None})
        view = await AppSettingsService.view(db)
        assert view["deepseek"]["source"] == "environment"
        assert view["deepseek"]["configured"] is True


async def test_startup_load_applies_stored_ciphertext(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    AppSettingsService.reset_cache()
    async with AsyncSessionLocal() as db:
        db.add(
            AppSettingModel(
                key="deepseek_api_key",
                value=encrypt_secret("sk-from-db", aad="deepseek_api_key"),
                is_secret=True,
            )
        )
        await db.commit()
        await AppSettingsService.load_and_apply(db)
        assert settings.DEEPSEEK_API_KEY == "sk-from-db"
        view = await AppSettingsService.view(db)
        assert view["deepseek"]["configured"] is True
        assert view["deepseek"]["source"] == "database"


async def test_corrupt_ciphertext_is_tolerated(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    AppSettingsService.reset_cache()
    async with AsyncSessionLocal() as db:
        db.add(AppSettingModel(key="deepseek_api_key", value="v1:garbage", is_secret=True))
        await db.commit()
        await AppSettingsService.load_and_apply(db)
        assert settings.DEEPSEEK_API_KEY == ""
        view = await AppSettingsService.view(db)
        assert view["deepseek"]["configured"] is False
        assert view["deepseek"]["error"] == "decrypt_failed"
        # 行还在，但它没有被加载，实际生效的是“未配置”，不能谎报 database。
        assert view["deepseek"]["source"] == "unset"


async def test_corrupt_ciphertext_falls_back_to_environment(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "env-fallback")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-fallback")
    AppSettingsService.reset_cache()
    async with AsyncSessionLocal() as db:
        db.add(AppSettingModel(key="deepseek_api_key", value="v1:garbage", is_secret=True))
        await db.commit()
        await AppSettingsService.load_and_apply(db)
        view = await AppSettingsService.view(db)
        assert view["deepseek"]["configured"] is True
        assert view["deepseek"]["source"] == "environment"
        assert view["deepseek"]["error"] == "decrypt_failed"


async def test_plaintext_value_for_secret_key_is_not_trusted(monkeypatch):
    """即使行上 is_secret=False，密钥键的明文也不能被当作已配置。"""
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    AppSettingsService.reset_cache()
    async with AsyncSessionLocal() as db:
        db.add(
            AppSettingModel(key="deepseek_api_key", value="sk-plaintext", is_secret=False)
        )
        await db.commit()
        await AppSettingsService.load_and_apply(db)
        assert settings.DEEPSEEK_API_KEY == ""
        view = await AppSettingsService.view(db)
        assert view["deepseek"]["configured"] is False
        assert view["deepseek"]["error"] == "decrypt_failed"


async def test_migration_creates_app_settings():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text("PRAGMA table_info(app_settings)"))).all()
        names = {row[1] for row in rows}
        assert {"key", "value", "is_secret", "updated_at"} <= names
