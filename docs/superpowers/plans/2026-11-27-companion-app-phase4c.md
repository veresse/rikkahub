# Phase 4C（设备管理 + 遥控）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 App 能列出设备与在线状态、配对/改名/绑定/撤销，并在遥控页通过 `/app/ws` 安全地下发音量、角度、动作、待机、急停与校准命令，且绝不把 `remote.ack` 当成动作成功。

**Architecture:** 服务端只补一处：`GET /api/devices` 返回 `online`（复用 `device_link_is_online`）。App 侧新增 WS 下行发送能力（`WsFrame.Writable` + `AppWsManager.send`）、设备/动作目录两个仓库、设备管理页与遥控页两个 feature 包；遥控命令全部由 `RemoteViewModel` 生成，UI 只读状态。

**Tech Stack:** Kotlin 2.x、Jetpack Compose (Material 3 Expressive)、Retrofit + kotlinx.serialization、OkHttp WebSocket、JUnit4 + kotlinx-coroutines-test、FastAPI + pytest。

**Spec:** `docs/superpowers/specs/2026-11-27-companion-app-phase4-design.md`（§7.3、§8、§8.1、§9 4C、§13 #1）

**协议：** `companion/docs/protocol_v2.md` §4.2–4.7（下行命令）、§5.1（`action.result`）、§8（`remote.*`、`remote.ack`、10 次/秒限速）
**动作库：** `companion/shared/actions.json`（18 个动作、4 个类别、`limits`、`supports`）

## Global Constraints

- `pan ∈ [-90, +90]`、`tilt ∈ [-20, +45]`、`volume ∈ [0,100]`、校准偏移 `∈ [-30,30]`；滑条本地即限幅，不下发越界值。
- 滑条：拖动中只改本地 UI；停止 200ms 或松手才下发；同一控件新命令覆盖旧命令（服务端 LWW，客户端不排队）。
- `remote.ack(ok=true)` 只表示「已受理/已入队」；动作成功/失败/超时只认 `action.result`（或 `device.report`）。
- 设备离线、`ConnectionState != Connected`、`isOnline == false` 时所有遥控命令不得发出；急停也不得假装成功。
- 离线设备不调用 `remote.get_status`（服务端会直接回 `device_offline`）。
- 不引入 WebView、不跳过 TLS 校验、不落盘/打印 Token 与任何 Key；`companion/app` 内不得出现模型/ASR/TTS/搜索 Key。
- 根 RikkaHub 工程只读：不得改根 `settings.gradle.kts`、根 `build.gradle.kts`、`app/`、根 `gradle/`。
- 所有中文文件以 UTF-8 写入；不要用 PowerShell `Set-Content` 改写。
- 每完成一块：`cd companion\app; .\gradlew.bat testDebugUnitTest assembleDebug`；服务端 `python -m pytest -q`；然后写迁移日志并更新 `v2_migration_log/README.md` 索引。
- 每轮结束后开一个只读子智能体审查，P0/P1 必须先修。

## Review Focus

- **滑条高频下发**：拖动 1 秒可产生几十次变化；服务端限 10 次/秒。预期：200ms 防抖后仍可能触发 `rate_limited`，结果条必须显示原因，且下一次防抖继续正常工作（不能一次限流后永久封死）。
- **在途离线**：列表显示在线后设备掉线，命令收到 `remote.ack(ok=false,error=device_offline)`。预期：面板立即置灰、显示离线原因、清空「发送中」。
- **他人/重复结果**：`action.result` 可能来自 LLM（`source=llm`）或设备重传同一 `action_id`。预期：不属于本页命令的结果不冒充「你的动作成功」；同一 `action_id` 只结算一次。
- **断线在途**：命令发出后 WS 断开/重连。预期：pending ack 与 awaiting result 全部清空，结果条显示「未确认」，不残留「发送中」。
- **目录失败**：`/api/actions/catalog` 返回 401/网络错误。预期：动作区空态 + 重试按钮；已连接且在线时急停仍可用，其余动作禁用。
- **校准与上报竞争**：校准保存后设备 `device.report` 带新的 `pan_offset/tilt_offset`。预期：不覆盖用户正在拖动、尚未保存的滑条草稿。

---

## 文件结构

服务端：

- Modify `companion/server/app/routers/devices.py`（`_serialize_device` 增 `online`）
- Test `companion/server/tests/test_devices.py`

App 数据层：

- Create `companion/app/src/main/java/me/rerere/companion/data/api/dto/DeviceDtos.kt`
- Create `.../data/api/dto/CatalogDtos.kt`
- Modify `.../data/api/CompanionApi.kt`
- Create `.../data/repo/DeviceRepository.kt`
- Modify `.../core/AppContainer.kt`
- Modify `.../data/ws/WsTransport.kt`、`.../data/ws/OkHttpWsConnector.kt`、`.../data/ws/AppWsManager.kt`
- Test `.../test/.../data/repo/DeviceRepositoryTest.kt`、`.../data/ws/AppWsManagerTest.kt`、`.../data/ws/OkHttpWsConnectorTest.kt`（若不存在则新建）

App 导航：

- Modify `.../ui/navigation/AppRoute.kt`、`AppRouteSaver.kt`、`AppNav.kt`
- Test `.../ui/navigation/NavigationViewModelTest.kt`（路由编解码）

App 设备页：

