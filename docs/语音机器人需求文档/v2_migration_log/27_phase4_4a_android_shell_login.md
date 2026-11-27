# 27 · Phase 4-4A：Android 独立工程 / 登录 / 加密会话

- 状态：**已完成（两轮独立审查修复；真机与 HTTPS 证书验收待办）**
- 日期：2026-11-27
- 分支：`feature/companion-robot-v2`
- 设计依据：`docs/superpowers/specs/2026-11-27-companion-app-phase4-design.md`（§13 裁决）
- 实施计划：`docs/superpowers/plans/2026-11-27-companion-app-phase4a.md`
- 前置：音频 Block C（`3d3a69c0`，448 passed / mutation 15/15 + 9/9）

## 1. 目标与范围

Phase 4 按“纵向切片先能用”拆成 4A–4D。本块只做 4A：**能装上、能登录、能安全保存会话、401 能清退**。

已做：
- `companion/app` 独立单模块 Gradle 工程（不进入根 Android 工程，不改根 `settings.gradle.kts`）。
- 服务器地址校验与归一化；强制 HTTPS，仅 debug 版允许 localhost / 127.0.0.1 / 10.0.2.2 明文。
- 会话以 Keystore AES-256-GCM 加密后存 Preferences DataStore；启动恢复、过期/损坏清空。
- Retrofit + OkHttp 登录/登出；Bearer 注入；401 全局清退（登录接口的 401 不触发）。
- 登录页（地址/用户名/口令、倒计时、错误文案）、`session == null` 决定去登录页还是占位主页。
- 服务端登录 429 补 `Retry-After`，App 侧按其秒数做本地倒计时。

不在本块：角色/会话/设备/备份各页（4B–4D）、WebSocket App 通道、任何模型/ASR/TTS/搜索 Key、WebView 套壳。

## 2. 改动文件

### 新增（App）

| 文件 | 说明 |
| :--- | :--- |
| `companion/app/settings.gradle.kts` | 单 project `companion-app`，FAIL_ON_PROJECT_REPOS |
| `companion/app/build.gradle.kts` | AGP 9.4.0 / Kotlin 2.4.21 / Compose BOM 2026.09.00；`compileSdk 37.2`、`targetSdk 35`、`minSdk 26`；`buildConfig = true` |
| `companion/app/gradle.properties`、`.gitignore`、`gradlew`、`gradlew.bat`、`gradle/wrapper/*` | 从根工程复制 wrapper（Gradle 9.6.0），本地独立构建 |
| `companion/app/src/main/AndroidManifest.xml` | INTERNET / ACCESS_NETWORK_STATE；`usesCleartextTraffic="false"`；`networkSecurityConfig` |
| `companion/app/src/main/res/xml/network_security_config.xml` | 主源集：全局禁明文 |
| `companion/app/src/debug/res/xml/network_security_config.xml` | 仅 debug：localhost / 127.0.0.1 / 10.0.2.2 允许明文 |
| `companion/app/src/main/res/values/{strings,themes}.xml` | 应用名、NoActionBar 基础主题 |
| `companion/app/src/main/java/me/rerere/companion/CompanionApp.kt` | 持有 `AppContainer` |
| `.../MainActivity.kt` | `CompanionTheme { AppNav(container) }` |
| `.../core/AppError.kt` | `AppError.Http(code,message,retryAfterSeconds)` / `AppError.Network` / `AppResult` |
| `.../core/UrlValidator.kt` | `UrlError`、`UrlCheck`、`validate(raw, allowCleartext)` 归一化 |
| `.../core/AppContainer.kt` | 手写容器：`KeystoreSecureStore` + `SessionManager` + `RetrofitAuthApiFactory` + `HttpAuthRepository` |
| `.../data/store/SecureStore.kt` | `StoredSession`（@Serializable）与 `SecureStore` 接口 |
| `.../data/store/SessionCodec.kt` | 会话 JSON 编解码，`ignoreUnknownKeys`，坏数据返 null |
| `.../data/store/KeystoreSecureStore.kt` | AndroidKeyStore 别名 `companion.session`，AES-256-GCM，密文 `Base64(iv):Base64(ct)` 存 DataStore |
| `.../data/session/SessionManager.kt` | `state: StateFlow`、`restore/save/invalidate/clear`；过期或解密失败即清空 |
| `.../data/api/AuthApi.kt` | `POST api/auth/login`、`POST api/auth/logout` |
| `.../data/api/dto/AuthDtos.kt` | `LoginRequest`（`device_name`）、`LoginResponse`（`token_id`/`expires_at`） |
| `.../data/api/AuthInterceptor.kt` | 非登录请求注入 Bearer；首个 401 回调一次（AtomicBoolean） |
| `.../data/api/ErrorMapper.kt` | `detail` 字符串/中文句子/422 数组 → 可读文案；`Retry-After` 解析 |
| `.../data/api/AuthApiFactory.kt` | `fun interface AuthApiFactory` + `RetrofitAuthApiFactory`（10/15/15s 超时） |
| `.../data/repo/AuthRepository.kt` | `login` 映射 Http/IO 错误；`logout` 失败也清本地会话 |
| `.../feature/login/LoginViewModel.kt` | 校验→提交→429 倒计时；提交中重复点击只发一次 |
| `.../feature/login/LoginScreen.kt` | Material 3 表单、口令掩码、错误文案、按钮状态 |
| `.../feature/home/HomePlaceholderScreen.kt` | 4A 占位主页：显示地址 + 退出登录 |
| `.../ui/navigation/AppNav.kt` | `LaunchedEffect` 恢复会话；按 `state` 切登录/主页 |
| `.../ui/theme/Theme.kt` | `CompanionTheme(darkTheme = isSystemInDarkTheme())` |

