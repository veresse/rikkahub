# Companion Android App（Phase 4）设计

- 状态：**已批准**（2026-11-27；8 处缺口裁决见 §13，ASR 纠正采用“改文本、不自动重生成”）
- 日期：2026-11-27
- 分支：`feature/companion-robot-v2`
- 依据：`docs/语音机器人需求文档/v1_v2.md` Phase 4、`companion/docs/protocol_v2.md` §5/§8、`companion/docs/v1_to_v2_audit.md`
- 交付方式：纵向切片，4A → 4B → 4C → 4D，每块独立编译/单测/审查/迁移日志

## 1. 意图与为什么

v1 用网页管理机器人与角色；v2 要求用 Android 原生 App 完成同样的管理，并增加遥控面板、语音事件展示与设备在线状态。App 必须能在 Android 8.0+ 真机安装，但当前没有真机/模拟器，因此本阶段以 `assembleDebug` + JVM 单测作为可验证交付，真机步骤如实标为待办。

成功的样子：安装 Debug APK → 输入服务器 HTTPS 地址与管理员口令 → 登录 → 管理角色/会话、流式聊天、查看语音转写、生成配对码、遥控动作/音量/角度、导入备份；杀掉 App 后 Token 仍在（加密存储），令牌被撤销后自动回登录页。

## 2. 目标 / 非目标 / 反目标

### 目标

- 7 个页面：登录、角色、聊天与记忆、备份导入、设备、遥控、设置与素材包。
- Android Keystore 加密保存服务器地址与 App Token；绝不存任何模型/ASR/TTS Key。
- 动态 BaseURL + Bearer 注入；401 / WS 4401 统一清退并回登录。
- `/app/ws` 长连接：指数退避重连、网络切换感知、在线设备状态、动作结果、语音事件、会话生成状态。
- 聊天 SSE 流式展示、取消、幂等重试（`client_msg_id`）、语音转写纠正。
- 遥控：音量/角度 200ms 防抖或松手下发、离线整页置灰、急停、校准、结果反馈。
- JVM 单测覆盖 URL 校验、登录限流、401 清退、SSE 解析、WS 重连状态机、防抖、离线门控、聊天并发与幂等。

### 非目标

- ESP32 固件（Phase 5）、远程 OTA、iOS/平板专用布局、推送通知、聊天历史离线缓存。
- App 内渲染 Q 版动画素材（只做上传/版本管理）。
- 真机 UI 仪器测试与 4G 实测（无设备，标待办）。
- 记忆自动总结（服务端无实现方，UI 置灰）。
- 纠正转写后自动重生成（需要消息↔轮次映射，留待后续）。

### 反目标（即使“能跑”也算失败）

- 自定义 `X509TrustManager` / `HostnameVerifier` 或任何“跳过证书校验”开关。
- Token 明文落盘、出现在日志/URL/崩溃信息；App 内出现 API Key。
- WebView 套网页；无限转圈无错误原因；离线时控件仍可点。
- UI 把 `remote.ack` 当动作成功（它只代表入队）。

## 3. 约束

- `companion/app` 是完全独立的 Gradle 工程，**不加入根 `settings.gradle.kts`**，根 RikkaHub 工程只读。
- 工具链与本机缓存一致：AGP 9.4.0、Kotlin 2.4.21、Gradle 9.6.0、JDK 17；Compose BOM 2026.09.00。
- `minSdk = 26`、`compileSdk = 37.2`（`release(37) { minorApiLevel = 2 }`；本机仅安装 android-37.0/37.2，上架前再与根工程统一）、`targetSdk = 35`；UI 中文、Material 3。
- 构建机 SDK 在 `D:\Android\sdk`，只写入未跟踪的 `companion/app/local.properties`。
- Release 禁止明文流量；debug 仅允许 `localhost` / `127.0.0.1` / `10.0.2.2` 明文联调。
- 错误响应：**既有接口只保证 `detail`**（字符串码、中文句子或 422 数组），App 必须兼容全部三种；4B/4D 新增接口统一为 `{"detail": "<code>", "message": "<可读文本>"}`。