- Create `.../feature/devices/DevicesViewModel.kt`、`DevicesScreen.kt`
- Test `.../test/.../feature/devices/DevicesViewModelTest.kt`

App 遥控页：

- Create `.../feature/remote/RemoteViewModel.kt`、`RemoteScreen.kt`
- Create `.../feature/remote/RemoteCommands.kt`（JSON 构造 + 错误文案映射，纯函数，便于测试）
- Test `.../test/.../feature/remote/RemoteViewModelTest.kt`、`RemoteCommandsTest.kt`

文档：

- `docs/语音机器人需求文档/v2_migration_log/30_phase4_4c_devices_remote.md`、`README.md` 索引

---

### Task 1: 服务端设备列表返回 `online`

**Files:**
- Modify: `companion/server/app/routers/devices.py:27-30,73-91`
- Test: `companion/server/tests/test_devices.py`

**Interfaces:**
- Consumes: `get_device_link(device_id)`、`device_link_is_online(link)`（均已存在于 `services/device_registry.py`）
- Produces: `GET /api/devices` 每项新增 `online: bool`；`is_active == False` 的设备恒为 `false`

- [ ] **Step 1: 写失败测试**

在 `test_devices.py` 追加（复用文件内 `_new_device` 与 `register_device_link`）：

```python
async def test_device_list_reports_online_from_live_link(client, auth_headers):
    _, pair = await _new_device(client, auth_headers)
    device_id = pair.json()["device_id"]

    listing = await client.get("/api/devices", headers=auth_headers)
    row = next(d for d in listing.json() if d["id"] == device_id)
    assert row["online"] is False  # 没有 /device/ws 链路

    class ReadyLink:
        ready = True
        closing = False
        closed = False

    link = ReadyLink()
    register_device_link(device_id, link)
    try:
        listing = await client.get("/api/devices", headers=auth_headers)
        row = next(d for d in listing.json() if d["id"] == device_id)
        assert row["online"] is True

        link.closing = True
        listing = await client.get("/api/devices", headers=auth_headers)
        row = next(d for d in listing.json() if d["id"] == device_id)
        assert row["online"] is False
    finally:
        unregister_device_link(device_id, link)
        reset_device_links()


async def test_revoked_device_is_never_online(client, auth_headers):
    _, pair = await _new_device(client, auth_headers)
    device_id = pair.json()["device_id"]

    class ReadyLink:
        ready = True
        closing = False
        closed = False

        async def close(self, code, reason):
            pass

    link = ReadyLink()
    register_device_link(device_id, link)
    try:
        await client.delete(f"/api/devices/{device_id}", headers=auth_headers)
        listing = await client.get("/api/devices", headers=auth_headers)
        row = next(d for d in listing.json() if d["id"] == device_id)
        assert row["is_active"] is False
        assert row["online"] is False
    finally:
        unregister_device_link(device_id, link)
        reset_device_links()
```

若 `test_devices.py` 未导入 `unregister_device_link`，在文件头 `from companion.server.app.services.device_registry import (...)` 中补上。

- [ ] **Step 2: 运行确认失败**

Run: `cd companion\server; ...\python.exe -m pytest -q tests/test_devices.py -k online`
Expected: FAIL（`KeyError: 'online'`）

- [ ] **Step 3: 实现**

`devices.py` 导入：

```python
from companion.server.app.services.device_registry import (
    device_lifecycle_lock,
    device_link_is_online,
    get_device_link,
)
```

`_serialize_device` 返回字典中加：

```python
"online": bool(device.is_active)
and device_link_is_online(get_device_link(device.id)),
```

（`bool(...) and ...` 保证已撤销设备即使链路对象还在也不显示在线。）

- [ ] **Step 4: 跑测试**

Run: `...\python.exe -m pytest -q tests/test_devices.py`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `...\python.exe -m pytest -q`
Expected: 425 + 2 passed

- [ ] **Step 6: Commit**

```bash
git add companion/server/app/routers/devices.py companion/server/tests/test_devices.py
git commit -m "feat(companion-server): expose device online state in device list"
```

---

### Task 2: `/app/ws` 支持下行发送

**Files:**
- Modify: `companion/app/src/main/java/me/rerere/companion/data/ws/WsTransport.kt`
- Modify: `.../data/ws/OkHttpWsConnector.kt`
- Modify: `.../data/ws/AppWsManager.kt`
- Test: `.../src/test/java/me/rerere/companion/data/ws/AppWsManagerTest.kt`

**Interfaces:**
- Produces:
  - `interface WsSink { fun send(text: String): Boolean }`
  - `data class WsFrame.Writable(val sink: WsSink) : WsFrame`
  - `AppWsManager.send(text: String): Boolean`（未连接/已断开返回 false）

- [ ] **Step 1: 写失败测试**

在 `AppWsManagerTest` 增加（`FakeConnector` 已支持任意 `WsFrame` 序列）：

