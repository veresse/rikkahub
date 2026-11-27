# Phase 4D 实施计划：设置 / 备份 / 素材包

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 companion App 里交付可用的设置（Key 只写）、会话/Token 管理、备份导入导出、素材包管理。

**Architecture:** 服务端沿用 FastAPI + SQLAlchemy/Alembic，新增 `app_settings` 与 `asset_packages` 两张表、`/api/settings` 与 `/api/assets` 两组路由，并把 DB 覆盖值在启动/写入后回写到现有 `settings` 单例（消费方都是调用时读取）。App 沿用单模块 MVVM + 手写 `AppContainer` + 手写导航栈，新增 `Settings/Backup/Assets` 三个路由，上传/下载全部流式。

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, cryptography(AES-GCM), pytest, Kotlin/Compose, Retrofit/OkHttp, kotlinx.serialization, JUnit4 + coroutines-test。

**Spec:** `docs/superpowers/plans/2026-11-27-companion-app-phase4d-design.md`

## Global Constraints

- 绝不读取/打印/提交根 `local.properties`；不记录真实 API Key、备份正文、Wi-Fi/管理员密码、私聊内容。
- API Key 只写不读：`GET /api/settings` 不返回明文、掩码或 Key 片段；App 内任何 `UiState`/事件文案/日志都不得包含 Key。
- 服务端密钥落库必须加密（AES-GCM，密钥由 `SECRET_KEY` 经 HKDF 派生）；解密失败按未配置处理，不崩溃、不回显。
- 上传/导出流式：备份 ZIP 不写 App 缓存；导出不整包进内存；服务端上传按 `MAX_UPLOAD_SIZE_BYTES` 计数。
- 禁止自定义 `X509TrustManager`/`HostnameVerifier`/跳过证书校验；禁止 WebView；禁止把模型/ASR/TTS/搜索 Key 放进 App。
- 离线时所有写按钮禁用；写操作在途时拦截返回（沿用 4A–4C 语义）。
- 新 `rememberSaveable` 只能追加在 `AppNav.kt` 现有两个之后。
- `AppRoute` 只在末尾追加；`AppRouteSaver` 的旧路由编码不得改变。
- 素材帧固定 `240x240`、PNG 8-bit、单帧 ≤512 KiB、帧数 1–240、解压总量 ≤20 MiB。
- 测试：App 基线 **24 suites / 223 tests**，服务端基线 **427 passed**，只增不减。
- 服务端测试命令（`companion/server` 下）：`$env:PYTHONPATH="..\.."; uv run --python 3.12 --with-requirements requirements.txt python -m pytest -q`
- App 测试命令（`companion/app` 下）：`.\gradlew.bat testDebugUnitTest assembleDebug assembleRelease --console=plain`
- PowerShell 不支持 `&&`；含中文的提交信息用 UTF-8 文件 + `git commit -F`。
- 每个 Task 结束提交一次；日志文件最后统一补充，但每完成一个 Task 先记录要点到 `docs/语音机器人需求文档/v2_migration_log/31_phase4_4d_settings_assets_backup.md`。

## Review Focus

1. **Key 泄漏到 UI/日志**：设置页任何状态、Snackbar、异常文案出现输入值即失败 → Task 4 断言 `state`/事件文本不含 Key。
2. **上传边界**：超大/损坏 ZIP 不应把内存打满，也不应破坏当前生效素材包 → Task 6（备份 413）与 Task 10（素材校验失败保持 active 不变）。
3. **预览复用/过期**：备份预览被另一个 Token 或过期后仍能确认 → Task 6 断言 owner 不匹配/过期都 400。
4. **伪 PNG 通过校验**：只查扩展名会让坏图进设备 → Task 10 断言 IHDR 尺寸/位深/色型/内容类型。
5. **撤销当前 Token**：撤销后仍停留在已登录界面 → Task 4 断言当前 Token 撤销后清本地会话。
6. **离线/在途**：断网或上传中仍可点保存/导入 → Task 5/8/13 的 `busy` 与 `isOnline` 断言。

---

## Task 1: 服务端设置存储（加密 + 启动加载）

**Files:**
- Create: `companion/server/app/models/app_setting.py`
- Create: `companion/server/app/services/secret_box.py`
- Create: `companion/server/app/services/app_settings.py`
- Create: `companion/server/migrations/versions/0004_app_settings.py`
- Modify: `companion/server/app/config.py`（在语音配置块后加 `SEARCH_ENABLED: bool = True`）
- Modify: `companion/server/app/main.py`（`lifespan` 里 `init_db()` 之后加载覆盖）
- Modify: `companion/server/requirements.txt`（加 `cryptography>=43.0.0`）
- Modify: `companion/server/tests/conftest.py`（加全局 autouse 清理：快照 settings 字段、每例后清 `app_settings` 行并 `reset_cache`）
- Test: `companion/server/tests/test_app_settings.py`

**Interfaces:**
- Consumes: `companion.server.app.config.settings`、`companion.server.app.database.{Base, AsyncSessionLocal}`。
- Produces:
  - `secret_box.encrypt_secret(plaintext: str) -> str`（`"v1:" + base64(nonce‖ciphertext)`）
  - `secret_box.decrypt_secret(value: str) -> str`（失败抛 `ValueError`）
  - `AppSettingsService.load_and_apply(db) -> None`
  - `AppSettingsService.update(db, *, plain: dict[str, str] | None = None, secrets: dict[str, str | None] | None = None) -> None`
  - `AppSettingsService.view(db) -> dict`
  - `AppSettingsService.effective(key: str) -> str`
  - `AppSettingsService.reset_cache() -> None`（仅测试/启动用）
  - `SECRET_KEYS`、`PLAIN_KEYS`、`FIELD_BY_KEY: dict[str, tuple[str, type]]`

- [ ] **Step 1: 写失败测试**

`companion/server/tests/test_app_settings.py`：

