"""4D：`/api/settings` 读写与密钥只写语义。"""

from __future__ import annotations

from companion.server.app.config import settings


async def test_get_settings_hides_secret_values(client, auth_headers):
    resp = await client.get("/api/settings", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["deepseek"]["source"] in {"database", "environment", "unset"}
    assert "api_key" not in resp.text


async def test_put_settings_applies_and_persists(client, auth_headers):
    resp = await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"deepseek_model": "deepseek-reasoner", "search_enabled": False},
    )
    assert resp.status_code == 200
    assert settings.DEEPSEEK_DEFAULT_MODEL == "deepseek-reasoner"
    assert settings.SEARCH_ENABLED is False
    body = resp.json()
    assert body["deepseek"]["model"] == "deepseek-reasoner"
    assert body["search"]["enabled"] is False


async def test_put_secrets_is_write_only(client, auth_headers):
    resp = await client.put(
        "/api/settings/secrets",
        headers=auth_headers,
        json={"deepseek_api_key": "sk-write-only"},
    )
    assert resp.status_code == 200
    assert "sk-write-only" not in resp.text
    got = await client.get("/api/settings", headers=auth_headers)
    assert "sk-write-only" not in got.text
    assert got.json()["deepseek"]["configured"] is True


async def test_empty_secret_and_delete_clear(client, auth_headers):
    await client.put(
        "/api/settings/secrets",
        headers=auth_headers,
        json={"dashscope_api_key": "sk-x"},
    )
    await client.put(
        "/api/settings/secrets",
        headers=auth_headers,
        json={"dashscope_api_key": ""},
    )
    got = (await client.get("/api/settings", headers=auth_headers)).json()
    assert got["dashscope"]["configured"] is False

    await client.put(
        "/api/settings/secrets",
        headers=auth_headers,
        json={"dashscope_api_key": "sk-y"},
    )
    resp = await client.delete(
        "/api/settings/secrets/dashscope_api_key", headers=auth_headers
    )
    assert resp.status_code == 200
    got = (await client.get("/api/settings", headers=auth_headers)).json()
    assert got["dashscope"]["configured"] is False


async def test_unknown_secret_key_is_rejected(client, auth_headers):
    resp = await client.delete(
        "/api/settings/secrets/not_a_key", headers=auth_headers
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unknown_setting"


async def test_invalid_base_url_and_provider_are_rejected(client, auth_headers):
    bad_url = await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"deepseek_base_url": "ftp://example.test"},
    )
    assert bad_url.status_code == 400
    assert bad_url.json()["detail"] == "invalid_base_url"

    bad_provider = await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"voice_provider": "openai"},
    )
    assert bad_provider.status_code == 400
    assert bad_provider.json()["detail"] == "invalid_voice_provider"


async def test_overlong_secret_is_rejected(client, auth_headers):
    resp = await client.put(
        "/api/settings/secrets",
        headers=auth_headers,
        json={"deepseek_api_key": "k" * 513},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_secret"


async def test_misrouted_secret_field_is_rejected(client, auth_headers):
    # 密钥发到普通设置端点：必须报错，不能“200 但静默丢弃”。
    resp = await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"deepseek_api_key": "sk-misrouted"},
    )
    assert resp.status_code == 422

    # 普通字段发到密钥端点：同样必须报错。
    resp = await client.put(
        "/api/settings/secrets",
        headers=auth_headers,
        json={"deepseek_model": "deepseek-reasoner"},
    )
    assert resp.status_code == 422


async def test_base_url_must_be_deepseek_https(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_ALLOW_CUSTOM_BASE_URL", False)

    foreign = await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"deepseek_base_url": "https://attacker.example"},
    )
    assert foreign.status_code == 400
    assert foreign.json()["detail"] == "invalid_base_url"

    # 明文 HTTP 会把 Key 暴露在链路上，必须拒绝。
    cleartext = await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"deepseek_base_url": "http://api.deepseek.com"},
    )
    assert cleartext.status_code == 400
    assert cleartext.json()["detail"] == "invalid_base_url"


async def test_secret_with_control_chars_is_rejected(client, auth_headers):
    resp = await client.put(
        "/api/settings/secrets",
        headers=auth_headers,
        json={"deepseek_api_key": "sk-a\nsk-b"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_secret"


async def test_delete_missing_secret_is_idempotent(client, auth_headers):
    resp = await client.delete(
        "/api/settings/secrets/deepseek_api_key", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["deepseek"]["configured"] is False


async def test_voice_settings_rebuild_runtime(client, auth_headers, monkeypatch):
    from companion.server.app.audio.dashscope import DashScopeAsrProvider
    from companion.server.app.routers import ws

    monkeypatch.setattr(settings, "VOICE_PROVIDER", "unavailable")
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)

    await client.put(
        "/api/settings/secrets",
        headers=auth_headers,
        json={"dashscope_api_key": "sk-dashscope"},
    )
    await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"voice_provider": "dashscope"},
    )
    runtime = ws.device_gateway._voice_runtime
    assert runtime is not None
    assert isinstance(runtime.asr, DashScopeAsrProvider)

    # 关闭语音后必须立刻撤掉运行时，不能让旧 Key 继续上传音频。
    await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"voice_enabled": False},
    )
    assert ws.device_gateway._voice_runtime is None