```kotlin
private class RecordingSink : WsSink {
    val sent = mutableListOf<String>()
    override fun send(text: String): Boolean {
        sent += text
        return true
    }
}

@Test
fun `send goes to the open socket and is refused after close`() = runTest {
    val sink = RecordingSink()
    val connector = FakeConnector(
        mutableListOf(flowOf(WsFrame.Open, WsFrame.Writable(sink), WsFrame.Closed(1011, "lost"))),
    )
    val manager = AppWsManager(connector, { session }, {}, backgroundScope, now = { 0L })

    assertFalse(manager.send("""{"type":"remote.get_status"}"""))

    manager.start()
    runCurrent()
    // 连接在 Closed 后已进入重连等待，sink 必须失效。
    assertFalse(manager.send("""{"type":"remote.get_status"}"""))
    assertTrue(sink.sent.isEmpty())

    manager.stop()
}

@Test
fun `send works while the socket is open`() = runTest {
    val sink = RecordingSink()
    val connector = FakeConnector(
        mutableListOf(flowOf(WsFrame.Open, WsFrame.Writable(sink), WsFrame.Text("""{"v":2,"type":"device.status","device_id":"d1","online":true}"""))),
    )
    val manager = AppWsManager(connector, { session }, {}, backgroundScope, now = { 0L })
    manager.start()
    runCurrent()

    assertTrue(manager.send("""{"type":"remote.get_status","request_id":"r1","device_id":"d1"}"""))
    assertEquals(1, sink.sent.size)

    manager.stop()
    assertFalse(manager.send("""{"type":"remote.get_status"}"""))
}
```

第一条测试里 `Closed` 之后 `runLoop` 会进入 `Retrying` 等待；`runCurrent()` 之后 sink 已清空。若 `FakeConnector` 的流在 `Closed` 后自然结束，也满足断言。

- [ ] **Step 2: 运行确认失败**

Run: `cd companion\app; .\gradlew.bat testDebugUnitTest --tests "*AppWsManagerTest*"`
Expected: 编译失败（`WsSink`/`WsFrame.Writable`/`send` 不存在）

- [ ] **Step 3: 实现传输层**

`WsTransport.kt` 追加：

```kotlin
/** 已建立连接的可写句柄；连接断开后调用 `send` 返回 false。 */
fun interface WsSink {
    fun send(text: String): Boolean
}
```

并在 `WsFrame` 中追加（**不修改** `Open`，避免动 4B 的既有测试）：

```kotlin
/** 握手完成且可写；与 [Open] 配对出现，断开后失效。 */
data class Writable(val sink: WsSink) : WsFrame
```

`OkHttpWsConnector`：在 `onOpen` 里 `trySend(WsFrame.Open)` 之后加

```kotlin
trySend(WsFrame.Writable(WsSink { socket.send(it) }))
```

- [ ] **Step 4: 实现 `AppWsManager.send`**

在 `AppWsManager` 增加字段：

```kotlin
@Volatile
private var sink: WsSink? = null
```

- `is WsFrame.Writable ->` 分支：`sink = frame.sink`，其余行为不变（不改 `_connection`）；
- `is WsFrame.Closed`、`is WsFrame.Failed`、`collect` 结束、`catch`、`stop()`、`finally` 都要 `sink = null`；
- 新增：

```kotlin
/** 连接不可写时返回 false，调用方必须把它当失败处理。 */
fun send(text: String): Boolean = sink?.send(text) ?: false
```

注意顺序：`Closed` 分支先把 `sink` 置空，再判断 4401。

- [ ] **Step 5: 跑测试**

Run: `.\gradlew.bat testDebugUnitTest --tests "*AppWsManagerTest*"`
Expected: PASS（含 4B 既有 10 个用例）

- [ ] **Step 6: Commit**

```bash
git add companion/app/src/main/java/me/rerere/companion/data/ws companion/app/src/test/java/me/rerere/companion/data/ws/AppWsManagerTest.kt
git commit -m "feat(companion-app): allow app ws to send remote commands"
```

---

### Task 3: 设备与动作目录数据层

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/DeviceDtos.kt`
- Create: `.../data/api/dto/CatalogDtos.kt`
- Modify: `.../data/api/CompanionApi.kt`
- Create: `.../data/repo/DeviceRepository.kt`
- Modify: `.../core/AppContainer.kt`
- Test: `.../src/test/java/me/rerere/companion/data/repo/DeviceRepositoryTest.kt`

**Interfaces:**
- Produces:

```kotlin
@Serializable data class DeviceDto(
    val id: String,
    val name: String = "",
    @SerialName("protocol_version") val protocolVersion: Int = 2,
    @SerialName("firmware_version") val firmwareVersion: String = "",
    val capabilities: List<String> = emptyList(),
    @SerialName("bound_assistant_id") val boundAssistantId: String? = null,
    @SerialName("bound_conversation_id") val boundConversationId: String? = null,
    @SerialName("is_active") val isActive: Boolean = true,
    val volume: Int = 70,
    @SerialName("idle_enabled") val idleEnabled: Boolean = true,
    @SerialName("pan_offset") val panOffset: Double = 0.0,
    @SerialName("tilt_offset") val tiltOffset: Double = 0.0,
    val state: String = "",
    val online: Boolean = false,
    @SerialName("last_seen_at") val lastSeenAt: Long? = null,
)

@Serializable data class PairCodeDto(
    val code: String,
    @SerialName("expires_in") val expiresIn: Int,
)

@Serializable data class DeviceBindBody(
    @SerialName("device_id") val deviceId: String,
    @SerialName("bound_assistant_id") val boundAssistantId: String? = null,
    @SerialName("bound_conversation_id") val boundConversationId: String? = null,
)