```python
import pytest
from sqlalchemy import delete, select, text

from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.app_setting import AppSettingModel
from companion.server.app.services import app_settings as mod
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


async def test_update_applies_values_immediately():
    async with AsyncSessionLocal() as db:
        await AppSettingsService.update(
            db,
            plain={"deepseek_base_url": "https://example.test", "voice_provider": "dashscope"},
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


async def test_clear_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-key")
    async with AsyncSessionLocal() as db:
        await AppSettingsService.update(db, secrets={"deepseek_api_key": None})
        view = await AppSettingsService.view(db)
        assert view["deepseek"]["source"] == "environment"
        assert view["deepseek"]["configured"] is True


async def test_corrupt_ciphertext_is_tolerated():
    async with AsyncSessionLocal() as db:
        db.add(AppSettingModel(key="deepseek_api_key", value="v1:garbage", is_secret=True))
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `$env:PYTHONPATH="..\.."; uv run --python 3.12 --with-requirements requirements.txt python -m pytest tests/test_app_settings.py -q`
Expected: FAIL / collection error（`No module named app_setting`）。

- [ ] **Step 3: 实现**

`models/app_setting.py`：`key: Mapped[str] = mapped_column(String(64), primary_key=True)`、`value: Mapped[str] = mapped_column(Text, default="")`、`is_secret: Mapped[bool]`、`updated_at: Mapped[int]`（ms）。

`services/secret_box.py`：用 `hashlib`/`cryptography`：

```python
def _aead() -> AESGCM:
    key = HKDF(algorithm=SHA256(), length=32, salt=b"companion-settings-v1",
               info=b"aes-gcm").derive(settings.SECRET_KEY.encode("utf-8"))
    return AESGCM(key)

def encrypt_secret(plaintext: str) -> str:
    nonce = os.urandom(12)
    return "v1:" + base64.urlsafe_b64encode(nonce + _aead().encrypt(nonce, plaintext.encode(), None)).decode()

def decrypt_secret(value: str) -> str:
    # 非 v1: 前缀、base64 失败、GCM 认证失败都抛 ValueError（不带原文）
```

`services/app_settings.py`：

```python
SECRET_KEYS = ("deepseek_api_key", "dashscope_api_key")
PLAIN_KEYS = ("deepseek_base_url", "deepseek_model", "voice_enabled", "voice_provider",
              "dashscope_region", "dashscope_asr_model", "dashscope_tts_model",
              "dashscope_tts_voice", "search_enabled")
FIELD_BY_KEY = {
    "deepseek_api_key": ("DEEPSEEK_API_KEY", str),
    "dashscope_api_key": ("DASHSCOPE_API_KEY", str),
    "deepseek_base_url": ("DEEPSEEK_BASE_URL", str),
    "deepseek_model": ("DEEPSEEK_DEFAULT_MODEL", str),
    "voice_enabled": ("VOICE_ENABLED", "bool"),
    "voice_provider": ("VOICE_PROVIDER", str),
    "dashscope_region": ("DASHSCOPE_REGION", str),
    "dashscope_asr_model": ("DASHSCOPE_ASR_MODEL", str),
    "dashscope_tts_model": ("DASHSCOPE_TTS_MODEL", str),
    "dashscope_tts_voice": ("DASHSCOPE_TTS_VOICE", str),
    "search_enabled": ("SEARCH_ENABLED", "bool"),
}
```

- `_cache: dict[str, str]` 存“DB 覆盖后的有效值”；`_broken: set[str]` 记解密失败键。
- `load_and_apply`：一次 select 全表；secret 尝试 `decrypt_secret`，失败加入 `_broken` 且跳过；随后 `_apply_all()`。
- `_apply_all()`：对 `_cache` 每项 `setattr(settings, field, bool(v == "true") if kind=="bool" else v)`。
- `update`：plain 值直接写 row；secrets 传 `None`/`""` 删除 row，否则加密写 row；`await db.commit()`；再 `load_and_apply(db)`。
- `view`：读行 key 集合与 `_broken`；`source = "database" if key in rows else ("environment" if key.upper() in os.environ else "unset")`；`configured = bool(effective(key))`；`voice.effective/reason` 按设计 §2.3 推导；返回 dict（无任何 Key 文本）。
- `effective(key)`：`_cache.get(key)`，否则 `str(getattr(settings, FIELD_BY_KEY[key][0], "") or "")`。

`migrations/versions/0004_app_settings.py`：`revision="0004_app_settings"`、`down_revision="0003_assistant_model_search"`，`op.create_table("app_settings", ...)`，downgrade `op.drop_table("app_settings")`。

`main.py` lifespan：

```python
await init_db()
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.services.app_settings import AppSettingsService
async with AsyncSessionLocal() as session:
    await AppSettingsService.load_and_apply(session)
```

- [ ] **Step 4: 跑测试确认通过**

Run 同上。Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add companion/server/app/models/app_setting.py companion/server/app/services/secret_box.py companion/server/app/services/app_settings.py companion/server/migrations/versions/0004_app_settings.py companion/server/app/config.py companion/server/app/main.py companion/server/requirements.txt companion/server/tests/test_app_settings.py
git commit -m "feat(companion-server): store app settings with encrypted secrets"
```

---

## Task 2: 设置 API + 搜索总开关

**Files:**
- Create: `companion/server/app/routers/settings.py`
- Modify: `companion/server/app/main.py`（注册路由）
- Modify: `companion/server/app/services/orchestrator.py:549-565`（搜索前判断 `settings.SEARCH_ENABLED`）
- Test: `companion/server/tests/test_settings_api.py`
- Test: `companion/server/tests/test_chat_model_search.py`（追加一条）

**Interfaces:**
- Consumes: Task 1 的 `AppSettingsService`、`SECRET_KEYS`、`PLAIN_KEYS`。
- Produces: `GET/PUT /api/settings`、`PUT /api/settings/secrets`、`DELETE /api/settings/secrets/{key}`（响应均为 Task 1 的 `view` 结构）。

- [ ] **Step 1: 写失败测试**

`tests/test_settings_api.py`：

