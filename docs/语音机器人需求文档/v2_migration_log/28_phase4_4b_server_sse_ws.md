# 28 · Phase 4-4B（上）：角色 / 会话 / SSE 聊天 / WS / 转写纠正

- 状态：**已完成（含 App 独立审查修复；Task 9 见日志 29）**
- 日期：2026-11-27
- 分支：`feature/companion-robot-v2`
- 设计依据：`docs/superpowers/specs/2026-11-27-companion-app-phase4-design.md`
- 实施计划：`docs/superpowers/plans/2026-11-27-companion-app-phase4b.md`
- 前置：4A 登录 / 加密会话（日志 27，`01629f4e`）

## 1. 目标与范围

4B 的目标是让 App 具备“角色 → 会话 → 流式聊天”的完整纵向切片，并把语音/网页共用的服务端能力补齐：

- 角色：暴露 `model_id` 与搜索三态 `web_search_mode`；旧客户端 PUT 不得清空未提交字段。
- 会话：`client_msg_id` 幂等；`conversation.turn_state` 真正广播；`done`/`turn_end` 带 `user_message_id`。
- 转写纠正：`PUT` 只改已落库的 USER 文本，不自动重生成。
- App：REST 仓库、SSE 解析与取消、`/app/ws` 连接管理、角色列表/编辑页。
- 离线门控：断网时所有写操作与依赖网络的刷新必须不可点。

本日志覆盖服务端 Task 1–4 及修复、App Task 5–8 及独立审查修复。Task 9（会话列表 / 聊天页 / 记忆页）与最终审查见日志 29。

不在本块：4C 设备遥控、4D 备份/设置/素材、真机验收、任何模型/ASR/TTS/搜索 Key。

## 2. 提交与改动文件

### 2.1 服务端

| 提交 | 说明 |
| :--- | :--- |
| `228f7b0e` | 角色 `model_id` / `web_search_mode`、迁移 `0003`、导入导出与编排器读取 |
| `c9513512` | `client_msg_id` 幂等与 `turn_key = uuid5(NS_URL, "chat:{cid}:{client_msg_id}")` |
| `77686ac8` | `conversation.turn_state` 广播、语音轮次补 `user_message_id` |
| `4d7ca7bf` | `PUT /api/conversations/{conv}/nodes/{node}/messages/{message}` 纠正 USER 文本 |
| `70d6d10a` | 审查修复：幂等重放、`regenerate` 判定、中断/空回答覆盖、角色设置保全 |

主要文件：

- `companion/server/app/models/assistant.py`
- `companion/server/migrations/versions/0003_assistant_model_search.py`
- `companion/server/app/routers/assistants.py`、`conversations.py`、`chat.py`、`import_export.py`
- `companion/server/app/services/orchestrator.py`
- `companion/server/app/importer/parser.py`、`service.py`
- `companion/server/app/audio/binding.py`、`pipeline.py`
- 测试：`test_assistants_api.py`、`test_chat_model_search.py`、`test_migrations.py`、`test_chat_idempotency.py`、`test_turn_state.py`、`test_message_correction.py`、`test_audio_pipeline.py`、`test_importer.py`

### 2.2 App

| 提交 | 说明 |
| :--- | :--- |
| `dad6551a` | `CompanionApi`、Assistant/Conversation/Memory DTO 与仓库、`ApiCall` 错误映射 |
| `d66324f7` | `ChatRepository`、`SseParser`、`ChatEvent`、流式 Retrofit 工厂 |
| `9ed728de` | `AppWsManager`、`OkHttpWsConnector`、`WsEvent`、`WsTransport` |
| `4b9c69e0` | 角色列表/编辑页、手写 `AppRoute` / `AppNav` 栈 |
| `2a00ee8b` | 独立审查修复：SSE 取消、离线门控、WS 退避与状态清理、错误文案 |

主要文件：

- `companion/app/src/main/java/me/rerere/companion/data/api/{CompanionApi,AuthApiFactory}.kt`
- `.../data/api/dto/{AssistantDtos,ChatDtos,ConversationDtos,MemoryDtos}.kt`
- `.../data/repo/{ApiCall,AssistantRepository,ConversationRepository,MemoryRepository,ChatRepository}.kt`
- `.../data/sse/{ChatEvent,SseParser}.kt`
- `.../data/ws/{AppWsManager,OkHttpWsConnector,WsEvents,WsTransport}.kt`
- `.../feature/assistants/{AssistantsViewModel,AssistantsScreen,AssistantEditViewModel,AssistantEditScreen}.kt`
- `.../ui/navigation/{AppRoute,AppNav}.kt`
- `.../core/{AppContainer,ConnectivityMonitor}.kt`
- 测试：`AssistantRepositoryTest`、`ConversationRepositoryTest`、`MemoryRepositoryTest`、`AuthApiTest`、`ChatRepositoryTest`、`SseParserTest`、`AppWsManagerTest`、`AssistantsViewModelTest`、`AssistantEditViewModelTest` 及 `Fake*` 测试替身

## 3. 关键实现

### 3.1 服务端幂等与重放

