# Companion App 4A（脚手架 + 登录 + 加密会话）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 建立完全独立的 `companion/app` Android 工程，交付可登录、可持久化会话、令牌失效自动回登录的最小可用 App。

**Architecture:** 单模块 MVVM（单 project 构建，模块即 `companion/app` 根）；手写 `AppContainer` 装配 `SecureStore`、`SessionManager`、`AuthApi`、`AuthRepository`；服务器地址与 Token 用 Android Keystore AES-GCM 加密后写入 Preferences DataStore；UI 用 Compose + 自建轻量导航状态栈（不引入 navigation-compose，规避未缓存依赖）。

**Tech Stack:** Kotlin 2.4.21、AGP 9.4.0、Gradle 9.6.0、JDK 17、Compose BOM 2026.09.00、Retrofit 3.0.0 + OkHttp 5.5.0 + kotlinx.serialization 1.12.0、DataStore Preferences 1.3.0、JUnit4 4.13.2 + kotlinx-coroutines-test 1.11.0。

**Spec:** `docs/superpowers/specs/2026-11-27-companion-app-phase4-design.md`（§6、§7.1、§9 的 4A 行、§13 第 6/7 条）

## Global Constraints

- `companion/app` 为独立 Gradle 工程，**不得修改根 `settings.gradle.kts`、根 `build.gradle.kts`、`app/` 与 `gradle/` 根文件**。
- `minSdk = 26`；`targetSdk = 35`；`compileSdk` 用本机已安装平台 `release(37) { minorApiLevel = 2 }`（本机仅有 android-37.0/37.2，偏差已回填设计 §3）。
- 严禁自定义 `X509TrustManager`/`HostnameVerifier`/信任所有证书；release `cleartextTrafficPermitted="false"`；debug 仅 `localhost`、`127.0.0.1`、`10.0.2.2` 允许明文。
- App 内不得出现任何模型/ASR/TTS/搜索 Key；Token 只允许以 Keystore 加密后的密文落盘；日志不得打印 Token、口令、密文或完整请求体。
- 所有用户可见文案为中文；错误必须给出可读原因，禁止无限转圈。
- 单元测试全部为 JVM 测试，不依赖真机、真实网络或真实服务器；HTTP 用例使用 JDK 内置 `com.sun.net.httpserver.HttpServer`。
- 构建机 Android SDK 路径 `D:\Android\sdk` 只写入未跟踪的 `companion/app/local.properties`。
- 每个任务结束必须 `git status --porcelain` 无未提交改动（除被 ignore 的 `local.properties`）。
- Python 改动不得破坏既有 448 条测试；解释器固定 `C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest`（从仓库根运行）。

## Review Focus

1. **服务器地址形态**：带路径前缀（`https://host/companion`）、带端口、带尾斜杠、含 query/fragment/userinfo 时，拼接必须仍正确且不丢前缀、不产生 `//api`。
2. **令牌过期判断**：`expires_at` 是毫秒时间戳；本地时钟超前/落后、等于过期时刻时必须按“已过期”处理，不得带着过期 Token 进入主界面。
3. **429 倒计时**：`Retry-After` 缺失、为 `0`、为负数或极大值（如 86400）时，按钮不能永久禁用、也不能立刻反复重试。
4. **401 来源**：登录请求自身返回 401 必须只显示“账号或口令错误”，不得触发全局清退并导致导航循环。
5. **加密存储损坏**：Keystore 密钥失效、密文被截断、旧版本 JSON 字段缺失时必须清空并回登录，不得崩溃或白屏。
6. **错误体形态**：`detail` 为字符串码、中文句子或 422 数组，以及无 body 的 5xx，都必须映射出可读中文原因。

---

### Task 1: 独立工程骨架与可构建的 Compose 空壳

**Files:**
- Create: `companion/app/settings.gradle.kts`
- Create: `companion/app/build.gradle.kts`
- Create: `companion/app/gradle.properties`
- Create: `companion/app/gradle/wrapper/gradle-wrapper.properties`、`companion/app/gradle/wrapper/gradle-wrapper.jar`
- Create: `companion/app/gradlew`、`companion/app/gradlew.bat`
- Create: `companion/app/.gitignore`
- Create (未跟踪): `companion/app/local.properties`
- Create: `companion/app/src/main/AndroidManifest.xml`
- Create: `companion/app/src/main/res/values/strings.xml`
- Create: `companion/app/src/main/res/values/themes.xml`
- Create: `companion/app/src/main/res/xml/network_security_config.xml`
- Create: `companion/app/src/debug/res/xml/network_security_config.xml`
- Create: `companion/app/src/main/java/me/rerere/companion/CompanionApp.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/MainActivity.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/ui/theme/Theme.kt`