### 新增（App 测试，7 个 suite / 53 例）

| 文件 | 例数 | 覆盖 |
| :--- | :--- | :--- |
| `core/UrlValidatorTest.kt` | 20 | 补斜杠、端口+路径、空白、明文策略、ftp、userinfo、query/fragment、空 host、空白输入、端口范围、点段/编码分隔符、主机大小写与末尾点、IPv6、反斜杠、IPv4 八位组、IPv4-mapped IPv6 |
| `data/store/SessionCodecTest.kt` | 4 | 往返、缺字段、坏 JSON、未知字段 |
| `data/session/SessionManagerTest.kt` | 6 | 有效恢复、恰好到期视为过期、读失败清空不抛、重复清退、restore 前清退、过期 Token 不清新会话 |
| `data/api/ErrorMapperTest.kt` | 7 | 已知 code、中文 detail、422 数组、空 body、message 优先、Retry-After 解析、429 携带秒数 |
| `data/api/AuthApiTest.kt` | 5 | 真实 JDK HttpServer 解析登录响应并断言 `device_name`、Bearer 注入、重复 401 只回调一次、登录 401 不算失效、新 Token 再次回调 |
| `data/repo/AuthRepositoryTest.kt` | 5 | 成功落库、401 不落库、IO→Network、登出失败仍清本地、非法 baseUrl 可读错误 |
| `feature/login/LoginViewModelTest.kt` | 6 | 非法地址不调仓库、归一化地址、401 文案、429 倒计时恢复、无 Retry-After 立即可提交、双击只调一次 |
| `testutil/{TestHttpServer,FakeAuthApi,FakeAuthRepository,FakeSecureStore,MainDispatcherRule}.kt` | — | 测试替身；HTTP 用 `com.sun.net.httpserver.HttpServer` |

### 修改（服务端）

| 文件 | 说明 |
| :--- | :--- |
| `companion/server/app/security/rate_limit.py` | 新增 `retry_after_seconds(key, window, now=)`：窗口内最早记录过期剩余秒，`max(1, ceil)` |
| `companion/server/app/routers/auth.py` | 429 响应带 `Retry-After` 头（值来自上述函数） |
| `companion/server/tests/test_auth_retry_after.py` | 4 例：空窗口为 0、按最早过期倒计时、不超窗口、真实登录 429 带合法头 |
| `companion/.gitignore` | `data/` → `/server/data/`（否则会忽略 App 源码包 `…/companion/data/`） |

### 文档

- `docs/superpowers/specs/2026-11-27-companion-app-phase4-design.md`（已批准设计）
- `docs/superpowers/plans/2026-11-27-companion-app-phase4a.md`（本块计划）
- 本日志与 `README.md` 索引

## 3. 关键实现