interface DeviceRepository {
    suspend fun list(): AppResult<List<DeviceDto>>
    suspend fun createPairCode(assistantId: String? = null, conversationId: String? = null): AppResult<PairCodeDto>
    suspend fun bind(deviceId: String, assistantId: String?, conversationId: String?): AppResult<Unit>
    suspend fun rename(deviceId: String, name: String): AppResult<Unit>
    suspend fun revoke(deviceId: String): AppResult<Unit>
    suspend fun catalog(): AppResult<ActionCatalogDto>
}
```

- [ ] **Step 1: 写失败测试**

`DeviceRepositoryTest.kt`（照 `ChatRepositoryTest` 的 `manager(url)` + `TestHttpServer` 写法）：

```kotlin
@Test
fun `lists devices with online flag`() = runTest {
    TestHttpServer().use { server ->
        server.enqueue(200, """[{"id":"d1","name":"客厅","online":true,"volume":60,"state":"idle"}]""")
        val repo = HttpDeviceRepository(RetrofitApiFactory({ manager.state.value }, {}), manager)
        val result = repo.list()
        val device = (result as AppResult.Ok).value.single()
        assertTrue(device.online)
        assertEquals(60, device.volume)
    }
}

@Test
fun `pair code parses expiry seconds`() = runTest { /* enqueue {"code":"123456","expires_in":120} */ }

@Test
fun `bind sends assistant and conversation ids`() = runTest {
    // enqueue 200 {"status":"bound"}，断言 server.lastRequestBody 含两个 id
}

@Test
fun `catalog parses categories and action supports`() = runTest {
    // enqueue 最小 catalog：一个 category、一个 action(supports intensity/speed)、limits
    // 断言 action.maxDurationMs、supports.repeat 缺省为 false
}

@Test
fun `device_offline error maps to readable message`() = runTest {
    // enqueue 409 {"detail":"device_offline"} -> error.message == "设备不在线"
}
```

- [ ] **Step 2: 运行确认失败**

Run: `.\gradlew.bat testDebugUnitTest --tests "*DeviceRepositoryTest*"`
Expected: 编译失败

- [ ] **Step 3: 实现 DTO / API / 仓库**

`CompanionApi` 追加：

```kotlin
@GET("api/devices")
suspend fun listDevices(): List<DeviceDto>

@POST("api/devices/pair-code")
suspend fun createPairCode(@Body body: PairCodeRequest): PairCodeDto

@PUT("api/devices/bind")
suspend fun bindDevice(@Body body: DeviceBindBody): StatusDto

@PUT("api/devices/{id}")
suspend fun renameDevice(@Path("id") id: String, @Body body: DeviceRenameBody): StatusDto

@DELETE("api/devices/{id}")
suspend fun revokeDevice(@Path("id") id: String): StatusDto