**Interfaces:**
- Consumes: 无。
- Produces: 包名/命名空间 `me.rerere.companion`；`MainActivity` 启动后显示“Companion”占位文本；`CompanionApp : Application`（4A 后续任务在此创建 `AppContainer`）。

- [x] **Step 1: 复制 Gradle Wrapper 并建立工程文件**

从仓库根复制 `gradlew`、`gradlew.bat`、`gradle/wrapper/gradle-wrapper.jar`、`gradle/wrapper/gradle-wrapper.properties` 到 `companion/app/` 对应位置（保持 `distributionUrl` 为 gradle-9.6.0-bin.zip）。

`companion/app/settings.gradle.kts`：

```kotlin
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "companion-app"
```

`companion/app/gradle.properties`：

```properties
org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8
android.useAndroidX=true
kotlin.code.style=official
```

`companion/app/.gitignore`：

```gitignore
.gradle/
build/
local.properties
*.iml
.idea/
```

`companion/app/local.properties`（不提交）：`sdk.dir=D\:\\Android\\sdk`

- [x] **Step 2: 写构建脚本**

`companion/app/build.gradle.kts` 必须包含：

```kotlin
plugins {
    id("com.android.application") version "9.4.0"
    id("org.jetbrains.kotlin.android") version "2.4.21"
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.21"
    id("org.jetbrains.kotlin.plugin.serialization") version "2.4.21"
}

android {
    namespace = "me.rerere.companion"
    compileSdk { version = release(37) { minorApiLevel = 2 } }
    defaultConfig {
        applicationId = "me.rerere.companion"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "2.0.0"
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
}
```

依赖（`dependencies` 块）固定版本：`platform("androidx.compose:compose-bom:2026.09.00")`、`androidx.compose.ui:ui`、`androidx.compose.ui:ui-tooling-preview`、`androidx.compose.material3:material3`、`androidx.activity:activity-compose:1.13.0`、`androidx.core:core-ktx:1.19.1`、`androidx.lifecycle:lifecycle-runtime-ktx:2.11.0`、`androidx.lifecycle:lifecycle-runtime-compose:2.11.0`、`androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0`、`androidx.datastore:datastore-preferences:1.3.0`、`com.squareup.retrofit2:retrofit:3.0.0`、`com.squareup.retrofit2:converter-kotlinx-serialization:3.0.0`、`com.squareup.okhttp3:okhttp:5.5.0`、`org.jetbrains.kotlinx:kotlinx-serialization-json:1.12.0`、`org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0`；测试：`junit:junit:4.13.2`、`org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0`。

- [x] **Step 3: 写清单、网络安全配置、主题与占位界面**

`AndroidManifest.xml` 关键项：

```xml
<application
    android:name=".CompanionApp"
    android:label="@string/app_name"
    android:networkSecurityConfig="@xml/network_security_config"
    android:usesCleartextTraffic="false"
    android:theme="@style/Theme.Companion">
    <activity android:name=".MainActivity" android:exported="true">
        <intent-filter>
            <action android:name="android.intent.action.MAIN" />
            <category android:name="android.intent.category.LAUNCHER" />
        </intent-filter>
    </activity>
</application>
```

`src/main/res/xml/network_security_config.xml`：`<base-config cleartextTrafficPermitted="false" />`。
`src/debug/res/xml/network_security_config.xml`：同样 base 为 false，另加 `domain-config cleartextTrafficPermitted="true"` 覆盖 `localhost`、`127.0.0.1`、`10.0.2.2`。
`strings.xml` 提供 `app_name` = `Companion`。
`src/main/res/values/themes.xml`：`<style name="Theme.Companion" parent="android:Theme.Material.Light.NoActionBar" />`。
`Theme.kt` 用 `MaterialTheme`（`dynamicColor = false`），深/浅色方案各一套 `lightColorScheme()`/`darkColorScheme()`；`MainActivity` 用 `setContent { CompanionTheme { Text("Companion") } }`。

- [x] **Step 4: 构建验证**

Run: `.\gradlew.bat assembleDebug`（workdir `companion/app`）
Expected: `BUILD SUCCESSFUL`，产物 `build/outputs/apk/debug/app-debug.apk` 存在。

- [x] **Step 5: 提交**