```python
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
    assert resp.json()["deepseek"]["source"] == "database"


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
    await client.put("/api/settings/secrets", headers=auth_headers,
                     json={"dashscope_api_key": "sk-x"})
    await client.put("/api/settings/secrets", headers=auth_headers,
                     json={"dashscope_api_key": ""})
    got = (await client.get("/api/settings", headers=auth_headers)).json()
    assert got["dashscope"]["configured"] is False

    await client.put("/api/settings/secrets", headers=auth_headers,
                     json={"dashscope_api_key": "sk-y"})
    resp = await client.delete("/api/settings/secrets/dashscope_api_key", headers=auth_headers)
    assert resp.status_code == 200
    got = (await client.get("/api/settings", headers=auth_headers)).json()
    assert got["dashscope"]["configured"] is False


async def test_unknown_secret_key_is_rejected(client, auth_headers):
    resp = await client.delete("/api/settings/secrets/not_a_key", headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unknown_setting"


async def test_invalid_base_url_and_provider_are_rejected(client, auth_headers):
    bad_url = await client.put("/api/settings", headers=auth_headers,
                               json={"deepseek_base_url": "ftp://example.test"})
    assert bad_url.status_code == 400
    assert bad_url.json()["detail"] == "invalid_base_url"

    bad_provider = await client.put("/api/settings", headers=auth_headers,
                                    json={"voice_provider": "openai"})
    assert bad_provider.status_code == 400
    assert bad_provider.json()["detail"] == "invalid_voice_provider"
```

`test_chat_model_search.py` 追加：

```python
async def test_global_search_switch_off_skips_provider(session, monkeypatch):
    await seed(session, mode="on", enable_web_search=True)
    calls: list[str] = []

    async def fake_search(query: str):
        calls.append(query)
        return []

    monkeypatch.setattr(search_module.SearchProvider, "search", fake_search)
    monkeypatch.setattr(orchestrator_module.settings, "SEARCH_ENABLED", False)

    _ = [e async for e in ChatOrchestrator.stream_chat(
        session, CONVERSATION_ID, "讲个故事", client=RecordingClient())]

    assert calls == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `... python -m pytest tests/test_settings_api.py tests/test_chat_model_search.py -q`
Expected: `/api/settings` 404 / 搜索仍被调用。

- [ ] **Step 3: 实现**

`routers/settings.py`：

```python
router = APIRouter(prefix="/settings", tags=["settings"])

class SettingsUpdateBody(BaseModel):
    deepseek_base_url: str | None = None
    deepseek_model: str | None = None
    voice_enabled: bool | None = None
    voice_provider: str | None = None
    dashscope_region: str | None = None
    dashscope_asr_model: str | None = None
    dashscope_tts_model: str | None = None
    dashscope_tts_voice: str | None = None
    search_enabled: bool | None = None

class SecretsUpdateBody(BaseModel):
    deepseek_api_key: str | None = None
    dashscope_api_key: str | None = None
```

- 校验函数 `_validate(body) -> dict[str, str]`：字符串 `strip()` 后非空、长度 1–200（模型/音色/区域 1–80）；`voice_provider ∈ {"unavailable","dashscope"}`；base_url 用 `urllib.parse.urlsplit`，`scheme ∈ {http,https}`、`netloc` 非空且不含 `@`；失败抛 `HTTPException(400, "invalid_<field>")`。布尔转 `"true"/"false"`。
- `PUT /settings`：`await AppSettingsService.update(db, plain=_validate(req))`，返回 `await AppSettingsService.view(db)`。
- `PUT /settings/secrets`：每个非 `None` 值：`strip()` 后长度 0（清除）或 1–512（写入），超长 400 `invalid_secret`；调用 `update(db, secrets=...)`。
- `DELETE /settings/secrets/{key}`：`key not in SECRET_KEYS` → 400 `unknown_setting`；否则 `update(db, secrets={key: None})`。
- 路由在 `main.py` 用 `app_protected` 注册。
- `orchestrator.py` 搜索分支：`if settings.SEARCH_ENABLED and mode_requires_search:`（保持原三态判定不变）。

- [ ] **Step 4: 跑测试确认通过**

Run: `... python -m pytest tests/test_settings_api.py tests/test_chat_model_search.py tests/test_app_settings.py -q`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add companion/server/app/routers/settings.py companion/server/app/main.py companion/server/app/services/orchestrator.py companion/server/tests/test_settings_api.py companion/server/tests/test_chat_model_search.py
git commit -m "feat(companion-server): expose app settings api and search gate"
```

---

## Task 3: App 设置/Token 网络层

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/SettingsDtos.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/AuthTokenDtos.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/SettingsRepository.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/TokenRepository.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/data/api/CompanionApi.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/data/api/ErrorMapper.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/data/repo/SettingsRepositoryTest.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/data/repo/TokenRepositoryTest.kt`

**Interfaces:**
- Produces:
  - `SettingsRepository { suspend fun load(): AppResult<SettingsDto>; suspend fun update(body: SettingsUpdateBody): AppResult<SettingsDto>; suspend fun updateSecrets(body: SecretsUpdateBody): AppResult<SettingsDto>; suspend fun clearSecret(key: String): AppResult<SettingsDto> }`
  - `TokenRepository { suspend fun list(): AppResult<List<AuthTokenDto>>; suspend fun revoke(tokenId: String): AppResult<Unit> }`
  - `AppContainer.settingsRepository` / `AppContainer.tokenRepository`
  - `SettingsDto(deepseek: DeepseekSettingsDto, dashscope: DashscopeSettingsDto, voice: VoiceSettingsDto, search: SearchSettingsDto)`
  - `AuthTokenDto(id, username, deviceName, createdAt, lastUsedAt, expiresAt, revokedAt)`

- [ ] **Step 1: 写失败测试**

`SettingsRepositoryTest.kt`（照 `DeviceRepositoryTest` 的 `manager()` 写法）：

```kotlin
@Test fun `load parses view and never contains a key`() = runTest {
    TestHttpServer().use { server ->
        server.enqueue(200, """{"deepseek":{"configured":true,"source":"database",
            "base_url":"https://api.deepseek.com","model":"deepseek-chat"},
            "dashscope":{"configured":false,"source":"unset","region":"cn-beijing",
            "asr_model":"fun-asr-realtime","tts_model":"m","tts_voice":"cherry"},
            "voice":{"enabled":true,"provider":"unavailable","effective":"unavailable",
            "reason":"dashscope_key_missing"},
            "search":{"enabled":true,"provider":"builtin_bing","requires_key":false}}""")
        val session = manager(server.baseUrl)
        val repo = HttpSettingsRepository(RetrofitApiFactory({ session.state.value }, {}), session)

        val dto = (repo.load() as AppResult.Ok).value

        assertTrue(dto.deepseek.configured)
        assertEquals("database", dto.deepseek.source)
        assertEquals("dashscope_key_missing", dto.voice.reason)
        assertEquals("builtin_bing", dto.search.provider)
        assertEquals("/api/settings", server.lastRequestUri)
    }
}