## 4. 方案与选择

| 方案 | 内容 | 取舍 |
| :--- | :--- | :--- |
| **A（选中）** | 单模块 MVVM + 手写 `AppContainer` + Keystore AES-GCM + DataStore | 无注解处理器、构建快、单测只依赖接口；7 页规模足够。装配代码需自己维护 |
| B | 单模块 + Hilt/KSP + EncryptedSharedPreferences | 注入样板少；`androidx.security-crypto` 维护模式，KSP 与 Kotlin/AGP 耦合、构建更重 |
| C | 多模块 `core/network`、`core/data`、`feature/*` + Hilt | 边界最清晰；4A 脚手架成本高，逐块审查慢 |

选择 A：满足规格的安全与可测试要求，把复杂度留在页面本身；接口化后未来拆模块不破坏调用方。

## 5. 总体结构

```
companion/app/
  settings.gradle.kts, build.gradle.kts, gradle.properties, gradle/wrapper/...
  local.properties            # 本机 SDK 路径，git 忽略
                              # release 签名由 CI 注入；4A 不引入签名模板文件
  src/main/AndroidManifest.xml
  src/main/java/me/rerere/companion/
    CompanionApp.kt            # 创建 AppContainer
    core/                      # AppContainer、AppError、AppResult、UrlValidator
    data/
      api/                     # Retrofit 接口、DTO、AuthInterceptor、ErrorMapper
      store/                   # SecureStore 接口 + KeystoreSecureStore
      repo/                    # Auth/Assistant/Chat/Device/Backup/Settings Repository
      ws/                      # AppWsManager、事件 DTO、重连策略
      sse/                     # SseParser
    ui/
      theme/                   # Material 3 主题、颜色、字号
      components/              # 加载/错误/空态、确认弹窗、分段卡片
      navigation/              # NavHost、路由
    feature/login|assistants|chat|backup|devices|remote|settings/
  src/test/java/...            # JVM 单测
```

- 单向数据流：`ViewModel` 暴露 `StateFlow<UiState>` + 一次性 `SharedFlow<UiEvent>`；Repository 返回 `AppResult<T>`。
- 依赖装配：`AppContainer` 持有 `SecureStore`、`SessionManager`、`ApiClientFactory`、各 Repository、`AppWsManager`；`CompanionApp` 创建，Activity 通过 `LocalAppContainer` 获取。
- 不引入 Hilt/KSP；测试用构造函数注入假实现。

## 6. 安全存储与会话

- `SecureStore` 接口：`serverUrl`、`token`、`username`、`tokenId`、`expiresAt`；`suspend fun load/save/clear`。
- `KeystoreSecureStore`：
  - AndroidKeyStore 别名 `companion.session`，AES-256-GCM，`setRandomizedEncryptionRequired(true)`；每次写入随机 IV。
  - 明文为一个 JSON（URL + Token + 元数据），密文与 IV Base64 后写入 Preferences DataStore（单 key）。
  - 不要求用户生物认证：App 需要后台重连。
  - 解密失败（密钥失效/跨设备恢复）→ 清空并回登录，不崩溃。
- `SessionManager`：内存态 `StateFlow<Session?>`；登录成功写 Store 并广播；`logout()` 先调 `/auth/logout`（失败也继续）→ 清 Store → 关 WS → 发导航事件。
- 401 清退：`AuthInterceptor` 仅对带 Bearer 的请求观察 401；触发 `SessionManager.invalidate()`；登录请求本身不触发。
- Token 过期时间在登录响应中；启动时若 `expiresAt` 已过，直接回登录（不做本地刷新）。

## 7. 网络、SSE 与 WebSocket

### 7.1 REST