```bash
git add companion/app
git commit -m "feat(companion-app): scaffold standalone android project"
```

---

### Task 2: 服务器地址校验与归一化

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/core/UrlValidator.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/core/UrlValidatorTest.kt`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `enum class UrlError { EMPTY, BAD_SCHEME, CLEARTEXT_NOT_ALLOWED, INVALID_HOST, HAS_QUERY_OR_FRAGMENT, HAS_USERINFO }`
  - `sealed interface UrlCheck { data class Ok(val normalized: String) : UrlCheck; data class Invalid(val reason: UrlError) : UrlCheck }`
  - `object UrlValidator { fun validate(raw: String, allowCleartext: Boolean): UrlCheck }`
  - `normalized` 保证以 `https://` 或 debug 下 `http://` 开头、以 `/` 结尾、保留端口与路径前缀、去掉 query/fragment。

- [x] **Step 1: 写失败测试**

覆盖：`https://example.com` → `https://example.com/`；`https://example.com:8443/companion` → `.../companion/`；`http://localhost:8000` 在 `allowCleartext=true` 时通过、在 false 时 `CLEARTEXT_NOT_ALLOWED`；`http://10.0.2.2:8000/` 仅 debug 通过；`ftp://x` → `BAD_SCHEME`；`https://user:pass@host` → `HAS_USERINFO`；`https://host/?a=1` → `HAS_QUERY_OR_FRAGMENT`；`https://` → `INVALID_HOST`；`""` 与 `"   "` → `EMPTY`。

```kotlin
@Test fun `normalizes bare https host with trailing slash`() {
    assertEquals(UrlCheck.Ok("https://example.com/"), UrlValidator.validate("https://example.com", allowCleartext = false))
}
@Test fun `rejects cleartext outside debug targets`() {
    assertEquals(UrlCheck.Invalid(UrlError.CLEARTEXT_NOT_ALLOWED), UrlValidator.validate("http://example.com", allowCleartext = true))
}
```

- [x] **Step 2: 运行并确认失败**

Run: `.\gradlew.bat testDebugUnitTest --tests "me.rerere.companion.core.UrlValidatorTest"`
Expected: FAIL（`UrlValidator` 未定义）。

- [x] **Step 3: 实现 `UrlValidator`**

用 `java.net.URI` 解析：`scheme` 只接受 `http`/`https`；`host` 非空；`userInfo` 非空 → `HAS_USERINFO`；`query`/`fragment` 非空 → `HAS_QUERY_OR_FRAGMENT`；`https` 始终允许，`http` 仅在 `allowCleartext` 且 host ∈ {`localhost`,`127.0.0.1`,`10.0.2.2`} 时允许；输出用拼接而非 `URI.toString()`，避免保留 query 或丢掉尾部斜杠。

- [x] **Step 4: 运行并确认通过**

Run: `.\gradlew.bat testDebugUnitTest --tests "me.rerere.companion.core.UrlValidatorTest"`
Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add companion/app/src/main/java/me/rerere/companion/core/UrlValidator.kt companion/app/src/test/java/me/rerere/companion/core/UrlValidatorTest.kt
git commit -m "feat(companion-app): validate and normalize server url"
```

---

### Task 3: 加密会话存储与会话状态

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/data/store/SecureStore.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/store/SessionCodec.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/store/KeystoreSecureStore.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/session/SessionManager.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/testutil/FakeSecureStore.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/data/store/SessionCodecTest.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/data/session/SessionManagerTest.kt`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `@Serializable data class StoredSession(val serverUrl: String, val token: String, val tokenId: String, val username: String, val expiresAtMillis: Long)`
  - `interface SecureStore { suspend fun read(): StoredSession?; suspend fun write(session: StoredSession); suspend fun clear() }`
  - `object SessionCodec { fun encode(session: StoredSession): String; fun decode(json: String): StoredSession? }`（缺字段/坏 JSON 返回 `null`）
  - `fun interface Clock { fun nowMillis(): Long }`
  - `class SessionManager(private val store: SecureStore, private val clock: Clock) { val state: StateFlow<StoredSession?>; suspend fun restore(): StoredSession?; suspend fun save(session: StoredSession); suspend fun invalidate(); suspend fun clear() }`
  - `invalidate()`/`clear()` 幂等：已为 `null` 时不重复清空、不重复发射。

- [x] **Step 1: 写失败测试**