async def test_non_voice_save_keeps_connected_voice_session(client, auth_headers):
    """普通设置保存不能关闭已连接设备的语音会话。"""
    import asyncio

    from companion.server.app.routers import ws

    class _Session:
        def __init__(self) -> None:
            self.voice = object()
            self.voice_runtime = ws.device_gateway._voice_runtime
            self._voice_begin_task = None
            self.closed: list[bool] = []

        async def close_voice(self) -> None:
            self.closed.append(True)
            self.voice = None

    session = _Session()
    ws.device_gateway._sessions.add(session)
    try:
        resp = await client.put(
            "/api/settings",
            headers=auth_headers,
            json={"search_enabled": True, "deepseek_model": "deepseek-chat"},
        )
        assert resp.status_code == 200
        await asyncio.sleep(0)
        assert session.closed == []
        assert session.voice is not None
    finally:
        ws.device_gateway._sessions.discard(session)


async def test_voice_save_closes_connected_voice_session(client, auth_headers):
    """语音相关设置变化必须让已连接设备下一轮按新配置重建。"""
    import asyncio

    from companion.server.app.routers import ws

    class _Session:
        def __init__(self) -> None:
            self.voice = object()
            self.voice_runtime = ws.device_gateway._voice_runtime
            self._voice_begin_task = None
            self.closed: list[bool] = []

        async def close_voice(self) -> None:
            self.closed.append(True)
            self.voice = None

    session = _Session()
    ws.device_gateway._sessions.add(session)
    try:
        resp = await client.put(
            "/api/settings",
            headers=auth_headers,
            json={"voice_enabled": not settings.VOICE_ENABLED},
        )
        assert resp.status_code == 200
        await asyncio.sleep(0)
        assert session.closed == [True]
        assert session.voice is None
    finally:
        ws.device_gateway._sessions.discard(session)


async def test_saving_unchanged_voice_values_does_not_rebuild(
    client, auth_headers, monkeypatch
):
    """原值保存（App 每次提交整组语音字段）不能关闭正在进行的语音通话。"""
    from companion.server.app.routers import ws

    calls: list[int] = []
    monkeypatch.setattr(ws, "rebuild_voice_runtime", lambda: calls.append(1))
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    # 环境/CI 的 .env 可能把 provider 设成 mock（API 只接受 unavailable/dashscope）；
    # 固定基线，避免用例依赖外部配置。
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "unavailable")

    resp = await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"voice_enabled": True, "voice_provider": "unavailable"},
    )
    assert resp.status_code == 200
    assert calls == []


async def test_non_voice_settings_do_not_rebuild_runtime(
    client, auth_headers, monkeypatch
):
    """搜索/DeepSeek 设置不能连带重建语音运行时，否则会踢掉进行中的语音会话。"""
    from companion.server.app.routers import ws

    calls: list[int] = []
    monkeypatch.setattr(ws, "rebuild_voice_runtime", lambda: calls.append(1))

    await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"search_enabled": True, "deepseek_model": "deepseek-chat"},
    )
    await client.put(
        "/api/settings/secrets",
        headers=auth_headers,
        json={"deepseek_api_key": "sk-not-voice"},
    )
    assert calls == []


async def test_settings_save_succeeds_when_runtime_rebuild_fails(
    client, auth_headers, monkeypatch
):
    """设置已 commit 后重建运行时失败，不能把保存报告成 500。"""
    from companion.server.app.routers import ws

    def boom() -> None:
        raise RuntimeError("rebuild exploded")

    # 先把 provider 推到 dashscope，确保下面的改动确实触发重建。
    await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"voice_provider": "dashscope"},
    )
    monkeypatch.setattr(ws, "rebuild_voice_runtime", boom)

    resp = await client.put(
        "/api/settings",
        headers=auth_headers,
        json={"voice_provider": "unavailable"},
    )
    assert resp.status_code == 200
    assert resp.json()["voice"]["provider"] == "unavailable"


async def test_startup_loads_db_overrides_before_building_voice_runtime(monkeypatch):
    """回归：运行时曾在 DB 覆盖加载之前构建，导致 4D 的语音设置不生效。"""
    from companion.server.app.audio.dashscope import DashScopeAsrProvider
    from companion.server.app.database import AsyncSessionLocal
    from companion.server.app.main import prepare_settings_runtime
    from companion.server.app.models.app_setting import AppSettingModel
    from companion.server.app.routers import ws
    from companion.server.app.services.app_settings import AppSettingsService
    from companion.server.app.services.secret_box import encrypt_secret

    async with AsyncSessionLocal() as db:
        db.add(
            AppSettingModel(key="voice_provider", value="dashscope", is_secret=False)
        )
        db.add(
            AppSettingModel(
                key="dashscope_api_key",
                value=encrypt_secret("sk-from-db", aad="dashscope_api_key"),
                is_secret=True,
            )
        )
        await db.commit()

    AppSettingsService.reset_cache()
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "unavailable")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(settings, "VOICE_ENABLED", True)
    ws.rebuild_voice_runtime()
    assert not isinstance(ws.device_gateway._voice_runtime.asr, DashScopeAsrProvider)

    await prepare_settings_runtime()

    assert isinstance(ws.device_gateway._voice_runtime.asr, DashScopeAsrProvider)