- Retrofit + OkHttp + `kotlinx.serialization` converter。
- `ApiClientFactory.create(baseUrl, sessionProvider, onUnauthorized)`：URL 变更或登录成功后重建；`baseUrl` 末尾补 `/`，所有路径以 `api/` 开头。（4A 先落地为 `AuthApiFactory` / `RetrofitAuthApiFactory`，4B 扩展为覆盖全部接口的客户端工厂。）
- 超时：connect 10s、call 15s；聊天 SSE 使用独立 `OkHttpClient`（readTimeout 0、不自动重试）。
- 错误映射 `ErrorMapper`：
  - `detail` 是字符串：先查中文文案表（`invalid_credentials`、`rate_limited`、`device_offline`、`queue_full`、`angle_out_of_range`、`preview_expired` 等），未命中则原样显示该字符串。
  - `detail` 是数组（422）：映射为 `validation_error`，显示首条 `msg` 或“请求参数不合法”。
  - 有 `message` 字段时优先显示（新接口）。
  - 无 JSON body：按状态码给通用文案（401/403/404/429/5xx）。
- 服务器地址校验：release 只接受 `https://host[:port]`（可带路径前缀）；debug 额外接受 `http://localhost`、`http://127.0.0.1`、`http://10.0.2.2`，并在 UI 标注“仅调试”。
- 登录 429：读 `Retry-After`（秒）做倒计时；缺失时只显示“尝试过于频繁，请稍后再试”，不编造固定秒数。

### 7.2 聊天 SSE

- `POST /api/chat/completions`，`Accept: text/event-stream`，请求体 `{conversation_id, message, client_msg_id, regenerate_node_id?}`。
- `SseParser` 逐行处理 `data: <json>`，空行分帧；产出 `ChatEvent(status/content/reasoning/tool_result/usage/error/done)`。
- 取消 = `Call.cancel()` + 本地把当前助手消息标为“已取消”；服务端断连逻辑已有。
- **幂等（4B 服务端）**：`turn_key = uuid5(NAMESPACE_URL, "chat:{conversation_id}:{client_msg_id}")` 传入落库；同一 `client_msg_id` 命中已落库的助手节点时，直接回 `done(replayed=true, assistant_message_id, user_message_id)`，不再调用模型；同一 id 正在生成时回 `error=duplicate_in_progress`。确定性 id 落库，单 worker 之外也安全。
- `done` 增加 `user_message_id`（本轮落库的用户消息 id），供纠正转写使用；`replayed` 时 App 重拉会话渲染而不是追加流。

### 7.3 `/app/ws`

- `https` → `wss`，路径 `/app/ws`；`Authorization: Bearer <token>`（OkHttp 握手头）。
- `AppWsManager` 暴露：`StateFlow<ConnectionState>`、`StateFlow<Map<String, DeviceStatus>>`、`StateFlow<Map<String, TurnState>>`、`SharedFlow<WsEvent>`（`device.status`、`device.report`、`action.result`、`voice.event`、`conversation.turn_state`、`remote.ack`、`error`）。
- 重连：1s、2s、4s…上限 30s + ±20% 抖动；`ConnectivityManager` 网络恢复立即重试；主动登出/4401 不重连。
- 关闭码：4401 `token_revoked` → `SessionManager.invalidate()`；1011/1012 → 正常退避重连（服务端停机后恢复）。
- 心跳：依赖 uvicorn 25s ping（OkHttp 自动回 pong）；客户端 `pingInterval(25s)` 兜底。
- 遥控：`request_id = UUID`；维护 pending ack（5s 超时）；`action.result` 按 `action_id` 回填结果条；重复终态以第一次为准。
- **设备状态来源（服务端无初始快照）**：
  - 初值来自 `GET /api/devices`（4C 增加 `online` 字段）以及响应里已有的 `volume`/`state`/`last_report`。
  - 收到 `device.report` → 标记该设备在线并更新 `idle`/`volume`/`pan`/`tilt`。
  - `device.status(online=false, reason)` 为权威离线；`reason` 原样展示。
  - 离线设备不调用 `remote.get_status`（服务端要求链路存在，会直接失败）。