`SessionCodecTest`：往返相等；缺 `token` 返回 `null`；非法 JSON 返回 `null`。
`SessionManagerTest`（`runTest`）：
- `restore` 读到未过期会话 → `state.value` 非空；
- `expiresAtMillis == clock.nowMillis()` → 视为过期，清空并返回 `null`；
- `store.read()` 抛异常（模拟密文损坏）→ 清空、返回 `null`、不抛出；
- 连续两次 `invalidate()` 只产生一次 `null` 发射（用 `StateFlow` 去重特性断言 `state.value == null` 且 `store.clearCount == 1`）。

- [x] **Step 2: 运行并确认失败**

Run: `.\gradlew.bat testDebugUnitTest --tests "me.rerere.companion.data.store.SessionCodecTest" --tests "me.rerere.companion.data.session.SessionManagerTest"`
Expected: FAIL（类型未定义）。

- [x] **Step 3: 实现 codec 与 SessionManager**

`SessionManager.state` 用 `MutableStateFlow`；`restore()` 捕获 `Exception`（含 `GeneralSecurityException`、`SerializationException`）→ `clear()`；过期判断 `expiresAtMillis <= clock.nowMillis()`。`invalidate()` 内部 `if (state.value == null) return`。

- [x] **Step 4: 实现 `KeystoreSecureStore`（无 JVM 单测，真机待办）**

构造 `KeystoreSecureStore(context: Context)`：DataStore 文件 `session.preferences_pb`，单 key `payload`；AndroidKeyStore 别名 `companion.session`，AES/GCM/NoPadding，`KeyGenParameterSpec` 设 `setRandomizedEncryptionRequired(true)`、`PURPOSE_ENCRYPT|PURPOSE_DECRYPT`；写入格式 `Base64(iv):Base64(ciphertext)`；解密失败向上抛 `GeneralSecurityException` 由 `SessionManager.restore()` 兜底清空。注释写明“真机验证待办”。

- [x] **Step 5: 运行并确认通过**

Run: `.\gradlew.bat testDebugUnitTest --tests "me.rerere.companion.data.store.SessionCodecTest" --tests "me.rerere.companion.data.session.SessionManagerTest"`
Expected: PASS。

- [x] **Step 6: 提交**

```bash
git add companion/app/src/main/java/me/rerere/companion/data companion/app/src/test/java/me/rerere/companion
git commit -m "feat(companion-app): add encrypted session store and session manager"
```

---