### 3.1 URL 策略
- 只接受 `http`/`https`；拒绝 userinfo、query、fragment；host 必须存在。
- `https` 一律允许；`http` 仅当 `allowCleartext`（= `BuildConfig.DEBUG`）且 host 属于
  `localhost` / `127.0.0.1` / `10.0.2.2`。归一化补尾斜杠（Retrofit baseUrl 要求）。
- 主源集 `network_security_config` 全局禁明文，debug 源集叠加三个本地域；不配置任何
  `X509TrustManager`/`HostnameVerifier`，证书校验完全交给系统。

### 3.2 会话存储
- `KeystoreSecureStore`：密钥不可导出，别名 `companion.session`，`AES/GCM/NoPadding`、
  128-bit tag、随机 IV；明文含 token，只在内存短暂存在。
- 解密/解析任一失败直接抛，`SessionManager.restore()` 捕获后 `clear()` 回登录页，
  不把异常泄给 UI，也不留下半截状态。
- 到期判定 `expiresAtMillis <= now`（等于即过期）。

### 3.3 HTTP 与错误
- `AuthInterceptor` 用 `encodedPath.endsWith("api/auth/login")` 识别登录请求：登录时
  不带旧 Token、登录返回 401 不触发清退，避免“口令错→被踢→再登录”死循环。
- 401 回调用 `AtomicBoolean` 只触发一次；`AppContainer` 在 `Dispatchers.IO` 的 appScope
  内 `invalidate()`，UI 因 `state` 变 null 自动回登录页。
- `ErrorMapper` 兼容三类历史格式：`detail` 字符串（code）、`detail` 中文句子（直接展示）、
  `detail` 对象数组（422 → “请求参数不合法”）；新接口的 `message` 字段优先。

### 3.4 429 倒计时
- 服务端 `Retry-After` 取“窗口内最早一条记录还剩多少秒”，`ceil` 且至少 1。
- App 只在 `code == "rate_limited"` 且 `retryAfterSeconds != null && > 0` 时倒计时；
  每秒递减（ticker 可注入，测试走虚拟时间），归零恢复可点。缺头时只显示通用文案。

## 4. 验证结果（本机实测）

| 场景 | 命令 | 结果 |
| :--- | :--- | :--- |
| 4A 全量单测 | `cd companion/app && .\gradlew.bat testDebugUnitTest` | **BUILD SUCCESSFUL**，7 suites / **53 tests** / 0 failures |
| 4A 构建 | `cd companion/app && .\gradlew.bat assembleDebug assembleRelease` | **BUILD SUCCESSFUL**；debug `companion-app-debug.apk` 13,318,140 B；release `companion-app-release-unsigned.apk` 9,590,604 B |
| 服务端全量 | `C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest -q`（仓库根） | **452 passed in 29.89s**（448 + 4 例 Retry-After） |
| 429 定向 | `… -m pytest companion/server/tests/test_auth_retry_after.py companion/server/tests/test_auth.py -q` | **18 passed** |

关键红灯记录（TDD 证据）：
- `UrlValidatorTest` 首次运行 → `Unresolved reference 'UrlCheck'`（先写测试后写实现）。
- `AuthApiTest` 首次运行 → `Unresolved reference 'dto' / 'RetrofitAuthApiFactory'`。
- `LoginViewModelTest` → `Unresolved reference 'scheduler'`（`MainDispatcherRule` 缺 `scheduler` 访问器）。
- `test_auth_retry_after.py` → `ImportError: cannot import name 'retry_after_seconds'`。

## 5. 与设计的偏差（已裁决）