@GET("api/actions/catalog")
suspend fun actionCatalog(): ActionCatalogDto
```

请求体：`PairCodeRequest(@SerialName("assistant_id") val assistantId: String?, @SerialName("conversation_id") val conversationId: String?)`、`DeviceRenameBody(val name: String)`。

`CatalogDtos.kt`：

```kotlin
@Serializable data class ActionCatalogDto(
    val version: String = "",
    val categories: Map<String, String> = emptyMap(),
    val limits: CatalogLimitsDto = CatalogLimitsDto(),
    val actions: List<CatalogActionDto> = emptyList(),
)
@Serializable data class CatalogLimitsDto(
    val pan: AxisLimitDto = AxisLimitDto(),
    val tilt: AxisLimitDto = AxisLimitDto(-20.0, 45.0),
    val volume: VolumeLimitDto = VolumeLimitDto(),
)
@Serializable data class AxisLimitDto(val min: Double = -90.0, val max: Double = 90.0)
@Serializable data class VolumeLimitDto(val min: Int = 0, val max: Int = 100, val default: Int = 70)
@Serializable data class CatalogActionDto(
    val name: String,
    @SerialName("label_zh") val labelZh: String = "",
    val category: String = "",
    val supports: ActionSupportsDto = ActionSupportsDto(),
    @SerialName("max_duration_ms") val maxDurationMs: Int = 3000,
)
@Serializable data class ActionSupportsDto(
    val intensity: Boolean = false,
    val speed: Boolean = false,
    val repeat: Boolean = false,
)
```

`HttpDeviceRepository` 继承 `SessionApi`，每个方法一行 `call { ... }`。

- [ ] **Step 4: 接入容器**

`AppContainer` 增加 `val deviceRepository: DeviceRepository = HttpDeviceRepository(apiFactory, sessionManager)`。

- [ ] **Step 5: 跑测试**

Run: `.\gradlew.bat testDebugUnitTest --tests "*DeviceRepositoryTest*"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add companion/app/src/main/java/me/rerere/companion/data companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt companion/app/src/test/java/me/rerere/companion/data/repo/DeviceRepositoryTest.kt
git commit -m "feat(companion-app): add device and action catalog repositories"
```

---

### Task 4: 设备管理页

**Files:**
- Modify: `.../ui/navigation/AppRoute.kt`、`AppRouteSaver.kt`、`AppNav.kt`
- Create: `.../feature/devices/DevicesViewModel.kt`、`DevicesScreen.kt`
- Test: `.../src/test/java/me/rerere/companion/feature/devices/DevicesViewModelTest.kt`、`.../ui/navigation/NavigationViewModelTest.kt`

**Interfaces:**
- Produces:
  - `AppRoute.Devices`、`AppRoute.Remote(val deviceId: String, val deviceName: String)`
  - `DevicesViewModel(repository, assistantRepository, conversationRepository, deviceStatuses: StateFlow<Map<String, DeviceStatus>>, isOnline: () -> Boolean, tickMillis: Long = 1_000)`
  - `DevicesUiState(devices: List<DeviceRow>, loading: Boolean, error: String?, pairCode: PairCodeUi?, busyId: String?, binding: BindingUi?, snackbar: String?)`
  - `DeviceRow(device: DeviceDto, online: Boolean)`
  - `sealed interface DevicesEvent { data class OpenRemote(val deviceId: String, val name: String) : DevicesEvent }`

- [ ] **Step 1: 路由编解码测试**

在 `NavigationViewModelTest` 追加：`AppRoute.Devices` 与 `AppRoute.Remote("d1","客厅")` 经 `encodeRouteForTest`/`decodeRouteForTest` 往返相等；未知 type 仍回 `AppRoute.Assistants`。

- [ ] **Step 2: 运行确认失败**

Run: `.\gradlew.bat testDebugUnitTest --tests "*NavigationViewModelTest*"`
Expected: 编译失败（路由不存在）

- [ ] **Step 3: 实现路由**

`AppRoute` 追加 `data object Devices` 与 `data class Remote(val deviceId: String, val deviceName: String)`；`AppRouteSaver` 的 `encodeRoute`/`decodeRoute` 增加 `devices`（无参数）与 `remote`（`a=deviceId`、`b=deviceName`）分支。

- [ ] **Step 4: 写 ViewModel 失败测试**

`DevicesViewModelTest` 使用假仓库（参照 `FakeConversationRepository` 风格，记录调用）：

```kotlin
@Test fun `merges live device status over rest snapshot`() = runTest { /* dto online=false + status online=true -> row.online=true */ }
@Test fun `pair code counts down and clears at zero`() = runTest { /* advanceTimeBy(120_000) -> pairCode == null，提示已过期 */ }
@Test fun `rename trims and rejects blank`() = runTest { /* blank -> 不发请求，snackbar 提示 */ }
@Test fun `revoke marks device inactive and drops its live status`() = runTest { /* 成功后 row 消失或显示已撤销 */ }
@Test fun `bind passes assistant and conversation`() = runTest { /* 断言仓库收到两个 id */ }
@Test fun `write actions are refused while offline`() = runTest { /* isOnline=false -> 无仓库调用 */ }
@Test fun `device status offline overrides rest online`() = runTest { /* 权威离线 */ }
```

- [ ] **Step 5: 运行确认失败**

Run: `.\gradlew.bat testDebugUnitTest --tests "*DevicesViewModelTest*"`
Expected: 编译失败

- [ ] **Step 6: 实现 `DevicesViewModel`**

要点：

- `init { refresh() }`；`refresh()` 在 `loading` 中禁止重入；错误进 `error`。
- `row.online = deviceStatuses.value[id]?.online ?: dto.online`；`deviceStatuses` 用 `viewModelScope.launch { collect }` 合并。
- 所有写操作先 `if (!isOnline()) { snackbar = "当前离线，无法操作"; return }`。
- `createPairCode()`：成功启动 1s ticker 递减 `remainingSeconds`，归零清空并置 `expired = true`；重新生成先取消旧 ticker。
- `bind(...)` 从 `assistantRepository.list()` 与 `conversationRepository.list(assistantId)` 取选项；保存成功刷新设备列表。
- `revoke` 走二次确认（UI 弹窗），成功后 `refresh()`。
- `emit(DevicesEvent.OpenRemote)` 供导航使用。

- [ ] **Step 7: 实现 `DevicesScreen`**

Material 3 Expressive：`TopAppBar`（标题「设备」、返回、刷新）；`CardGroup` 分段：每台设备一张 `ListItem` 风格行（名称、在线/离线徽标、固件版本、绑定角色名、`state` 中文映射），行尾菜单：重命名 / 绑定 / 遥控 / 撤销；空态「还没有设备」+ 「生成配对码」；配对码弹窗显示 6 位码与倒计时（`剩余 1:58`），过期显示「已过期，请重新生成」；撤销弹窗文案「设备会立即断开，需恢复出厂后重新配对」。离线原因横幅复用 `OfflineBanner` 风格。

- [ ] **Step 8: 接线导航**

- `AppNav`：`AppRoute.Devices` 用 `navigationViewModel.ownerFor(route)` + `viewModel(key = "devices")`；`onBack` 用现有 `lastOrNull() == route` 守卫；`DevicesEvent.OpenRemote` 收到后 `pushRoute(AppRoute.Remote(...))`。
- `AssistantsScreen` 顶栏在「刷新」旁加 `TextButton(onClick = onDevices) { Text("设备") }`；`AppNav` 传 `onDevices = { pushRoute(AppRoute.Devices) }`。
- `AppRoute.Remote` 暂时只渲染占位 `Text("遥控")`（Task 5/6 替换），保证本任务可编译、可返回。

- [ ] **Step 9: 跑测试 + 构建**

Run: `.\gradlew.bat testDebugUnitTest assembleDebug`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add companion/app/src/main/java/me/rerere/companion/feature/devices companion/app/src/main/java/me/rerere/companion/ui/navigation companion/app/src/test/java/me/rerere/companion
git commit -m "feat(companion-app): add device management page"
```

---