@Test fun `save update sends only plain fields`() = runTest {
    // enqueue 200 同款视图；断言 lastRequestBody 含 deepseek_model，不含 api_key
}

@Test fun `secret update never echoes the value`() = runTest {
    // enqueue 200 视图；assertFalse(response.toString().contains("sk-test"))
}

@Test fun `clear secret hits delete path`() = runTest {
    // DELETE /api/settings/secrets/dashscope_api_key
}

@Test fun `file_too_large maps to readable message`() = runTest {
    // 413 {"detail":"file_too_large"} -> "文件超过服务器允许的大小"
}
```

`TokenRepositoryTest.kt`：list 解析、revoke 路径/方法、`preview_expired` 已有映射不必重复。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd companion\app; .\gradlew.bat testDebugUnitTest --tests "*SettingsRepositoryTest" --console=plain`
Expected: 编译失败（类不存在）。

- [ ] **Step 3: 实现**

DTO 用 `@Serializable` + `@SerialName`，所有字段带默认值；`SettingsDto` 不含任何 key 字段。

`CompanionApi` 追加：

```kotlin
@GET("api/settings") suspend fun getSettings(): SettingsDto
@PUT("api/settings") suspend fun updateSettings(@Body body: SettingsUpdateBody): SettingsDto
@PUT("api/settings/secrets") suspend fun updateSecrets(@Body body: SecretsUpdateBody): SettingsDto
@DELETE("api/settings/secrets/{key}") suspend fun clearSecret(@Path("key") key: String): SettingsDto
@GET("api/auth/tokens") suspend fun listTokens(): List<AuthTokenDto>
@POST("api/auth/tokens/{id}/revoke") suspend fun revokeToken(@Path("id") id: String): StatusDto
```

`HttpSettingsRepository` / `HttpTokenRepository` 均 `SessionApi(apiFactory, sessionManager)`，用 `call { }` 包装。

`ErrorMapper.messages` 追加：`file_too_large` → `"文件超过服务器允许的大小"`、`invalid_base_url` → `"服务器地址不合法"`、`invalid_secret` → `"Key 格式不合法"`、`unknown_setting` → `"未知的设置项"`、`invalid_voice_provider` → `"不支持该语音提供方"`、`no_previous_package` → `"没有可回滚的上一版素材"`、`invalid_package` → `"素材包校验失败"`、`upload_expired` → `"上传已过期，请重新选择文件"`、`asset_frame_not_found` → `"素材帧不存在"`。

`AppContainer` 加两个属性，紧跟在 `deviceRepository` 之后。

- [ ] **Step 4: 跑测试确认通过**

Run: `.\gradlew.bat testDebugUnitTest --tests "*SettingsRepositoryTest" --tests "*TokenRepositoryTest" --console=plain`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add companion/app/src/main/java/me/rerere/companion/data companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt companion/app/src/test/java/me/rerere/companion/data/repo
git commit -m "feat(companion-app): add settings and token repositories"
```

---

## Task 4: SettingsViewModel（Key 只在内存）

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/feature/settings/SettingsViewModel.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/testutil/FakeSettingsRepository.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/testutil/FakeTokenRepository.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/feature/settings/SettingsViewModelTest.kt`

**Interfaces:**
- Consumes: Task 3 的仓库、`SessionManager`、`isOnline: () -> Boolean`。
- Produces:
  - `SettingsViewModel(settingsRepository, tokenRepository, sessionManager, isOnline)`
  - `SettingsUiState`（含表单、状态、`tokens`、`deepseekKeyDraft`、`dashscopeKeyDraft`）
  - `SettingsEvent.Message(text)`、`SettingsEvent.LoggedOut`
  - 方法：`load()`, `setDeepseekBaseUrl`, `setDeepseekModel`, `setDeepseekKeyDraft`,
    `saveDeepseek()`, `clearDeepseekKey()`, `setDashscope*`, `setDashscopeKeyDraft`,
    `saveVoice()`, `clearDashscopeKey()`, `setSearchEnabled(Boolean)`,
    `refreshTokens()`, `revokeToken(id)`, `dismissError()`

- [ ] **Step 1: 写失败测试**（`MainDispatcherRule` + `runTest` + fake 仓库）

```kotlin
@Test fun `load maps view into form state`()
@Test fun `save deepseek sends plain fields and keeps key out of state`()
// 断言 lastUpdateBody.deepseekApiKey == null；state.deepseekKeyDraft == ""
@Test fun `saving a secret clears the draft and never emits it`()
// fake 记录 updateSecrets 入参；所有 event 文本 + state.toString() 不含 "sk-test"
@Test fun `offline blocks every write`()
@Test fun `revoking the current token clears the session`()
// fake revoke 成功 -> sessionManager.state == null，event LoggedOut
@Test fun `revoking another token refreshes the list`()
@Test fun `search toggle sends only search_enabled`()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\gradlew.bat testDebugUnitTest --tests "*SettingsViewModelTest" --console=plain`

- [ ] **Step 3: 实现**

- `busy` 用单个 `saving: Boolean`；所有写方法开头 `if (!isOnline()) { emitError("当前离线，无法保存"); return }` 和 `if (state.value.saving) return`。
- `load()`：并行 `settingsRepository.load()` 与 `tokenRepository.list()`；设置失败进 `error`，Token 失败进 `tokensError`（不阻塞设置可用）。
- `saveSecret` 成功：清对应 draft、用响应刷新 `configured/source`、`events.emit(Message("已保存"))`；失败：`events.emit(Message(error.message))`，draft 保留由用户重试。
- `revokeToken(id)`：成功后若 `id == sessionManager.state.value?.tokenId` → `sessionManager.clear()` + `LoggedOut`；否则 `refreshTokens()`。
- 任何 `UiState` 更新都不得把 Key 写进 `error`/事件；`clearDeepseekKey()` 走确认框（UI 层）。

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: 提交**

```bash
git add companion/app/src/main/java/me/rerere/companion/feature/settings companion/app/src/test/java/me/rerere/companion/testutil/FakeSettingsRepository.kt companion/app/src/test/java/me/rerere/companion/testutil/FakeTokenRepository.kt companion/app/src/test/java/me/rerere/companion/feature/settings
git commit -m "feat(companion-app): add settings view model"
```