## 8. 页面与流程

| 页面 | 关键行为 | 依赖 |
| :--- | :--- | :--- |
| 登录 | 服务器地址 + 用户名/口令；格式校验；429 按 `Retry-After` 倒计时（缺失则通用提示）；成功后保存并进入角色页；退出登录撤销 Token | `POST /api/auth/login`、`POST /api/auth/logout` |
| 角色 | 列表/新增/编辑/删除（二次确认）；姓名、人设、头像、`model_id`、上下文条数、temperature/top_p、记忆开关（角色/全局；自动总结置灰标“未实现”）、`web_search_mode` 三态、动作联动开关 | `/api/assistants` CRUD（4B 扩字段） |
| 聊天与记忆 | 角色 → 会话列表/新建/删除；消息树当前分支；SSE 流式正文/思考/搜索状态/来源；取消；失败重试（`client_msg_id`）；`voice.event` 转写气泡与“已打断”标记；转写纠正；会话生成中提示（`conversation.turn_state`）；记忆查看/编辑/删除/开关 | `/api/conversations`、`/api/chat/completions`、`/api/memories`、`/app/ws` |
| 备份导入 | SAF 选 `.zip` → `preview` → 展示源版本、角色/会话/消息数、ID 冲突数、未迁移密钥（只列键名）、不支持项、过期时间 → 显式确认 → 结果报告；过期提示重选文件 | `/api/backup/preview`、`/confirm`（4D 扩字段） |
| 设备 | 列表与在线状态；生成配对码（倒计时）；重命名；绑定角色/会话；撤销（二次确认）；恢复出厂指引 | `/api/devices`（4C 加 `online`）、`/app/ws` |
| 遥控 | 在线状态栏；离线整页置灰；音量 0–100 + 静音（松手/200ms）；急停；18 动作按类别 + intensity/speed/repeat；pan −90..90、tilt −20..45 滑条（200ms 防抖）；待机小动作；校准预览/保存；底部结果条 | `/api/actions/catalog`、`/app/ws` remote.* |
| 设置与素材包 | Token 会话管理/撤销；模型/ASR/TTS/搜索 Key 只写掩码配置；素材包上传 ZIP/版本/状态；退出登录 | `/api/auth/tokens`、`/api/settings/*`（4D 新增）、`/api/assets/*`（4D 新增） |

### 8.1 交互细则

- 所有滑条：拖动中只更新本地 UI；停止 200ms 或松手才下发；同一控件新命令覆盖旧命令（`servo.move`/`volume.set` 服务端本身支持覆盖）。
- 遥控页命令在 `ConnectionState != Connected` 或目标设备离线时直接禁用，并显示具体原因。
- 动作按钮点击后立即进入“发送中”，收到 `remote.ack` 后转为“已受理”，收到对应 `action.result` 后显示成功/失败/超时。
- 聊天页发送中显示停止按钮；`conversation.turn_state` 驱动会话列表的“生成中”角标与发送按钮禁用。
- 语音转写：`asr_final` 以“语音输入”气泡显示在输入区上方；`turn_start` 提供 `conversation_id`，App 缓存 `turn_id → conversation_id`，4B 起 `asr_final` 也直接带 `conversation_id`。
- 转写纠正：只有收到 `turn_end`/`done` 里的 `user_message_id` 后才启用“纠正”；提交 `PUT …/messages/{mid}` 改文本，成功后局部刷新；**不自动重生成**，改完提示“回答未更新，可重新发送”。
- `interrupted` 标记显示在对应助手消息尾部。

## 9. 分块交付