### Task 4: HTTP 层（登录 API、Bearer 注入、401 单次清退、错误映射）

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/core/AppError.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/AuthDtos.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/AuthApi.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/AuthInterceptor.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/ErrorMapper.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/AuthApiFactory.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/testutil/TestHttpServer.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/testutil/FakeAuthApi.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/AuthRepository.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/data/api/ErrorMapperTest.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/data/api/AuthApiTest.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/data/repo/AuthRepositoryTest.kt`

**Interfaces:**
- Consumes: `StoredSession`、`SessionManager`、`UrlValidator`。
- Produces:
  - `sealed interface AppError { val message: String; val code: String }`，含 `data class Http(override val code: String, override val message: String, val retryAfterSeconds: Int? = null): AppError` 与 `data class Network(override val message: String): AppError`
  - `object ErrorMapper { fun map(status: Int, body: String?, retryAfter: String? = null): AppError; fun retryAfterSeconds(header: String?): Int? }`
  - `@Serializable data class LoginRequest(val username: String, val password: String, @SerialName("device_name") val deviceName: String = "android")`
  - `@Serializable data class LoginResponse(val token: String, @SerialName("token_id") val tokenId: String, val username: String, @SerialName("expires_at") val expiresAt: Long)`
  - `interface AuthApi { @POST("api/auth/login") suspend fun login(@Body body: LoginRequest): LoginResponse; @POST("api/auth/logout") suspend fun logout(): Unit }`
  - `class AuthInterceptor(private val tokenProvider: () -> String?, private val isLoginRequest: (String) -> Boolean, private val onUnauthorized: () -> Unit) : Interceptor`
  - `class AuthApiFactory(private val sessionProvider: () -> StoredSession?, private val onUnauthorized: () -> Unit) { fun create(baseUrl: String): AuthApi }`
  - `interface AuthRepository { suspend fun login(serverUrl: String, username: String, password: String): AppResult<StoredSession>; suspend fun logout() }`
  - `class HttpAuthRepository(private val apiFactory: AuthApiFactory, private val sessionManager: SessionManager) : AuthRepository`（`logout()` 用 `sessionManager.state.value?.serverUrl` 建客户端，失败也清空本地会话）
  - `sealed interface AppResult<out T> { data class Ok<T>(val value: T); data class Err(val error: AppError) }`

- [x] **Step 1: 写 `TestHttpServer` 与失败测试**

`TestHttpServer`：封装 `com.sun.net.httpserver.HttpServer` 绑定 `127.0.0.1:0`，暴露 `baseUrl: String`、`enqueue(status: Int, body: String, headers: Map<String,String> = emptyMap())`、`lastRequestHeaders`、`lastRequestBody`。

`ErrorMapperTest` 断言：
- `{"detail":"invalid_credentials"}` → 中文“账号或口令错误”；
- `{"detail":"服务器开小差了"}` → 原样返回该字符串；
- `{"detail":[{"msg":"field required"}]}` → `validation_error` 且消息非空；
- `null` body + 500 → 5xx 通用文案；
- `{"detail":"x","message":"可读说明"}` → 优先“可读说明”；
- `retryAfterSeconds("120")` → 120；`"0"`/`"-3"` → 0；`null`/非数字 → `null`。

`AuthApiTest`：
- 200 响应能反序列化 `tokenId`/`expiresAt`；
- 请求头带 `Authorization: Bearer t0`（tokenProvider 返回 `t0`）；
- 401 响应触发 `onUnauthorized` 一次；同一响应内多次调用不重复触发（`AtomicBoolean`）；
- 登录路径 401 不触发 `onUnauthorized`（`isLoginRequest` 命中）。

`AuthRepositoryTest`：用 `FakeAuthApi`（实现 `AuthApi` 的类，记录调用、可返回或抛错）：
- 登录成功 → `sessionManager.state.value.serverUrl` 为归一化地址且带 token；
- 登录抛 `HttpException` 401 → 返回 `AppResult.Err` 且不写会话。

- [x] **Step 2: 运行并确认失败**

Run: `.\gradlew.bat testDebugUnitTest --tests "me.rerere.companion.data.api.*" --tests "me.rerere.companion.data.repo.*"`
Expected: FAIL（类型未定义）。

- [x] **Step 3: 实现 DTO、ErrorMapper、Interceptor、Factory**

`AuthApiFactory`：OkHttpClient 超时 connect 10s / read 15s / write 15s；`addInterceptor(AuthInterceptor(...))`；`Retrofit.Builder().baseUrl(baseUrl).client(...).addConverterFactory(Json.asConverterFactory("application/json".toMediaType())).build()`；`Json { ignoreUnknownKeys = true }`。`isLoginRequest(path) = path.endsWith("api/auth/login")`（用 `request.url.encodedPath`）。

`ErrorMapper.map`：解析 body 为 `JsonObject`（失败则 null）；`message` 优先；`detail` 是字符串时查固定文案表，未命中返回原文；`detail` 是数组时返回 `Http("validation_error", "请求参数不合法")`；无 body 按 `status` 返回 `Http("http_$status", ...)`。

`HttpAuthRepository.login`：捕获 `HttpException` → `ErrorMapper.map(code(), body, retryAfter = e.response()?.headers()?.get("Retry-After"))`, 捕获 `IOException` → `AppError.Network("无法连接服务器，请检查地址与网络")`；成功后构造 `StoredSession` 并 `sessionManager.save`。`logout()` 取 `sessionManager.state.value?.serverUrl` 建客户端调用 `/api/auth/logout`，任何异常都吞掉，最后 `sessionManager.clear()`。

- [x] **Step 4: 运行并确认通过**

Run: `.\gradlew.bat testDebugUnitTest --tests "me.rerere.companion.data.api.*" --tests "me.rerere.companion.data.repo.*"`
Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add companion/app/src/main/java/me/rerere/companion companion/app/src/test/java/me/rerere/companion
git commit -m "feat(companion-app): add auth http layer and error mapping"
```

---

### Task 5: 服务端登录 429 返回 `Retry-After`

**Files:**
- Modify: `companion/server/app/security/rate_limit.py`
- Modify: `companion/server/app/routers/auth.py:67-69`
- Test: `companion/server/tests/test_auth_retry_after.py`

**Interfaces:**
- Consumes: 既有 `_EVENTS`、`_prune`。
- Produces: `security/rate_limit.py` 新增 `retry_after_seconds(key: str, window_seconds: float, *, now: float | None = None) -> int`：窗口非空时返回 ≥1 的整数（不超过 `ceil(window_seconds)`），窗口为空返回 `0`。登录 429 响应带 `Retry-After: "<n>"`。