---

## Task 5: 设置页 + 路由 + 入口

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/feature/settings/SettingsScreen.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/ui/navigation/AppRoute.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/ui/navigation/AppRouteSaver.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/ui/navigation/AppNav.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/feature/assistants/AssistantsScreen.kt`（`onSettings` 替换 `onLogout`）
- Test: `companion/app/src/test/java/me/rerere/companion/feature/settings/SettingsScreenGateTest.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/ui/navigation/AppRouteSaverTest.kt`（如已存在则追加）

**Interfaces:**
- Consumes: Task 4 的 VM。
- Produces: `AppRoute.Settings`；`settingsActionsEnabled(online: Boolean, saving: Boolean): Boolean`；`AssistantsScreen(..., onSettings: () -> Unit)`。

- [ ] **Step 1: 写失败测试**

```kotlin
@Test fun `settings actions require online and idle`() {
    assertFalse(settingsActionsEnabled(online = false, saving = false))
    assertFalse(settingsActionsEnabled(online = true, saving = true))
    assertTrue(settingsActionsEnabled(online = true, saving = false))
}

@Test fun `settings route survives encode round trip`() {
    assertEquals(AppRoute.Settings, decodeRouteForTest(encodeRouteForTest(AppRoute.Settings)))
}
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

- `AppRoute` 末尾追加 `data object Settings : AppRoute`；saver 加 `"settings"` 的 encode/decode（`a/b/c` 全 null），旧分支不动。
- `SettingsScreen`：`Scaffold` + `TopAppBar("设置")` + 离线横幅 + `LazyColumn`；分组用 `Surface(color = surfaceContainerHigh, shape = shapes.large)`；Key 输入框 `PasswordVisualTransformation()`、`KeyboardType.Password`，Header 写明“只写，不回显”。
- 保存/清除/撤销按钮统一 `enabled = settingsActionsEnabled(online, state.saving || state.revokingId != null)`。
- 清除 Key、撤销 Token、退出登录都用 `AlertDialog` 二次确认；Token 列表项显示设备名/创建时间/“当前”。
- `AppNav`：新增 `AppRoute.Settings` 分支，`viewModelStoreOwner = navigationViewModel.ownerFor(route)`；`BackHandler` 在 `saving/revokingId != null` 时拦截；`LoggedOut` 事件不需要导航（会话清空自动回登录页）。
- `AssistantsScreen` 顶栏：`设备` / `设置` / `刷新`；`AppNav` 里把 `onLogout` 换成 `pushRoute(AppRoute.Settings)`。

- [ ] **Step 4: 跑测试 + 构建**

Run: `.\gradlew.bat testDebugUnitTest --tests "*SettingsScreenGateTest" --tests "*AppRouteSaverTest" --console=plain`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add companion/app/src/main companion/app/src/test/java/me/rerere/companion/feature/settings companion/app/src/test/java/me/rerere/companion/ui/navigation
git commit -m "feat(companion-app): add settings screen and route"
```

---

## Task 6: 服务端备份加固

**Files:**
- Modify: `companion/server/app/importer/service.py`
- Modify: `companion/server/app/routers/import_export.py`
- Test: `companion/server/tests/test_backup_api.py`

**Interfaces:**
- Produces:
  - `ImportService.generate_preview(zip_path: Path, owner_token_id: str) -> dict`（缓存 `{data, owner, expires_at}`，TTL 600s，上限 8）
  - `ImportService.execute_import(preview_id: str, owner_token_id: str, db) -> dict`（owner/过期校验，失败 `ValueError("preview_expired")`）
  - 预览响应新增 `owner` 不返回（仅内部）。

- [ ] **Step 1: 写失败测试**

```python
async def test_preview_rejects_oversized_upload(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 10)
    files = {"file": ("b.zip", io.BytesIO(b"x" * 100), "application/zip")}
    resp = await client.post("/api/backup/preview", headers=auth_headers, files=files)
    assert resp.status_code == 413
    assert resp.json()["detail"] == "file_too_large"


async def test_preview_is_owned_and_expires(client, auth_headers, monkeypatch):
    # 1) 用真实最小 ZIP（settings.json + rikka_hub.db）拿到 preview_id
    # 2) 直接改 service 缓存里该项的 owner/expires_at
    # 3) confirm 分别断言 400 preview_expired