| 块 | 范围 | 验收 |
| :--- | :--- | :--- |
| **4A** | 独立工程脚手架、主题/导航、`SecureStore`、`SessionManager`、`ApiClientFactory`、`ErrorMapper`、登录页、401 清退、启动恢复会话；**服务端**：登录 429 带 `Retry-After`（`retry_after_seconds`） | 单测 + `assembleDebug`；登录成功/失败/429/401/重启恢复用例；服务端限速用例 |
| **4B** | 角色 CRUD（含 `model_id`、`web_search_mode`）、会话列表/消息树、SSE 聊天、取消/重试、`AppWsManager`、语音事件、转写纠正、记忆；**服务端**：`client_msg_id` 幂等、`conversation.turn_state` 广播、assistant 字段扩展与 model 透传、`PUT` 消息文本、`done`/`turn_end` 增 `user_message_id` | 单测 + 编译；服务端新增用例全绿（幂等 replay、turn_state 守卫、model 透传、纠正权限/角色校验） |
| **4C** | 设备列表/配对/绑定/撤销、遥控全功能、校准、离线门控、动作结果匹配；**服务端**：`GET /api/devices` 增加 `online` | 单测 + 编译；防抖/离线/ack-result 匹配用例；设备列表 online 用例 |
| **4D** | 备份导入、Token 管理、Key 掩码设置、素材包上传/版本；**服务端**：preview 字段扩展 + TTL/`preview_expired`、设置与素材接口 | 单测 + 编译；上传大小/类型/脱敏用例；preview 过期用例 |

服务端缺口只在该块内补，不提前实现；每块结束更新迁移日志与 README。

## 10. 测试与验证

- JVM 单测（JUnit4 + kotlinx-coroutines-test + OkHttp MockWebServer）：
  - URL 校验（https/端口/路径/debug localhost）。
  - 登录成功/401/429（有/无 `Retry-After`）/503；倒计时计算与归零后行为。
  - 401 清退只触发一次；登录请求不触发。
  - SecureStore：假实现读写；Keystore 实现只在真机验证（标待办）。
  - ErrorMapper：`detail` 字符串 / 中文句子 / 422 数组 / 无 body / 有 `message`。
  - SSE 解析：多行 data、分帧、坏 JSON、`done`、`replayed`、取消。
  - 幂等：同一 `client_msg_id` 重试复用请求体并处理 `replayed`。
  - WS 重连：退避序列、4401 不重连、网络恢复立即重试、turn_state 更新。
  - 防抖：200ms 内多次只发最后一次；松手立即发。
  - 设备离线门控：离线时命令不发出，且不发 `remote.get_status`。
  - 转写纠正：无 `user_message_id` 时按钮禁用；提交体只含 `text`。
- 构建：`companion/app/gradlew.bat testDebugUnitTest assembleDebug`（Windows）或 `./gradlew`；真实记录耗时与输出。
- 仪器/真机：无设备，列出待办步骤（安装、HTTPS 证书、登录、4G 切换、遥控、语音、Keystore）。

## 11. 风险与待办

1. 依赖下载：AGP/Kotlin/Compose 需要网络；若版本不可用，回退到根工程缓存中已有版本，并在日志记录。
2. 无真机：UI 观感、Keystore 真机行为、4G/WS 切换只能待办。
3. 服务端改造面：4A/4B/4C/4D 各有一处小改（见 §9），必须与既有 448 条 pytest 一起回归；新端点用 `{detail,message}`，旧端点不动。
4. `remote.ack` 只代表入队，最终结果靠 `action.result`；UI 不能把 ack 当成功。
5. 素材包没有现成服务端存储格式；4D 先定义最小 ZIP + 版本元数据，动画渲染不在 App 内做。
6. 转写纠正只改文本、不重生成；历史回答与修正文本可能不一致，UI 必须提示。

## 12. 留给实现者

- Compose 具体组件组合、颜色/间距、图标、空态/错误文案、时间格式。
- DTO 字段命名与序列化细节（以真实路由响应为准）。
- 单测文件划分与假实现组织。
- 4D 设置/素材接口的最小 JSON 形状（在 4D 计划中定稿）。

## 13. 实现前核查裁决（8 处缺口）

独立实现者检查发现设计与真实代码有 8 处不符，逐条裁决如下。实施计划以本表为准，不得回到旧假设。