- [x] **Step 1: 写失败测试**

```python
def test_retry_after_zero_when_empty():
    reset_rate_limits()
    assert retry_after_seconds("login:x", 300, now=1000.0) == 0

def test_retry_after_counts_down_to_oldest_expiry():
    reset_rate_limits()
    for t in (1000.0, 1001.0, 1002.0):
        record("login:x", 300, now=t)
    assert retry_after_seconds("login:x", 300, now=1000.0) == 300
    assert retry_after_seconds("login:x", 300, now=1100.0) == 200
```

API 用例：测试开始先 `reset_rate_limits()`，用 `monkeypatch` 设 `settings.LOGIN_RATE_LIMIT = 1`、`settings.LOGIN_RATE_WINDOW_SECONDS = 300`，先错一次口令，再请求 → 429 且 `response.headers["retry-after"]` 解析后 ∈ `[1, 300]`；用例结束 `reset_rate_limits()` 复原。

- [x] **Step 2: 运行并确认失败**

Run: `C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest companion/server/tests/test_auth_retry_after.py -q`
Expected: FAIL（`retry_after_seconds` 不存在 / 无 header）。

- [x] **Step 3: 实现**

`retry_after_seconds`：取 bucket 首元素 `oldest`，`remaining = window_seconds - (now - oldest)`；`max(0, ceil(remaining))`；空 bucket 返回 0。`auth.py` 429 分支改为 `raise HTTPException(status_code=429, detail="rate_limited", headers={"Retry-After": str(retry_after_seconds(rate_key, settings.LOGIN_RATE_WINDOW_SECONDS))})`。

- [x] **Step 4: 运行新用例与既有鉴权用例**

Run: `…python.exe -m pytest companion/server/tests/test_auth_retry_after.py companion/server/tests/test_auth.py -q`
Expected: 全 PASS。

- [x] **Step 5: 提交**

```bash
git add companion/server/app/security/rate_limit.py companion/server/app/routers/auth.py companion/server/tests/test_auth_retry_after.py
git commit -m "feat(companion): return retry-after on login rate limit"
```

---

### Task 6: 登录页、启动恢复会话、轻量导航

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/ui/navigation/AppNav.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/feature/login/LoginViewModel.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/feature/login/LoginScreen.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/feature/home/HomePlaceholderScreen.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/CompanionApp.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/MainActivity.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/testutil/MainDispatcherRule.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/testutil/FakeAuthRepository.kt`
- Test: `companion/app/src/test/java/me/rerere/companion/feature/login/LoginViewModelTest.kt`

**Interfaces:**
- Consumes: `UrlValidator`、`AuthRepository`、`SessionManager`、`Clock`、`ErrorMapper.retryAfterSeconds`。
- Produces:
  - `data class LoginUiState(val serverUrl: String = "", val username: String = "admin", val password: String = "", val submitting: Boolean = false, val error: String? = null, val urlError: UrlError? = null, val retryAfterSeconds: Int = 0, val canSubmit: Boolean = true)`
  - `class LoginViewModel(private val repository: AuthRepository, private val clock: Clock, private val allowCleartext: Boolean, private val ticker: suspend (Long) -> Unit = { delay(it) }) : ViewModel()`，暴露 `val state: StateFlow<LoginUiState>`、`fun onServerUrlChange(v: String)`、`fun onUsernameChange(v: String)`、`fun onPasswordChange(v: String)`、`fun submit()`。
  - `@Composable fun LoginScreen(state: LoginUiState, onServerUrlChange: (String) -> Unit, onUsernameChange: (String) -> Unit, onPasswordChange: (String) -> Unit, onSubmit: () -> Unit)`
  - `class AppContainer(context: Context, clock: Clock = Clock { System.currentTimeMillis() })`，字段 `sessionManager`、`authRepository: AuthRepository`、`allowCleartext = BuildConfig.DEBUG`；内部持有 `appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)`。

- [x] **Step 1: 写失败测试**

`LoginViewModelTest`（`runTest` + `StandardTestDispatcher`，配合 `MainDispatcherRule`；`FakeAuthRepository` 记录入参并可返回 `AppResult.Err`；ticker 用 `{ delay(it) }` 配合虚拟时间）：
- 非法地址（`ftp://x`）提交 → `state.urlError == UrlError.BAD_SCHEME`，仓库未被调用；
- 成功 → `state.error == null`，仓库收到的地址为归一化地址；
- 401 → `error == "账号或口令错误"`；
- 429（`AppError.Http("rate_limited", ..., retryAfterSeconds = 2)`）→ `retryAfterSeconds == 2` 且 `canSubmit == false`，`advanceTimeBy(2000)` 后为 `0` 且 `canSubmit == true`；
- `Retry-After` 缺失（`retryAfterSeconds == null`）→ 立即恢复可提交，只显示通用提示；
- 提交中重复点击只调用一次仓库。