async def test_export_round_trip_and_cleanup(client, auth_headers):
    resp = await client.get("/api/backup/export", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    assert {"settings.json", "rikka_hub.db"} <= set(z.namelist())
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

- `preview_backup(file, auth: AuthContext = Depends(require_app_token))`：分块复制并计数，超 `settings.MAX_UPLOAD_SIZE_BYTES` → `HTTPException(413, "file_too_large")`；`ImportService.generate_preview(tmp_path, auth.token_id)`。
- `confirm_import(req, auth, db)`：捕获 `ValueError` → `HTTPException(400, "preview_expired")`。
- `ImportService` 缓存项加 `owner`、`expires_at = time.time() + 600`；`_prune()` 删除过期项并保留最近 8 条（`sorted(..., key=expires_at)[-8:]`）。
- 导出：`FileResponse(..., filename=f"rikkahub_backup_{date}.zip", background=BackgroundTask(shutil.rmtree, temp_dir, True))`。
- 不记录任何正文；异常文案不带解析内容（`str(e)` 保持现状但只在 400 `detail` 里，不含密钥）。

- [ ] **Step 4: 跑测试**

Run: `... python -m pytest tests/test_backup_api.py tests/test_importer.py -q`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add companion/server/app/importer/service.py companion/server/app/routers/import_export.py companion/server/tests/test_backup_api.py
git commit -m "feat(companion-server): harden backup preview and export"
```

---

## Task 7: App 备份网络层（流式）

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/BackupDtos.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/StreamRequestBody.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/BackupRepository.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/data/api/CompanionApi.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt`
- Modify: `companion/app/src/test/java/me/rerere/companion/testutil/TestHttpServer.kt`（支持二进制响应 + 记录原始请求体）
- Test: `companion/app/src/test/java/me/rerere/companion/data/repo/BackupRepositoryTest.kt`

**Interfaces:**
- Produces:
  - `BackupRepository { suspend fun preview(stream: InputStream, filename: String): AppResult<BackupPreviewDto>; suspend fun confirm(previewId: String): AppResult<BackupResultDto>; suspend fun exportTo(dest: OutputStream): AppResult<Long> }`
  - `AppContainer.backupRepository`
  - `TestHttpServer.enqueueBytes(status: Int, bytes: ByteArray, contentType: String = "application/octet-stream")`、`lastRequestBodyBytes: ByteArray`

- [ ] **Step 1: 写失败测试**

```kotlin
@Test fun `preview streams the zip as multipart`() = runTest {
    TestHttpServer().use { server ->
        server.enqueue(200, """{"preview_id":"p1","summary":{"assistant_count":2,
            "conversation_count":3,"message_node_count":9,"total_message_versions":11,
            "memory_count":1,"unsupported_features":[]},
            "assistants_sample":["A"],"conversations_sample":["C"]}""")
        val session = manager(server.baseUrl)
        val repo = HttpBackupRepository(RetrofitApiFactory({ session.state.value }, {}), session)

        val preview = (repo.preview(ByteArrayInputStream("PK\u0003\u0004abc".toByteArray()), "backup.zip")
            as AppResult.Ok).value

        assertEquals("p1", preview.previewId)
        assertEquals(11, preview.summary.totalMessageVersions)
        assertTrue(server.lastRequestBody.contains("filename=\"backup.zip\""))
        assertTrue(server.lastRequestBody.contains("PK"))
        assertEquals("/api/backup/preview", server.lastRequestUri)
    }
}

@Test fun `export copies bytes with a single buffer loop`() = runTest {
    TestHttpServer().use { server ->
        server.enqueueBytes(200, ByteArray(20_000) { it.toByte() })
        val session = manager(server.baseUrl)
        val repo = HttpBackupRepository(RetrofitApiFactory({ session.state.value }, {}), session)
        val out = ByteArrayOutputStream()

        val result = repo.exportTo(out)

        assertEquals(20_000L, (result as AppResult.Ok).value)
        assertEquals(20_000, out.size())
        assertEquals("/api/backup/export", server.lastRequestUri)
    }
}

@Test fun `preview expiry maps to readable message`() = runTest {
    // 400 {"detail":"preview_expired"} -> "预览已过期，请重新选择文件"
}
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`StreamRequestBody`：

```kotlin
class StreamRequestBody(
    private val open: () -> InputStream,
    private val length: Long = -1L,
) : RequestBody() {
    override fun contentType() = "application/zip".toMediaType()
    override fun contentLength() = length
    override fun isOneShot() = true
    override fun writeTo(sink: BufferedSink) {
        open().use { input -> sink.writeAll(input.source()) }
    }
}
```

`BackupRepository`：

- `preview`：`MultipartBody.Part.createFormData("file", filename, StreamRequestBody({ stream }, -1))` → `it.previewBackup(part)`。
- `confirm`：`it.confirmBackup(BackupConfirmBody(previewId))`。
- `exportTo`：在 `call { }` 内 `it.exportBackup().use { body -> body.byteStream().use { input -> input.copyTo(dest, 8192) } }` 返回累计字节数；`dest` 由调用方负责关闭（VM 用 `use`）。

`CompanionApi` 追加 `@Multipart @POST("api/backup/preview")`、`@POST("api/backup/confirm")`、`@Streaming @GET("api/backup/export")`。

`TestHttpServer`：把 handler 改写成读取 `lastRequestBodyBytes`，按 `Stub.bytes + contentType` 写响应；`enqueue` 保持转发到 `enqueueBytes(text.toByteArray(), "application/json")`。

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: 提交**

```bash
git add companion/app/src/main/java/me/rerere/companion/data companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt companion/app/src/test/java/me/rerere/companion/testutil/TestHttpServer.kt companion/app/src/test/java/me/rerere/companion/data/repo/BackupRepositoryTest.kt
git commit -m "feat(companion-app): add streaming backup repository"
```

---

## Task 8: BackupViewModel

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/feature/backup/BackupViewModel.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/testutil/FakeBackupRepository.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/feature/backup/BackupViewModelTest.kt`

**Interfaces:**
- Produces:
  - `BackupViewModel(repository, isOnline)`
  - `BackupUiState(previewing, preview, importing, result, exporting, exportedBytes, error)`
  - `fun preview(open: () -> InputStream?)`、`fun confirm()`、`fun exportTo(open: () -> OutputStream?)`、`fun dismissError()`
  - `BackupEvent.Message(text)`

- [ ] **Step 1: 写失败测试**

```kotlin
@Test fun `preview then confirm reports imported counts`()
@Test fun `expired preview clears the stale preview`()
@Test fun `network failure while confirming keeps the preview for retry`()
@Test fun `export writes to the provided sink and reports size`()
@Test fun `busy blocks duplicate imports`()
@Test fun `offline blocks preview confirm and export`()
```

- [ ] **Step 2–4: 红 → 实现 → 绿**

实现要点：
- `busy = previewing || importing || exporting`；所有入口先查 `isOnline()` 与 `busy`。
- `preview()` 中 `open()` 返回 null → `error = "无法读取所选文件"`；流用 `use` 关闭。
- `AppError.Network` 时保留 `preview`；`AppError.Http` 时清空并提示（含 `preview_expired`）。
- `confirm()` 成功：`result = dto`、`preview = null`、事件 `"导入完成：角色 X / 会话 Y"`。
- `exportTo()` 成功：`exportedBytes`、事件 `"已导出 3.2 MB"`；sink 用 `use`。

- [ ] **Step 5: 提交**

```bash
git add companion/app/src/main/java/me/rerere/companion/feature/backup companion/app/src/test/java/me/rerere/companion/testutil/FakeBackupRepository.kt companion/app/src/test/java/me/rerere/companion/feature/backup
git commit -m "feat(companion-app): add backup view model"
```

---

## Task 9: 备份页 + 路由 + 设置入口

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/feature/backup/BackupScreen.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/ui/navigation/{AppRoute,AppRouteSaver,AppNav}.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/feature/settings/SettingsScreen.kt`（加 `onOpenBackup` 行）
- Test: `companion/app/src/test/java/me/rerere/companion/ui/navigation/AppRouteSaverTest.kt`（追加 round-trip）

**Interfaces:**
- Produces: `AppRoute.Backup`；`BackupScreen(state, events, online, onPickFile, onExport, onConfirm, onDismissError, onBack)`；`SettingsScreen(..., onOpenBackup: () -> Unit)`。

- [ ] **Step 1: 测试**：`AppRoute.Backup` 编码/恢复；`backupActionsEnabled(online, busy)` 纯函数（离线或在途为 false）。
- [ ] **Step 2: 跑失败**。
- [ ] **Step 3: 实现**

- Screen 内两个 launcher：
  `rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument())` → `vm.preview { context.contentResolver.openInputStream(uri) }`；
  `CreateDocument("application/zip")` → `vm.exportTo { context.contentResolver.openOutputStream(uri) }`（文件名 `rikkahub_backup.zip`）。
- 预览摘要卡展示 6 个计数 + 样例标题；确认按钮二次确认；结果卡展示导入计数，并提示“返回后角色列表会自动刷新”。
- `AppNav` 分支：从备份页返回时调用 `assistantsViewModel.refresh()`（在 `popRoute()` 后或 `LaunchedEffect` 里）。
- 设置页加“备份与恢复”行 → `pushRoute(AppRoute.Backup)`。

- [ ] **Step 4: 跑测试 + 构建**
- [ ] **Step 5: 提交**

```bash
git add companion/app/src/main companion/app/src/test/java/me/rerere/companion/ui/navigation
git commit -m "feat(companion-app): add backup screen"
```

---

## Task 10: 服务端素材包核心（表 + 校验 + 存储）

**Files:**
- Create: `companion/server/app/models/asset_package.py`
- Create: `companion/server/migrations/versions/0005_asset_packages.py`
- Create: `companion/server/app/assetpacks/__init__.py`
- Create: `companion/server/app/assetpacks/validator.py`
- Create: `companion/server/app/assetpacks/service.py`
- Create: `companion/server/app/assetpacks/placeholder_240.png`（240×240 PNG，由脚本生成后提交）
- Modify: `companion/server/app/config.py`（`ASSET_MAX_ARCHIVE_BYTES=10MiB`、`ASSET_MAX_EXTRACTED_BYTES=20MiB`、`ASSET_MAX_FRAMES=240`、`ASSET_MAX_FRAME_BYTES=512KiB`）
- Test: `companion/server/tests/test_asset_packs.py`

**Interfaces:**
- Produces:
  - `validate_asset_zip(zip_path: Path, dest_dir: Path) -> dict`；失败抛 `AssetValidationError(code: str)`
  - `AssetService.preview(zip_path: Path, owner: str) -> dict`
  - `AssetService.confirm(upload_id: str, owner: str, db) -> dict`
  - `AssetService.rollback(db) -> dict`
  - `AssetService.view(db) -> dict`
  - `AssetService.frame_bytes(package_id: str, index: int) -> bytes | None`
  - `AssetPackageModel`

- [ ] **Step 1: 写失败测试**

```python
def _zip(entries: dict[str, bytes]) -> bytes          # 测试辅助：内存里拼 ZIP
def _png(width=240, height=240, bit=8, color=6) -> bytes  # 12 字节签名 + IHDR + 最小 IDAT/IEND