### Task 5: 遥控命令核心（防抖、ack、结果匹配、门控）

**Files:**
- Create: `.../feature/remote/RemoteCommands.kt`
- Create: `.../feature/remote/RemoteViewModel.kt`
- Test: `.../src/test/java/me/rerere/companion/feature/remote/RemoteCommandsTest.kt`、`RemoteViewModelTest.kt`

**Interfaces:**
- Consumes: `AppWsManager.send`（Task 2）、`DeviceRepository.catalog`（Task 3）、`WsEvent.*`（已存在）、`ConnectionState`
- Produces:

```kotlin
enum class RemoteResultKind { INFO, DONE, FAILED, TIMEOUT }
data class RemoteResult(val text: String, val kind: RemoteResultKind)

data class RemoteUiState(
    val deviceName: String = "",
    val deviceOnline: Boolean = false,
    val offlineReason: String = "",
    val connection: ConnectionState = ConnectionState.Stopped,
    val catalogLoading: Boolean = false,
    val catalogError: String? = null,
    val categories: Map<String, String> = emptyMap(),
    val actions: List<CatalogActionDto> = emptyList(),
    val limits: CatalogLimitsDto = CatalogLimitsDto(),
    val volume: Int = 70,
    val mutedBefore: Int = 70,
    val pan: Double = 0.0,
    val tilt: Double = 0.0,
    val idleEnabled: Boolean = true,
    val intensity: Int = 2,
    val speed: Int = 2,
    val repeat: Int = 1,
    val sendingAction: String? = null,
    val result: RemoteResult? = null,
)

class RemoteViewModel(
    private val deviceId: String,
    private val deviceName: String,
    private val repository: DeviceRepository,
    private val ws: AppWsManager,
    private val isOnline: () -> Boolean,
    private val debounceMillis: Long = 200,
    private val ackTimeoutMillis: Long = 5_000,
) : ViewModel()
```

公开方法：`start()`、`onVolumeChange(Int)`、`commitVolume()`、`toggleMute()`、`onPanTiltChange(Double, Double)`、`commitPanTilt()`、`playAction(String)`、`stopAll()`、`toggleIdle()`、`previewCalibration(Double, Double)`、`commitCalibrationPreview()`、`saveCalibration(Double, Double)`、`refreshCatalog()`、`dismissResult()`。

- [ ] **Step 1: 写命令构造测试（纯函数）**

`RemoteCommandsTest`：

```kotlin
@Test fun `servo move clamps to protocol limits`() {
    val json = remoteServoMove(requestId = "r1", deviceId = "d1", pan = -120.0, tilt = 90.0, speed = 500)
    // 断言 pan == -90.0, tilt == 45.0
}
@Test fun `action play carries selected params`() { /* name/intensity/speed/repeat/source=app */ }
@Test fun `calibration clamps offsets to 30`() { /* 45 -> 30.0, -31 -> -30.0 */ }
@Test fun `unknown ack error becomes readable text`() {
    assertEquals("设备离线", remoteAckError("device_offline"))
    assertEquals("操作太频繁，请稍后再试", remoteAckError("rate_limited"))
    // 未知 code 不直出英文：assertEquals("遥控指令失败", remoteAckError("weird_code"))
}
```

`RemoteCommands.kt` 提供 `remoteGetStatus`、`remoteActionPlay`、`remoteServoMove`、`remoteVolumeSet`、`remoteIdleSet`、`remoteActionStop`、`remoteCalibratePreview`、`remoteCalibrateSave`（均返回 `String`，内部 `buildJsonObject` + `"v":2`，包含 `request_id`、`device_id`、`ts` 由调用方传入或内部 `System.currentTimeMillis()`），以及 `remoteAckError(code: String?): String`。

- [ ] **Step 2: 运行确认失败**

Run: `.\gradlew.bat testDebugUnitTest --tests "*RemoteCommandsTest*"`
Expected: 编译失败

- [ ] **Step 3: 实现 `RemoteCommands.kt`，再写 ViewModel 测试**

`RemoteViewModelTest`（`AppWsManager` 是 final class，测试直接构造真实实例：`AppWsManager(FakeConnector, { session }, {}, backgroundScope)`；`FakeConnector`/`RecordingSink` 从 `AppWsManagerTest` 复制到 `testutil`）：

```kotlin
@Test fun `volume is debounced to one command`() = runTest {
    // onVolumeChange(30..60 共 5 次) -> advanceTimeBy(199) 无 send; advanceTimeBy(1) 恰好一条且 volume=60
}
@Test fun `volume commit sends immediately and cancels pending`() = runTest { }
@Test fun `offline device blocks every command`() = runTest { /* deviceOnline=false -> sent empty，result=FAILED 提示离线 */ }
@Test fun `ack ok shows accepted not done`() = runTest { /* ack ok+action_id -> result.kind=INFO，文本含“已受理” */ }
@Test fun `action result completes the matched command only once`() = runTest {
    // playAction -> ack(action_id=a1) -> result(a1,done) -> 再来一次 result(a1,done) 不改变结果
}
@Test fun `action result from llm is not reported as your action`() = runTest { /* source=llm -> 文本含“机器人” */ }
@Test fun `ack timeout reports unconfirmed`() = runTest { /* advanceTimeBy(5000) 无 ack -> FAILED/未确认 */ }
@Test fun `disconnect clears pending and shows unconfirmed`() = runTest { /* connection 变 Retrying -> result 提示未确认，sendingAction=null */ }
@Test fun `rate limited ack is shown and does not lock the panel`() = runTest { /* ack rate_limited 后再拖动仍发得出去 */ }
@Test fun `device offline ack flips panel offline`() = runTest { /* ack device_offline -> deviceOnline=false，后续 send 不再调用 ws */ }
@Test fun `calibration report does not overwrite unsaved slider draft`() = runTest {
    // preview offsets 5/-3 未保存时收到 device.report pan_offset=0 -> 草稿仍为 5/-3
}
@Test fun `emergency stop clears awaiting results`() = runTest { /* playAction 在途 -> stopAll -> 旧 result 不再写成成功 */ }
```