- [x] **Step 2: 运行并确认失败**

Run: `.\gradlew.bat testDebugUnitTest --tests "me.rerere.companion.feature.login.LoginViewModelTest"`
Expected: FAIL（类型未定义）。

- [x] **Step 3: 实现 ViewModel 与容器**

`submit()`：先 `UrlValidator.validate(serverUrl, allowCleartext)`；失败设置 `urlError` 返回。成功后 `submitting = true`；`try/finally` 复位。错误若是 `AppError.Http` 且 `code == "rate_limited"` 且 `retryAfterSeconds` 非空：记录 `retryUntil = clock.nowMillis() + seconds*1000`，循环 `ticker(250)` 直到 `retryUntil <= clock.nowMillis()`，期间 `retryAfterSeconds = ceil((retryUntil - now)/1000.0).toInt()`、`canSubmit = false`，结束后归零恢复 `canSubmit`。`retryAfterSeconds` 为空时只设置通用提示，不进入倒计时。

`AppContainer`：`KeystoreSecureStore(context)` + `SessionManager(store, clock)` + `AuthApiFactory(sessionProvider = { sessionManager.state.value }, onUnauthorized = { appScope.launch { sessionManager.invalidate() } })` + `HttpAuthRepository`，字段类型声明为 `AuthRepository`。`onUnauthorized` 只在非登录请求触发（Task 4 已保证）。

- [x] **Step 4: 实现界面与导航**

`AppNav`：`@Composable fun AppNav(container: AppContainer)`；`val session by container.sessionManager.state.collectAsStateWithLifecycle()`；`LaunchedEffect(Unit) { container.sessionManager.restore() }`；用 `if (session == null)` 选择 `LoginScreen` 或 `HomePlaceholderScreen`（显示服务器地址 + “退出登录”按钮，调用 `container.authRepository.logout()`，用于验证清退链路）。

`LoginScreen`：`OutlinedTextField`（地址、用户名、口令，口令 `visualTransformation = PasswordVisualTransformation()`），错误文本用 `MaterialTheme.colorScheme.error`，按钮在 `!state.canSubmit || state.submitting` 时禁用；`state.retryAfterSeconds > 0` 时按钮文案为“请等待 N 秒”。`MainActivity` 把 `AppNav(container)` 放入主题。

- [x] **Step 5: 运行单测与构建**

Run: `.\gradlew.bat testDebugUnitTest`；然后 `.\gradlew.bat assembleDebug`
Expected: 全部 PASS，`BUILD SUCCESSFUL`。

- [x] **Step 6: 提交**

```bash
git add companion/app
git commit -m "feat(companion-app): add login screen and session-gated navigation"
```

---

### Task 7: 4A 收尾验证与迁移日志

**Files:**
- Create: `docs/语音机器人需求文档/v2_migration_log/27_phase4_4a_android_shell_login.md`
- Modify: `docs/语音机器人需求文档/v2_migration_log/README.md`
- Modify: `docs/superpowers/plans/2026-11-27-companion-app-phase4a.md`（本文件，勾选复选框并记录偏差）

**Interfaces:**
- Consumes: 前 6 个任务的成果。
- Produces: 可复核的 4A 记录（命令、数字、偏差、待办）。

- [x] **Step 1: 全量回归**

Run（仓库根）: `C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest -q`
Expected: 448 + 新增用例全部 PASS，记录真实数字。

Run（`companion/app`）: `.\gradlew.bat testDebugUnitTest assembleDebug`
Expected: PASS + `BUILD SUCCESSFUL`，记录测试数量、耗时与 APK 路径。

- [x] **Step 2: 写日志 27**

内容必须包含：改动文件清单、真实命令与输出摘要（不含 Token/口令）、与设计的偏差（`compileSdk 37`、自建导航、未用 MockWebServer）、真机待办（安装、Keystore 解密、HTTPS 证书、401/4401 实测）、下一步（4B）。

- [x] **Step 3: 更新 README 索引与计划复选框**