async def test_valid_package_preview_and_confirm(db):
    # preview -> upload_id；confirm -> view.active.version == "1.0.0"

async def test_bad_dimensions_rejected_and_active_untouched(db):
    # 先启用合法包，再上传 200x240 -> AssetValidationError("invalid_frame")，view.active 不变

async def test_zip_slip_and_extra_entries_rejected()

async def test_frame_count_and_size_limits(db, monkeypatch)

async def test_rollback_restores_previous(db)

async def test_expired_upload_is_rejected(db, monkeypatch):
    # 手动把 expires_at 改成过去 -> confirm 抛 ValueError("upload_expired")

async def test_placeholder_is_valid_240_png():
    # AssetService.placeholder_bytes() 的 IHDR 宽高 == 240
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

- 迁移建表 `asset_packages`（字段见设计 §2.5），`storage_path` 存相对 `DATA_DIR/assets` 的目录名。
- `validator.py`：`zipfile.ZipFile` 遍历 `infolist()`；拒绝绝对路径/`..`/反斜杠/符号链接/目录外条目/大小写冲突/重复；只允许 `manifest.json` 与 `frames/<3+ 位数字>.png`；逐帧读入（≤512 KiB）→ `_png_header` 校验签名 + `IHDR` + 宽高 240 + bit depth 8 + color type ∈ {2,6} → sha256；整包 sha256 = 按 `frames` 顺序 `f"{path}:{hash}\n"` 拼接后 sha256。
- `service.py`：模块级 `asyncio.Lock`；内存暂存 `{upload_id: {"dir", "meta", "owner", "expires_at"}}`，TTL 600s、最多 4 个；`confirm` 把目录 `shutil.move` 到 `DATA_DIR/assets/<uuid>`，事务里旧 active→previous、旧 previous 删除（先删行再删目录）、新行 active。
- 占位图：用一次性脚本生成纯色 240×240 PNG 并提交（不要用 PowerShell 写二进制）。

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交**

```bash
git add companion/server/app/models/asset_package.py companion/server/migrations/versions/0005_asset_packages.py companion/server/app/assetpacks companion/server/app/config.py companion/server/tests/test_asset_packs.py
git commit -m "feat(companion-server): validate and store asset packages"
```

---

## Task 11: 素材包 API

**Files:**
- Create: `companion/server/app/routers/assets.py`
- Modify: `companion/server/app/main.py`
- Test: `companion/server/tests/test_assets_api.py`

**Interfaces:**
- Produces: `GET /api/assets`、`POST /api/assets/preview`、`POST /api/assets/confirm`、`POST /api/assets/rollback`、`GET /api/assets/{package_id}/frame/{index}`。

- [ ] **Step 1: 写失败测试**

```python
async def test_preview_confirm_and_fetch_frame(client, auth_headers)
async def test_invalid_package_returns_400_and_keeps_active(client, auth_headers)
async def test_rollback_without_previous_returns_409(client, auth_headers)
async def test_frame_index_out_of_range_returns_404(client, auth_headers)
async def test_fallback_frame_is_served(client, auth_headers)
async def test_upload_larger_than_limit_returns_413(client, auth_headers, monkeypatch)
```