| # | 偏差 | 原因 | 代价 |
| :--- | :--- | :--- | :--- |
| 1 | 不引入 `navigation-compose`，用 `session == null` 切屏 | 依赖未缓存、4A 只有两屏 | 4B 起层级变多时需要自建栈或补依赖 |
| 2 | 不使用 MockWebServer，测试用 JDK `com.sun.net.httpserver.HttpServer` | 依赖未缓存（且当时 Maven TLS 失败） | 无法模拟慢响应/连接中断等时序，4B 视需要再评估 |
| 3 | `compileSdk = release(37) { minorApiLevel = 2 }`，`targetSdk = 35` | 本机只有 android-37.0/37.2 平台 | 与根工程 `targetSdk 37` 不同；上架前需统一 |
| 4 | AGP 9.4 内置 Kotlin，移除 `org.jetbrains.kotlin.android` 插件 | 该插件在 AGP 9 下直接报错 | 与旧模板写法不同，需在 4B 保持 |
| 5 | APK 名为 `companion-app-debug.apk` | `rootProject.name = "companion-app"` | 文档/脚本引用需按实际名 |
| 6 | `LoginViewModel` 注入单调时钟 `elapsedMillis`（默认 `SystemClock.elapsedRealtime()`），按截止时刻每 250ms 重算 | 计划用 `Clock`+250ms 轮询；后台冻结后 tick 停但时间仍走，截止时刻算法能自动纠正 | 超过 3600s 的 `Retry-After` 按 3600s 展示（防服务端异常导致永久禁用） |
| 7 | `companion/.gitignore` 的 `data/` 改为 `/server/data/` | 会误伤 `app/src/main/java/…/companion/data/` | 以后新增其他 `data/` 目录需再补规则 |
| 8 | `AppContainer.logout()` 用应用级 `appScope`，而非 UI `rememberCoroutineScope` | 旋转会取消 UI 协程，导致登出没执行、本地 Token 残留 | 登出期间无 loading 态，重复点击会多一次空清退 |
| 9 | 401 去重按“已通知的 Token”记录（`AtomicReference<String?>`），提升到 `RetrofitAuthApiFactory` 实例 | 布尔标志在进程内永不复位，二次登录后的新 Token 再失效会“失聪”（复审 P1）；按 Token 去重天然支持重新登录 | 同一 Token 永远只清退一次；旧 Token 的迟到 401 由 `invalidate(failedToken)` 再校验一次，不会误杀新会话 |
| 10 | `AuthInterceptor` 仅在请求实际带 `Bearer` 时清退 | 无凭据的 401 与本地会话无关，避免误踢 | 无 |
| 11 | `SessionManager` 全部读写加 `Mutex`；`invalidate(failedToken)` 在锁内比对当前 Token | 401 与新登录并发会错位；restore 前的 401 会残留有效 Token；迟到的旧 401 不能清掉新会话 | 重复无条件清退多一次空删除（幂等，无副作用） |

## 6. 真机 / 真实环境待办（未验证，不得视为通过）

1. 安装 APK 到 Android 8.0+ 真机，确认启动不崩、登录页渲染正常。
2. Keystore 链路：首次写入后杀进程重启、冷启动解密；清除 App 数据后重新登录；确认
   卸载重装后旧密文不会导致崩溃（应回登录页）。
3. HTTPS：用真实域名 + 有效证书登录；确认自签证书被系统拒绝（不得出现任何绕过）。
4. 401/4401 实测：服务端吊销 Token 后，App 下一次请求应回登录页且只回一次。
5. 429：连续错误口令触发限速，确认按钮倒计时与服务端 `Retry-After` 一致。
6. debug 明文开关：`10.0.2.2:8000` 模拟器可登录；release 包同一地址应被拒。

## 7. 首轮独立审查与修复（4A 内完成）

独立审查结论：**有条件通过**，无 P0/P1，6 项安全红线全部通过。以下问题已在本块内修复，未留到 4B。

| # | 审查发现 | 修复 |
| :--- | :--- | :--- |
| 1 | `device_name` 因 kotlinx 默认不编码默认值而**未发出**，服务端记空设备名 | Json 加 `encodeDefaults = true`；`AuthApiTest` 断言请求体含 `"device_name":"android"` |
| 2 | URL 允许 `:0` / `:99999` 端口与 `..` 点段，OkHttp 随后抛参数异常 | 端口限 `1..65535`；拒绝 `.`/`..`/`%2e`/`%2f`/`%5c`；拒绝反斜杠；主机小写、去末尾点、IPv6 保留方括号 |
| 3 | `apiFactory.create()` 在 `try` 外，非法 baseUrl 直接冒泡 | 纳入 `try`，映射 `invalid_server_url` → “服务器地址无效” |
| 4 | 429 用 tick 递减，进程冻结后剩余秒数偏长 | 改截止时刻 + 250ms 重算；上限 3600s |
| 5 | `catch (Exception)` 吞掉 `CancellationException` | `SessionManager`/`AuthRepository`/`LoginViewModel` 先 rethrow 再兜底 |
| 6 | `invalidate()` 依赖内存 state，restore 前的 401 会残留 Token | 无条件清存储并置空；新增“restore 前清退”用例 |
| 7 | `SessionManager` 写操作无并发保护 | 全部入口加 `Mutex` |
| 8 | 401 去重是“每 client 一次”，且未校验 Bearer | 标志提升到工厂实例；仅对带 `Bearer` 的 401 清退 |
| 9 | 登出用 `rememberCoroutineScope`，旋转会取消、Token 未清 | 改为 `AppContainer.logout()`（应用级 `appScope`） |
| 10 | 登录成功后口令留在 Activity 作用域 VM，登出会回填 | 成功即清 `password`；测试断言 |
| 11 | 设计与实现不符：`compileSdk=35`、`keystore.properties.example` 不存在、`ApiClientFactory` 命名 | 设计 §3 改为 37.2 并说明原因；删除不存在的模板；注明 4A 命名 |