- `turn_key` 由 `uuid5(NAMESPACE_URL, "chat:{cid}:{client_msg_id}")` 生成，同一 `client_msg_id` 稳定映射同一轮。
- 已落库的重复请求直接回 `done(replayed=true)`，不调用模型。
- 同一 `client_msg_id` 正在生成时回 `error=duplicate_in_progress`，避免重复落库或双写。
- 审查修复后：幂等判定**不看** `regenerate_node_id`，按消息 id 全局定位；中断/空回答不 replay，由 `_save_turn` 就地覆盖同一条消息，避免出现两条半截回答。

### 3.2 turn_state 与语音消息 id

- `_publish_turn_idle` 为同步函数，`publish_async` 负责后台广播；`generating` 用同步 `publish`，保证客户端在开始生成前就能收到状态。
- 事件带 `turn_key`（兼容旧字段 `turn_id`）；App 用 `turn_key` 守卫，旧轮迟到的 `idle` 不会清掉新轮的 `generating`。
- `done` 与语音 `turn_end` 补 `user_message_id`，App 才能对语音轮次做文本纠正。

### 3.3 转写纠正

- 新接口：`PUT /api/conversations/{conv_id}/nodes/{node_id}/messages/{message_id}`，body `{text}`。
- 只接受已落库的 `USER` 消息；替换第一个 `text` part，其余 part 原样保留；不触发重新生成。
- 错误响应使用新格式 `{detail, message}`：`node_not_found` / `message_not_found` / `not_user_message`。
- App 成功后只改本地文本并提示“已更新文本，回答未更新，可重新发送”。

### 3.4 App SSE

- `SseParser` 逐行解析，支持跨 chunk 切断、未知事件忽略、`done`/`error` 终态。
- 取消必须真正断连：使用 `Call` 而非仅取消协程，`currentCoroutineContext().job.invokeOnCompletion { call.cancel() }` 关闭 socket，服务端才能感知断流并停止生成。
- 读超时改为 **120s 空闲超时**（见偏差表），防止模型挂起后无限转圈。
- EOF 没有 `done`/`error` 时补 `ChatEvent.Failed("stream_closed")`，UI 不会把截断当成功。
- 服务端 `error` 字段只映射已知原因（`duplicate_in_progress`/`timeout`/`interrupted`/`model_error`），未知文本一律展示“生成失败，请稍后重试”，不把内部异常原文丢给用户。

### 3.5 App WebSocket

- 指数退避 1/2/4/8/16/30 秒；网络恢复用 epoch 计数器唤醒，连接尝试期间发出的恢复信号也不丢。
- 只有连接稳定存活 **30s** 才重置退避，避免“接受后立刻关闭”导致 1s 重连风暴。
- 新连接 `Open` 时清空 `turnStates`；`stop()` 同时清空 `devices`/`turnStates`，登出后不把上一个账号的状态留给下一个账号。
- `4401` 只清退 Token，不重连。
- 未知/坏帧静默丢弃；所有事件只做只读解析，不打印载荷。

### 3.6 离线门控

- 新增 `ConnectivityMonitor`（`ACCESS_NETWORK_STATE`），`AppContainer.isOnline: StateFlow<Boolean>`。
- 写操作（新建/编辑/删除角色、保存）与刷新在离线时禁用；页面顶部显示离线横幅。
- 网络恢复回调 `appWsManager.onNetworkAvailable()`，不必干等退避到点。

### 3.7 角色设置保全

- App DTO 保留 `avatar` 为 `JsonObject` 原样回传；`AssistantEditViewModel` 保存时带上原 `avatar`。
- 服务端仅在显式提供 `model_id`/`avatar` 时更新，旧客户端 PUT 不会把未提交字段清空。
- 角色编辑页新增 `loadFailed`：加载失败时禁止保存（否则空表单会覆盖原角色），提供重试/返回。

## 4. 实际验证

| 场景 | 命令 | 结果 |
| :--- | :--- | :--- |
| 服务端全量 | `cd companion\server; C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest -q` | **425 passed in 12.61s** |
| App 全量单测（Task 5–8 + 审查修复） | `cd companion\app; .\gradlew.bat testDebugUnitTest` | **BUILD SUCCESSFUL**，15 suites / **100 tests** / 0 failures |
| App 构建 | `cd companion\app; .\gradlew.bat assembleDebug assembleRelease` | **BUILD SUCCESSFUL** |
| Task 9 后全量 | 同上（测试 + 双构建） | **BUILD SUCCESSFUL**，18 suites / **119 tests**；详见日志 29 |

关键红灯记录：

- 服务端幂等测试首次运行暴露“重放时仍调用模型 / 空回答被重复插入”，由 `70d6d10a` 修复。
- App 审查前 `ChatRepositoryTest` 无法验证“取消后连接真的断开”：`TestHttpServer` 固定 Content-Length 一次写完；改为自建 chunked 挂起 handler + `CountDownLatch`。
- `AuthApiTest` 非法 UTF-8 用例一度删掉断言，审查后恢复。

## 5. 与设计的偏差（已裁决）