| # | 缺口 | 代码证据 | 裁决 |
| :--- | :--- | :--- | :--- |
| 1 | 冷启动/重连后无设备在线状态来源；`device.report` 无 `online` | `routers/devices.py:73-91` 无 online（但已有 `last_report`/`state`/`volume`）；`services/device_gateway.py:252-265` `public_report` 无 online；`services/app_gateway.py:146-179` 连接时不推快照 | **4C 服务端**：`GET /api/devices` 增加 `online`（`get_device_link` + `device_link_is_online`）；`/app/ws` 不推全量快照。App 规则见 §7.3，离线不调 `remote.get_status`（要求链路存在，`app_gateway.py:320-323`） |
| 2 | `conversation.turn_state` 只有白名单、无发布点 | `app_gateway.py:42-48`；`services/orchestrator.py:313-315` 冲突只回发起方 error 文本 | **4B 服务端**：生成开始/终态广播 `{conversation_id, state: idle\|generating, source, turn_id}`；用 `turn_key` 守卫，旧轮结束不得把新轮置 idle；语音轮次同样广播（`source=voice`） |
| 3 | 角色 `model_id`、搜索三态、自动总结与代码不符 | `models/assistant.py:19-23`；`routers/assistants.py:13-42`；`orchestrator.py:367-372` 不传 model；`deepseek_client.py:38` 已支持 `model` 参数；搜索判定在 `orchestrator.py:323` | **4B**：API 增加 `model_id` 并透传 `client.stream_chat(model=...)`（空值回落默认）；新增 `web_search_mode: off/auto/on`（off=不搜、auto=现有关键词、on=每轮强制）；自动总结**不实现**，置灰标注 |
| 4 | “ASR 文本纠正”语义未定（对方未答复，按推荐暂定） | `audio/pipeline.py:556` 只广播文本；`orchestrator.py:514-535` 生成结束才落库；`routers/conversations.py` 无编辑端点 | **4B**：`done`/`turn_end` 增 `user_message_id`；新增 `PUT /api/conversations/{cid}/nodes/{nid}/messages/{mid}`（仅 USER、仅改 text part）；拿到 id 后才允许纠正；**不自动重生成**，改后提示“回答未更新” |
| 5 | 备份预览字段不足；preview 缓存无 TTL | `importer/service.py:37-61` 仅计数/不支持项；`:17` 进程内缓存；`importer/parser.py:14-64` 无版本/冲突/密钥 | **4D 服务端**：preview 增 `source_version`、`id_conflicts{count,sample}`、`unmigrated_secrets`（仅键名）、`expires_at`；缓存 10 分钟 TTL，过期回 `preview_expired`；App 允许重选文件 |
| 6 | 统一的 `{detail,message}` 格式不存在 | 全仓无 `add_exception_handler`；`routers/assistants.py:50` 等处 `detail` 为中文句子；422 的 `detail` 是数组 | **不改旧接口**：App 兼容字符串/数组/可选 `message`（§7.1）；仅新增端点用完整格式；服务端统一中间件列为后续可选项，避免动既有 448 条测试 |
| 7 | 429 无 `Retry-After`，设计写死 60s | `routers/auth.py:68-75`；`security/rate_limit.py:38-39` 只返回布尔；窗口 5 次/300 秒（`config.py:62-63`） | **4A 服务端**：新增 `retry_after_seconds(key, window)`，登录 429 带 `Retry-After`；App 有头倒计时，无头只给通用提示 |
| 8 | 幂等命中重复 `client_msg_id` 的响应未定义 | `orchestrator.py:227` 每请求新 `turn_key`；`:471` `done` 只回 `assistant_message_id` | **4B**：`turn_key = uuid5(NS_URL, "chat:{cid}:{client_msg_id}")`；已落库直接回 `done(replayed=true)` 不调模型；同 id 生成中回 `error=duplicate_in_progress`；App 收到 replayed 后重拉会话 |