修复后复测：App **7 suites / 49 tests / 0 failures**；`assembleDebug assembleRelease` 均 **BUILD SUCCESSFUL**（debug 13,312,372 B / release 9,590,604 B）；服务端未再改动，仍为 **452 passed**。

### 7.1 第二轮复审（定向）与 P1 回归修复

第二轮结论：**不通过**。10/11 项修对，但第 8 项（401 去重）的改法引入 P1 回归：
`AtomicBoolean` 提升到 `Application` 级工厂后**进程内永不复位**，同一进程“401 清退 → 重新登录 →
新 Token 再 401”时 `compareAndSet(false,true)` 恒为 false，**不再清退**（4A 里等于唯一逃生通道失效）。

修复（本块内完成，不留 4B）：
- 去重从布尔改为 **已通知的 Token**（`AtomicReference<String?>`）：Token 变化即视为新会话，
  二次登录后 401 重新生效；同一 Token 仍只清退一次。
- `SessionManager.invalidate(failedToken)` 在 `Mutex` 内再比对当前 Token：**旧 Token 的迟到 401
  不能清掉刚登录的新会话**（复审 P3-1）。
- 新增用例：`AuthApiTest`「不同 Token 会再次通知」（父版本会失败）、
  `SessionManagerTest`「过期 Token 不清新会话」。
- 顺带修复审 P3：`create()` 的 `IllegalArgumentException` 捕获收窄到只包工厂创建
  （避免 kotlinx `SerializationException` 被误报为地址无效）；URL 增加 IPv4 八位组范围校验
  （`999.999.999.999` 拒绝）、允许 IPv4-mapped IPv6；设计 §5/§7 的 `Debouncer` 与白名单遗漏已回填。

第二轮复测：App **7 suites / 53 tests / 0 failures**；`assembleDebug assembleRelease` →
**BUILD SUCCESSFUL**（debug 13,318,140 B / release 9,590,604 B）；服务端 **452 passed**。

### 7.2 第三轮收尾确认

结论：**通过**。按 Token 去重 + `invalidate(failedToken)` 锁内复核双向闭合；两个新用例在父提交下均必失败
（真回归，非恒真）；`state==null` 时跳过带 Token 清退不可达（无 Token 就不发 Bearer），登出的无条件
清退路径不变。复测：53 例 / 452 passed / 双构建成功 / `git status` 干净。仅余两条 P3：
服务端若复用同一 Token 字符串会“失聪”（`token_id` 每次不同，实际不可达）；旧 Token 迟到 401 会覆盖去重键，
但此时会话已清空，无副作用。

审查提出但**有意留到 4B**的项：
- 每次 `create()` 新建 `OkHttpClient`（4A 仅 1–2 个请求；4B 改单例复用并统一超时）。
- `ErrorMapper` 尚未覆盖 4C/4D 的 `invalid_pair_code`/`pair_code_expired`/`device_not_found` 等码。
- 大 `Retry-After` 只做 1h 展示上限，未做“跨重启恢复倒计时”。

## 8. 下一步（4B）

- 角色字段：API 暴露 `model_id` 与搜索三态 `web_search_mode`；自动总结保持置灰“未实现”。
- 会话：`client_msg_id` 幂等（`turn_key = uuid5(NS_URL, "chat:{cid}:{client_msg_id}")`）、
  `conversation.turn_state` 真正广播（`turn_key` 守卫旧轮）、`PUT` 纠正已落库 USER 文本
  （不自动重生成），`done`/`turn_end` 带 `user_message_id`。
- App 侧：角色/会话列表与聊天页，沿用本块的 `AppContainer`、错误映射与会话门控。