| # | 偏差 | 原因 | 代价 |
| :--- | :--- | :--- | :--- |
| 1 | SSE 读超时用 120s 空闲超时，而非设计中的 0（无限） | 模型/上游挂起时无限转圈不可接受；120s 足够长，又不至于让用户永远等 | 极慢模型可能被误判超时；需要真实服务端验证 120s 是否足够 |
| 2 | 取消用 `Call.cancel()` + `invokeOnCompletion`，而不是只取消 Flow 协程 | 仅取消协程不会打断阻塞中的 `readUtf8Line()`，服务端会继续生成并烧 token | 需要保证 `Call` 在正常完成时也会释放（已由响应体 `use` + 调用方关闭保证） |
| 3 | WS 退避重置只认“稳定 30s”，不认单帧流量 | 单帧流量无法证明连接长期可用；“接受后立刻关闭”会把退避永久打回 1s | 频繁短连接会持续退避到 30s |
| 4 | 不引入 `navigation-compose`，继续手写 `mutableStateListOf<AppRoute>` | 4B 层级仅三层，手写栈便于测试登录态重置 | 后续 4C/4D 页面变多时需要补依赖或继续维护手写栈 |
| 5 | 角色保存的 `avatar` 在 App 侧用 `JsonObject` 原样透传 | 避免 App 不认识新头像结构时把它清空 | DTO 与 UI 不解析头像内容，4D 素材页再处理 |
| 6 | 401 去重按 Token（`AtomicReference<String?>`）+ 锁内复核 | 4A 的布尔标志在二次登录后会“失聪” | 同一 Token 只清退一次；旧 Token 迟到 401 不会误杀新会话 |
| 7 | 服务端错误响应不改旧接口，仅新接口用 `{detail, message}` | 旧网页端/模拟器仍依赖 `detail` 字符串 | App 必须兼容 `detail` 字符串/中文句子/422 数组三种历史格式 |

## 6. App 独立审查与修复（`2a00ee8b`）

审查结论：**有条件通过**；P0×2、P1×9、P2×9。本块内已修复 P0/P1 与可快速闭合的 P2：

| # | 审查发现 | 修复 |
| :--- | :--- | :--- |
| 1 | SSE 取消只取消协程，socket 仍挂着，服务端继续生成 | `ChatRepository` 改用 `Call`，协程完成时 `call.cancel()`；新增真实挂起服务器的断连测试 |
| 2 | 中断/EOF 没有 `done` 时被当成成功，UI 永远“生成中” | EOF 无终态补 `stream_closed`；中途 `IOException` 转 `Failed`；`Call` 的 HTTP 错误统一映射 |
| 3 | 服务端 `error` 原文可能包含内部异常/上游细节 | `SseParser` 只映射已知 code，未知文案通用化 |
| 4 | 断网时仍可点保存/新建/删除/刷新 | 新增 `ConnectivityMonitor` + `online` 参数，所有写操作与刷新禁用并显示横幅 |
| 5 | `AssistantEditViewModel` 加载失败后仍可保存，空表单会覆盖原角色 | 增加 `loadFailed`，失败时禁用保存并提供重试 |
| 6 | `avatar` 编辑保存后被清空 | DTO/草稿/UI 状态透传 `JsonObject`；服务端仅显式提供时更新 |
| 7 | WS 退避在“接受后立刻关闭”时永远 1s，形成重连风暴 | 只有稳定 30s 才重置；新增虚拟时钟用例覆盖 1/2/4s |
| 8 | WS 重连后保留旧 `turnStates`，会话可能永久“生成中” | `Open` 时清空；`stop()` 清空 devices/turnStates |
| 9 | `onNetworkAvailable` 用一次性事件，连接尝试期间会丢 | 改为 epoch 计数器 `MutableStateFlow<Long>` |
| 10 | 每次请求新建 `OkHttpClient`，连接池/超时不一致 | `RetrofitApiFactory` 按 `baseUrl + readTimeout` 缓存；SSE 单独 120s 空闲超时 |
| 11 | 导航 `Saved` 与返回键并发时可能多弹一层 | 只在 `backStack.lastOrNull() == route` 时弹栈；`lastOrNull()` 防空栈崩溃 |
| 12 | 角色卡片用可点击的 `AssistChip` 展示标签，点了没反应 | 改为纯展示 `Tag`；离线时操作菜单/删除确认禁用 |

修复后复测：App **15 suites / 100 tests / 0 failures**；`assembleDebug assembleRelease` **BUILD SUCCESSFUL**；服务端未再改动，仍 **425 passed**。

## 7. 未完成 / 阻塞

- Task 9（会话列表 / 聊天页 / 记忆页 / SSE UI 接线）在本日志范围内未完成，见日志 29。
- 真机、真实 HTTPS、真实模型/ASR/TTS 均未验证；不得视为通过。
- 4C 设备在线初值、`remote.ack` 语义、动作成功判定尚未实现。

## 8. 下一步

1. 完成 Task 9：会话列表、流式聊天、记忆页与 `AppNav` 接线。
2. 独立审查 Task 9 与整块 4B，修复 P0/P1。
3. 4C：设备列表 / 在线状态 / 遥控动作，严格区分 `remote.ack` 与 `action.result`。