- [ ] **Step 2–4: 红 → 实现 → 绿**

要点：`AssetValidationError` → `HTTPException(400, code)`；`ValueError("upload_expired")` → 400；`frame_bytes` 返回 `Response(content=..., media_type="image/png", headers={"Cache-Control": "private, max-age=300"})`；`package_id` 只按 DB 查（不拼路径）。

- [ ] **Step 5: 提交**

```bash
git add companion/server/app/routers/assets.py companion/server/app/main.py companion/server/tests/test_assets_api.py
git commit -m "feat(companion-server): expose asset package api"
```

---

## Task 12: App 素材包网络层

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/AssetDtos.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/AssetRepository.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/data/api/CompanionApi.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/data/repo/AssetRepositoryTest.kt`

**Interfaces:**
- Produces: `AssetRepository { load(); preview(stream, filename); confirm(uploadId); rollback(); frameBytes(packageId, index) }`、`AppContainer.assetRepository`。

- [ ] **Step 1: 写失败测试**：load 解析 active/previous/fallback；preview multipart；confirm 路径；rollback 409 文案；frameBytes 返回二进制；无 active 时 `active == null`。
- [ ] **Step 2–4: 红 → 实现 → 绿**（frame 用 `body.bytes()`，服务端已限 512 KiB；`@Streaming` 防止缓冲）。
- [ ] **Step 5: 提交**

```bash
git add companion/app/src/main/java/me/rerere/companion/data companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt companion/app/src/test/java/me/rerere/companion/data/repo/AssetRepositoryTest.kt
git commit -m "feat(companion-app): add asset repository"
```

---

## Task 13: AssetsViewModel

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/feature/assets/AssetsViewModel.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/testutil/FakeAssetRepository.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/feature/assets/AssetsViewModelTest.kt`

**Interfaces:**
- Produces: `AssetsViewModel(repository, isOnline)`、`AssetsUiState(loading, error, active, previous, fallback, preview, uploading, confirming, rollingBack, previewBytes)`、`AssetsEvent.Message`、方法 `load/preview(open)/confirm/rollback/loadPreviewImage/dismissError`。

- [ ] **Step 1–4: 红 → 实现 → 绿**

测试：`load` 映射；`preview` 后 `state.preview`；`confirm` 后 active 更新且 preview 清空；`invalid_package` 文案；`rollback`；`loadPreviewImage` 返回字节并限制 1 MiB；离线/在途拦截。

- [ ] **Step 5: 提交**

```bash
git add companion/app/src/main/java/me/rerere/companion/feature/assets companion/app/src/test/java/me/rerere/companion/testutil/FakeAssetRepository.kt companion/app/src/test/java/me/rerere/companion/feature/assets
git commit -m "feat(companion-app): add assets view model"
```

---

## Task 14: 素材包页 + 路由 + 设置入口

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/feature/assets/AssetsScreen.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/ui/navigation/{AppRoute,AppRouteSaver,AppNav}.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/feature/settings/SettingsScreen.kt`（加 `onOpenAssets` 行）
- Test: `companion/app/src/test/java/me/rerere/companion/ui/navigation/AppRouteSaverTest.kt`（追加）

- [ ] **Step 1: 测试**：`AppRoute.Assets` round-trip；`assetActionsEnabled(online, busy)`。
- [ ] **Step 2–4: 红 → 实现 → 绿**

要点：首帧 `BitmapFactory.decodeByteArray` 放在 `remember(bytes) { ... }`，解码失败显示占位文案（不崩溃）；上传/确认/回滚在途拦截返回；回滚/覆盖前二次确认；说明“固件热替换在 Phase 5”。

- [ ] **Step 5: 提交**

```bash
git add companion/app/src/main companion/app/src/test/java/me/rerere/companion/ui/navigation
git commit -m "feat(companion-app): add assets screen"
```

---

## Task 15: 日志、索引与全量验证

**Files:**
- Create: `docs/语音机器人需求文档/v2_migration_log/31_phase4_4d_settings_assets_backup.md`
- Modify: `docs/语音机器人需求文档/v2_migration_log/README.md`
- Modify: `docs/superpowers/plans/2026-11-27-companion-app-phase4d-design.md`（若实现与设计有偏差，逐条记录）

- [ ] **Step 1: 全量服务端测试**

Run: `cd companion\server; $env:PYTHONPATH="..\.."; uv run --python 3.12 --with-requirements requirements.txt python -m pytest -q`
Expected: `0 failed`，总数 ≥ 427。

- [ ] **Step 2: App 全量测试 + 构建**

Run: `cd companion\app; .\gradlew.bat testDebugUnitTest assembleDebug assembleRelease --console=plain`
Expected: BUILD SUCCESSFUL，测试总数 ≥ 223。

- [ ] **Step 3: 写日志**

日志必须包含：改动文件清单、真实命令与输出摘要（services/tests 计数）、偏差、未覆盖项（真机 SAF/图片预览未验证）、下一步。不得含 Key、备份正文、密码。

- [ ] **Step 4: 更新索引并提交**

```bash
git add docs
git commit -F <utf8-message-file>
```

- [ ] **Step 5: 派审查子智能体**

只读审查本轮全部 diff：密钥泄漏、上传边界、预览 owner/TTL、PNG 校验、撤销当前 Token、离线/在途门控；输出 P0/P1/P2 清单。之后按清单修复并复跑全量。

---

## Self-Review 记录

- **Spec 覆盖**：设置（Task 1–5）、搜索总开关（Task 2）、Token 管理（Task 3/4/5）、备份导入+导出（Task 6–9）、素材包（Task 10–14）、日志（Task 15）均有对应 Task。
- **类型一致**：`AppSettingsService` 的 `view/update/effective`、`AssetService` 的 `preview/confirm/rollback/view/frame_bytes`、App 侧 `SettingsRepository/BackupRepository/AssetRepository/TokenRepository` 在首次定义处给出完整签名，后续 Task 只引用。
- **Review Focus 对应测试**：Key 泄漏 → Task 4；上传边界 → Task 6/10；预览复用/过期 → Task 6；伪 PNG → Task 10；撤销当前 Token → Task 4；离线/在途 → Task 5/8/13。
- **比例**：计划只固定接口、边界值与断言，未抄写实现体；实现细节留给执行者按现有代码风格决定。