- [ ] **Step 4: 实现 `RemoteViewModel`**

关键实现约束：

1. **可测试性**：构造函数注入 `send: (String) -> Boolean` 而不是 `AppWsManager`？——采用两者折中：主构造函数参数为 `ws: AppWsManager`，但内部只调用 `ws.send(...)`、读 `ws.connection`/`ws.devices`/`ws.events`；测试用 `AppWsManager(FakeConnector, ...)` 真实实例（`AppWsManagerTest` 已证明可测），避免为测试新建接口。若实现时发现 `AppWsManager` 难以驱动，允许抽 `RemoteTransport` 接口并让 `AppWsManager` 实现，但需同步更新计划偏差说明。
2. **防抖**：`private var volumeJob: Job?`；`onVolumeChange` 只更新 state 并 `volumeJob?.cancel(); volumeJob = viewModelScope.launch { delay(debounceMillis); sendVolume() }`；`commitVolume` 取消 job 后立即 `sendVolume()`。
3. **request_id/action_id**：`requestId = UUID.randomUUID()`；`Pending(requestId, kind, name, sentAt)` 存在 `MutableMap<String, Pending>`；`ackTimeout` 由 `viewModelScope.launch { delay(ackTimeoutMillis); if (still pending) markUnconfirmed() }`。
4. **ack 处理**：`ok=false` → 移除 pending、`result = FAILED(remoteAckError(error))`、`error=="device_offline"` 时 `deviceOnline=false`；`ok=true && actionId != null` → pending 从 `awaitingAck` 移到 `awaitingResult[actionId]`，`result = INFO("已受理：$name")`；`ok=true && actionId == null` → `result = INFO("已下发：$name")`。
5. **result 处理**：`awaitingResult.remove(actionId)` 命中 → `done/failed/timeout` 映射 DONE/FAILED/TIMEOUT，文本带动作名；未命中且 `actionId` 在 `settled` 集合 → 忽略；未命中首次 → `INFO("机器人动作：…")`（`source=llm` 时明确标注）。
6. **断线**：收集 `ws.connection`，当值不是 `Connected` 时清空 pending/awaiting，`sendingAction = null`，若之前有在途命令则 `result = FAILED("连接已断开，命令未确认")`。
7. **设备在线**：`start()` 时从 `DeviceRepository.list()` 取本设备 `online`；之后 `ws.devices[deviceId]` 更新；`device.status(online=false,reason)` 权威离线并显示 `reason`（映射为中文：`device_revoked`→已撤销、其它原样/通用）。
8. **`remote.get_status`**：仅当 `deviceOnline && connection == Connected && isOnline()` 时在 `start()` 发一次；收到 `device.report` 回填 volume/pan/tilt/idle（**仅当用户没有未提交草稿时才覆盖**：`volumeDirty`/`panTiltDirty` 标记在发送成功后清除）。
9. **限幅**：入口即 `coerceIn`；`volume` 用 `limits.volume`，`pan/tilt` 用 `limits.pan/tilt`。
10. **急停**：`stopAll()` 不受“目录未加载”限制，但仍要求连接/在线；发送后清空 `awaitingResult`（服务端会把在途动作结算为 stopped）。

- [ ] **Step 5: 跑测试**

Run: `.\gradlew.bat testDebugUnitTest --tests "*RemoteViewModelTest*" --tests "*RemoteCommandsTest*"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add companion/app/src/main/java/me/rerere/companion/feature/remote companion/app/src/test/java/me/rerere/companion/feature/remote
git commit -m "feat(companion-app): add remote control command state machine"
```

---

### Task 6: 遥控页 UI 与导航

**Files:**
- Create: `.../feature/remote/RemoteScreen.kt`
- Modify: `.../ui/navigation/AppNav.kt`
- Test: 以 ViewModel 测试为主；本任务只加“门控计算”纯函数测试（`RemoteScreenStateTest` 或并入 `RemoteCommandsTest`）：`canUseRemote(connection, deviceOnline, networkOnline)` 与 `canStop(...)`。

**Interfaces:**
- Consumes: Task 5 的 `RemoteUiState` 与回调
- Produces:
  - `fun canUseRemote(connection: ConnectionState, deviceOnline: Boolean, networkOnline: Boolean): Boolean`（`Connected && deviceOnline && networkOnline`）
  - `fun canStop(connection: ConnectionState, deviceOnline: Boolean, networkOnline: Boolean): Boolean`（同 `canUseRemote`，但不依赖目录/动作加载状态）
  - `@Composable fun RemoteScreen(state, networkOnline, onBack, onVolumeChange, onVolumeCommit, onToggleMute, onPanTiltChange, onPanTiltCommit, onPlayAction, onStop, onToggleIdle, onPreviewCalibration, onCommitCalibrationPreview, onSaveCalibration, onRefreshCatalog, onDismissResult)`