README 追加一行 `| 27 | Phase 4-4A：Android 工程/登录/加密会话 | ✅ 已完成 | 独立工程、Keystore 会话、401 清退、Retry-After；单元测试与 assembleDebug 通过 |`；本计划所有 `- [x]` 改为 `- [x]`（未做的真机步骤标注“待办”，保持未勾选并在日志说明）。

- [x] **Step 4: 提交**

```bash
git add "docs/语音机器人需求文档/v2_migration_log/27_phase4_4a_android_shell_login.md" "docs/语音机器人需求文档/v2_migration_log/README.md" docs/superpowers/plans/2026-11-27-companion-app-phase4a.md
git commit -m "docs(companion): record phase 4 block 4a"
```

---

## Verification Checklist（4A 完成定义）

- [x] `companion/app/gradlew.bat testDebugUnitTest` 全绿，覆盖 Review Focus 的 6 类输入。
- [x] `companion/app/gradlew.bat assembleDebug` 产出 APK。
- [x] 仓库根 `pytest -q` 全绿（448 + 新增）。
- [x] `git status --porcelain` 仅剩被 ignore 的 `local.properties`（若存在）。
- [x] 日志 27 与 README 索引已更新，含偏差与真机待办。

---

## 执行偏差记录（4A 实际）

| # | 计划 | 实际 | 原因 / 影响 |
| :--- | :--- | :--- | :--- |
| 1 | 引入 `navigation-compose` | 用 `session == null` 切屏 | 依赖未缓存；4A 仅两屏。4B 层级变多时再评估 |
| 2 | MockWebServer | JDK `com.sun.net.httpserver.HttpServer` | 依赖未缓存 + Maven TLS 失败；无法测慢响应/断连时序 |
| 3 | `compileSdk 37` / `targetSdk 35` | `compileSdk release(37){minorApiLevel=2}` / `targetSdk 35` | 本机只有 android-37.0/37.2；上架前与根工程统一 |
| 4 | 同时应用 `org.jetbrains.kotlin.android` | 移除该插件 | AGP 9.4 内置 Kotlin，旧插件直接报错 |
| 5 | APK `app-debug.apk` | `companion-app-debug.apk` | 随 `rootProject.name`；脚本/文档按实际名 |
| 6 | `LoginViewModel(…, clock, …)` + 250ms 轮询 | 注入 `elapsedMillis: () -> Long`（默认 `SystemClock.elapsedRealtime()`），按截止时刻 250ms 重算 | 后台冻结后 tick 停但时间仍走，截止时刻能自动纠正；超 3600s 按 3600s 展示 |
| 7 | 复用 `companion/.gitignore` 的 `data/` | 改为 `/server/data/` | `data/` 会忽略 App 源码包 `…/companion/data/` |
| 8 | `LaunchedEffect(Unit)` | `LaunchedEffect(container)` | 容器变化时重新恢复，避免陈旧状态 |
| 9 | `AuthApiFactory` 为类 | `fun interface` + `RetrofitAuthApiFactory` | 便于单测注入假 API |
| 10 | 401 去重按拦截器 | 按“已通知的 Token”去重（`AtomicReference`），仅对带 `Bearer` 的 401 清退 | 布尔标志永不复位会让二次登录后 401 失聪；按 Token 天然恢复，并配 `invalidate(failedToken)` 防旧 401 误杀新会话 |
| 11 | 登出用 `rememberCoroutineScope` | `AppContainer.logout()`（`appScope`） | 旋转取消 UI 协程会漏清 Token |
| 12 | URL 只校验 scheme/host/userinfo | 增加端口 `1..65535`、`.`/`..`/编码分隔符、反斜杠、主机小写与末尾点、IPv6 方括号、IPv4 八位组 | 否则 OkHttp 在 `create()` 阶段抛参数异常 |
| 13 | `SessionManager` 直接读写 | 加 `Mutex`；`invalidate(failedToken)` 在锁内比对 Token | 401 与新登录并发、restore 前的 401、迟到的旧 401 都会残留/误杀会话 |
| 14 | `create()` 在 `try` 外 | 纳入 `try`，映射 `invalid_server_url` | 非法 baseUrl 不再冒泡到 UI |
| 15 | 通用 `catch (Exception)` | 先 rethrow `CancellationException` | 不破坏结构化并发 |

真机待办（未勾选即未验证）：安装启动、Keystore 冷启动解密、真实 HTTPS 证书、401/4401 实测、429 倒计时对齐、release 包拒绝明文。