- [ ] **Step 1: 写门控失败测试**

```kotlin
@Test fun `remote is disabled when socket is not connected`() {
    assertFalse(canUseRemote(ConnectionState.Retrying(1, 1000), deviceOnline = true, networkOnline = true))
}
@Test fun `emergency stop requires connection but not catalog`() {
    assertTrue(canStop(ConnectionState.Connected, deviceOnline = true, networkOnline = true))
    assertFalse(canStop(ConnectionState.Connected, deviceOnline = false, networkOnline = true))
}
```

- [ ] **Step 2: 实现 UI**

结构（自上而下）：

1. `TopAppBar`：设备名 + 在线徽标 + 返回；离线时整页叠加 `OfflineBanner("设备离线：<reason>")`。
2. 状态条：`连接中/已连接/重连中( attempt )` + 设备 `state`（idle/listening/thinking/speaking/error 中文）。
3. 音量：`Slider(0f..100f)`，`onValueChange` → `onVolumeChange`，`onValueChangeFinished` → `onVolumeCommit`；旁边静音 `IconButton`（值为 0，取消静音恢复 `mutedBefore`）；说明文字「静音不影响机器人听你说话」。
4. 急停：全宽红色 `Button`「急停归位」，`enabled = canStop(...)`。
5. 动作区：`intensity/speed/repeat` 三个 `SingleChoiceSegmentedButtonRow`；`categories` 顺序渲染分组（`basic/social/emotion/mimic`），每类 `FlowRow` 按钮，`enabled = canUseRemote && catalogError == null`；空态显示重试。
6. 角度：pan 滑条（-90..90）、tilt 滑条（-20..45），显示当前值与「松开后下发」提示；`onValueChangeFinished` → commit。
7. 待机小动作：`Switch` → `onToggleIdle`。
8. 校准：`OutlinedButton("校准")` 打开 `AlertDialog`，两个滑条（偏移 -30..30），「预览」拖动即 `onPreviewCalibration`（内部 200ms 防抖），「保存」→ 二次确认 → `onSaveCalibration`。
9. 底部结果条：`state.result` 用 `Surface(tonalElevation)` + 颜色区分 INFO/DONE/FAILED/TIMEOUT；`LaunchedEffect` 6 秒后自动消失或点「知道了」。

- [ ] **Step 3: 接线导航**

`AppNav` 的 `AppRoute.Remote`：`ownerFor(route)` + `viewModel(key = "remote-${route.deviceId}")`；`BackHandler(enabled = state.sendingAction != null) { }`（在途命令不因弹栈丢 ack；与 4B 的写请求策略一致）；返回按钮同守卫。

- [ ] **Step 4: 跑测试 + 构建**

Run: `.\gradlew.bat testDebugUnitTest assembleDebug assembleRelease`
Expected: BUILD SUCCESSFUL

- [ ] **Step 5: Commit**

```bash
git add companion/app/src/main/java/me/rerere/companion/feature/remote companion/app/src/main/java/me/rerere/companion/ui/navigation companion/app/src/test/java/me/rerere/companion/feature/remote
git commit -m "feat(companion-app): add remote control screen"
```

---

### Task 7: 文档与整体验证

**Files:**
- Create: `docs/语音机器人需求文档/v2_migration_log/30_phase4_4c_devices_remote.md`
- Modify: `docs/语音机器人需求文档/v2_migration_log/README.md`

- [ ] **Step 1: 全量验证**

```text
cd companion\server
...\python.exe -m pytest -q

cd ..\app
.\gradlew.bat testDebugUnitTest assembleDebug assembleRelease
```

记录真实输出（测试数、构建结果、耗时）。

- [ ] **Step 2: 写日志 30**

内容：范围（4C）、提交列表、改动文件、服务端 `online` 语义、WS 发送能力、200ms 防抖与 ack/timeout 状态机、ack≠success、结果条规则、离线门控、校准草稿保护、偏差（如是否抽 `RemoteTransport` 接口）、审查结论（含未修 P2）、未完成项（真机遥控、真实舵机角度、10 次/秒限速下的手感、恢复出厂流程未在固件验证）。

- [ ] **Step 3: README 索引**

在 29 后追加 `| 30 | Phase 4-4C：设备管理与遥控 | ... |`，写清测试数与待办。

- [ ] **Step 4: Commit**

```bash
git add docs
git commit -m "docs(companion): log phase 4c devices and remote"
```

---

## Self-Review 记录

- 规格覆盖：§8「设备」「遥控」两行逐项有任务（配对码倒计时→Task 4；音量/静音/急停/动作/角度/待机/校准/结果条→Task 5+6；离线门控→Task 5；在线初值→Task 1+4）。
- §9 4C 验收：服务端 `online` 用例（Task 1）、防抖（Task 5）、离线（Task 5）、ack-result 匹配（Task 5）。
- 类型一致性：`DeviceDto.online`、`RemoteUiState`、`remoteAckError`、`canUseRemote`/`canStop` 在引入任务中定义，后续任务只引用。
- 未覆盖（记录在案）：真机与真实舵机/固件联调、10 次/秒限速下持续拖动的手感、恢复出厂操作本身（固件侧）、多设备同时遥控。
