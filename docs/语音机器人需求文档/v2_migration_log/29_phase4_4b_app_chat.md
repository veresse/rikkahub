# 29 · Phase 4-4B（App）：会话列表 / 流式聊天 / 记忆 / 导航作用域

- 状态：**已完成**（三轮独立审查修复均已合入；真机联调未做）
- 日期：2026-11-27
- 分支：`feature/companion-robot-v2`
- 设计依据：`docs/superpowers/specs/2026-11-27-companion-app-phase4-design.md`
- 实施计划：`docs/superpowers/plans/2026-11-27-companion-app-phase4b.md`
- 前置：日志 28（4B 服务端）；4A 登录/加密会话（日志 27，`01629f4e`）

## 1. 目标与范围

4B 的 App 侧目标：登录后能按「角色 → 会话 → 流式聊天」走完一条可用链路，并在弱网/离线/
多端同时操作时状态可控：

- 会话列表：新建、删除、刷新、按 WS `turn_state` 显示「生成中」角标。
- 聊天页：SSE 流式文本/推理、停止、重试、转写文本纠正、语音草稿、记忆入口。
- 记忆页：全局记忆与角色记忆的增删改，标注归属。
- 导航：手写栈 + 每路由独立 `ViewModelStore`，弹栈/登出即销毁页面 ViewModel（SSE 必须真正断）。
- 门控：离线时所有写操作不可点且 ViewModel 二次校验；生成中/加载中禁止发送与刷新。

不在本轮：设备列表与遥控（4C）、备份/设置/素材（4D）、真机与真实云 Key 联调。

## 2. 提交与改动文件

| 提交 | 说明 |
| :--- | :--- |
| `b41988fa` | Task 9：会话列表、流式聊天、记忆页、`AppNav` 接线 |
| `7f3bd326` | 日志 28 + README 索引 |
| `fe8124c7` | 第一轮审查修复：按路由 ViewModel 作用域、流状态机、重试定位、reload 代次、WS 清态、旋转恢复、语音归属、错误文案 |
| `746997ca` | 第二轮审查修复：写请求在途禁止弹栈、`turn_end` 补 `conversation_id`、`markFailed` 身份守卫、加载中禁止发送、429 倒计时、返回节流、滚动跟随、`ErrorMapper` 混合文案、补测试 |
| `92eca45e` | 第三轮审查修复：列表代次防「复活」、push 重置返回节流、根页返回窗口、返回回调同步读状态、旧语音轮 `turn_end` 守卫、取消清理先发事件、贴底判定 |

主要文件：

- 导航：`companion/app/src/main/java/me/rerere/companion/ui/navigation/{AppNav,NavigationViewModel,AppRouteSaver}.kt`
- 聊天：`.../feature/chat/{ChatScreen,ChatViewModel,ConversationsScreen,ConversationsViewModel,MemoriesScreen,MemoriesViewModel}.kt`
- 数据：`.../data/repo/{ChatRepository,ConversationRepository,MemoryRepository}.kt`、`.../data/sse/*`、`.../data/ws/AppWsManager.kt`、`.../data/api/ErrorMapper.kt`
- 测试：`ChatViewModelTest`、`ConversationsViewModelTest`、`MemoriesViewModelTest`、`NavigationViewModelTest`、`AppWsManagerTest`、`ChatRepositoryTest`、`ErrorMapperTest`、`FakeConversationRepository`
- 服务端（本轮附加）：`companion/server/app/audio/pipeline.py`、`companion/docs/protocol_v2.md`、`companion/server/tests/test_audio_pipeline.py`

## 3. 关键实现

### 3.1 按路由 ViewModel 作用域

`NavigationViewModel`（Activity 作用域）持有 `Map<AppRoute, ViewModelStore>`，`ownerFor(route)`
返回稳定的 `ViewModelStoreOwner`。路由出栈时 `clearRoute(route)`、登出时 `clearAll()`，触发
`onCleared()`，于是：

- 聊天页的 SSE 流被取消，`HttpChatRepository` 的 `invokeOnCompletion { call.cancel() }` 关闭 socket；
- WS 收集协程停止；
- 重新进入同一会话会新建 ViewModel 并重新拉详情，看到服务端最新状态。

路由是值类型（`data class`），旋转重建后恢复的 route 能重新对上同一个 store，正在生成的流不中断。

### 3.2 导航栈保存与恢复

- `rememberSaveable(saver = AppRouteBackStackSaver)` 保存路由栈；路由编码为 JSON 字符串，
  损坏/未知类型回退到角色列表。
- `activeTokenId` 也保存：旋转时 Token 未变 → 不清栈；Token 变化 → `clearAll()` 并把栈重置为角色列表，
  避免看到上一个账号的页面。
- `popRoute` 400ms 节流：连点两次返回不会一次弹两级（第二次点击会落在上一页的返回按钮上）。
  `pushRoute` 会重置节流，避免「弹栈 → 立刻进入新页 → 立刻返回」被吞掉；弹回根页后的
  400ms 内仍吞掉返回，否则深度 2 的第二下会直接结束 Activity（清空 ViewModelStore、
  取消在途写请求），窗口过后才恢复系统默认退出。
- 两个 `rememberSaveable` 使用**位置作用域**（自定义 `key` 已被 Compose 废弃），代码注释了
  「新的 rememberSaveable 只能追加在它们之后」的顺序约束。

### 3.3 聊天状态机

- `PendingTurn` 记录本轮 `client_msg_id`、用户/助手临时消息 key。
- `send()` 守卫：离线 → 提示；`sending || generating` → 忽略；`loading` → 忽略（否则作废正在加载的历史）。
- `retry(messageKey)` 按被点击气泡的 `client_msg_id` 重发，复用幂等键；离线/生成中拒绝。
- `markFailed()` 先校验 `pendingTurn === turn`，旧轮次的迟到异常不会把新轮次标失败。
- `runTurn()` 的 `finally` 同样用 `pendingTurn === turn` 守卫，不会清掉新轮次的 `sending/localStreaming`；
  新轮次会递增 `reloadGeneration` 并清掉被作废 reload 的 `loading`。
- `stop()` 先 `cancel()` 再置空 `pendingTurn`，被取消 job 的 `finally` 直接跳过。
- `Done(replayed=true)` 会触发一次 `reload()`，因为重放时本地没有增量文本。

### 3.4 SSE

- 用 `Call.chatCall()` + `invokeOnCompletion { call.cancel() }`，协程取消即断 socket（服务端能及时停止生成）。
- 读超时按 **120 秒空闲**（见第 5 节偏差）。
- 收到 `done` / `error` 之后若再抛 IO 异常，仓库层与 VM 层都忽略，不把完整回答改成「失败」；
  `CancellationException` 始终重抛。
- 429 读取 `Retry-After`，`ChatEvent.Failed.retryAfterSeconds` 一路带到 snackbar（「约 N 秒后可重试」）。
- 未知事件类型忽略；未知 `error` 文本映射为固定中文，不透出服务端内部文案。

### 3.5 WebSocket

- 指数退避 1s→2s→…→30s，连接稳定 30s 以上才重置退避（防止 accept 后立即 close 的抖动）。
- 进入重连循环、收到 `Open`、`stop()` 时清空 `turnStates`：断线期间丢掉的 `idle` 帧不会让会话永久「生成中」。
- `unauthorized` 关闭不再重连（交给 401 流程）；Token 变化会重启连接。

### 3.6 语音事件归属

- App 的 `ChatViewModel` 只处理 `conversationId == 当前会话` 的 `voice.event`；无归属的事件直接丢弃，
  避免 A 会话的 `asr_final` 落到 B 会话。
- 服务端原先的 `turn_end` 不带 `conversation_id`，按上述规则会被整类丢弃。本轮改为**服务端补齐字段**：
  `_TurnResources` 快照轮次开始时的 `conversation_id`，`_complete_turn` / `_cancel_cleanup` 都带上；
  从快照取而不是读 `self.conversation_id`，因为取消清理在后台跑时下一轮可能已经开始。
- `protocol_v2.md` 5.3 已同步说明 `turn_end` 带可选 `conversation_id`。

### 3.7 写请求与弹栈

`ViewModelStore.clear()` 会取消 `viewModelScope`，所以在途写请求（保存角色、新建/删除会话、
保存/删除记忆、纠正文本）不能靠「用户按了返回」来中止。处理：

- 各页返回按钮在 `saving/creating/deleting/correcting` 时禁用；按钮回调里同步读
  `viewModel.state.value`（`collectAsState` 的值落后一帧，只靠它做 `enabled` 会留下窗口）；
- `AppNav` 在对应路由上注册 `BackHandler`，回调内同步读 ViewModel 最新状态后决定是否弹栈；
  根页删除角色时也注册，避免系统返回结束 Activity 取消 DELETE；
- 只有角色编辑页保存成功后由事件流触发 `popRoute()`；新建会话是 `pushRoute(Chat)`，
  记忆保存是就地刷新，不弹栈。

### 3.8 记忆与列表语义

- 记忆页区分「全局」与「角色」记忆，保存/删除在途时禁用返回。
- 会话列表 `ConversationsViewModel(autoLoad = false)`，由页面 `LaunchedEffect` 每次进入时刷新：
  从聊天页返回后标题/排序/preview 不会停在旧值。

### 3.9 错误文案

`ErrorMapper` 只直出「已知 code 的固定中文」和「纯中文的旧版 `detail`」；5xx 一律用状态码文案；
混有英文标识的 `detail`（如 `ValueError: 参数非法`）不透出，避免内部标识符进 UI。

## 4. 实验验证

```text
cd companion\app
.\gradlew.bat testDebugUnitTest assembleDebug assembleRelease
...
> Task :testDebugUnitTest
BUILD SUCCESSFUL in 11s
94 actionable tasks: 15 executed, 79 up-to-date
```

测试汇总（读取 `build/test-results/testDebugUnitTest/*.xml`，第三轮修复后复测）：

```text
suites=19 tests=137 failures=0 errors=0 skipped=0
```

```text
cd companion\server
...\python.exe -m pytest -q
425 passed in 13.33s
```

限制：全部为 JVM 单元测试 + 编译；未在真机/模拟器上跑 Compose UI 导航用例，未接入真实云 Key。

## 5. 实施中的偏差（已判定）

1. **SSE 超时**：设计文档写的是较短的空闲超时，实现取 120 秒，因为推理模型首个 token 可能很慢；
   超时失败会以 `stream_timeout` 文案落到气泡 + 重试按钮。
2. **截断的 `onCompletion`**：SSE 在 `done` 之前正常结束流（无异常）也视为失败（「连接提前结束，请重试」），
   避免用户看到半截回答却没有失败标记。
3. **`reload()` 只在本地流/发送中拒绝**：`remoteGenerating` 不拦（否则首次进入页面时若别的设备正在生成，
   历史会永远加载不出来）；UI 上刷新/错误态重试在 `generating` 时都禁用。
4. **语音事件宁可丢弃**：没有 `conversation_id` 的语音事件不做「猜测归属」，改为服务端补齐字段；
   老服务端 + 新 App 的组合下 `turn_end` 只影响「语音中断」提示（当前 UI 未渲染该字段），不影响聊天。
5. **429 文案**：有 `Retry-After` 显示秒数；无头只给通用「稍后再试」，不猜测时间。
6. **广播范围**：服务端是单管理员账号，`AppGateway` 把 `voice.event` 广播给所有 App 连接等价于
   「同一账号的多台设备」，不构成跨账号泄漏；App 侧仍按会话过滤，避免多设备同时打开不同会话时串台。

## 6. 审查记录

### 6.1 第一轮（Task 9 后，独立只读子智能体）

结论「不建议直接进入 4C」，P0×2、P1×7、P2×10。已修 `fe8124c7`：

- P0：聊天页 ViewModel 曾是 Activity 作用域，返回后 SSE 不停；按路由 store 修复。
- P0：重试按钮在离线/生成中仍可点 → UI + VM 双重门控。
- P1：`done` 后 IO 异常覆盖完整回答；旧 job `finally` 清掉新轮次状态；重试打错气泡；
  生成中可刷新导致历史被替换；WS 断线后 `turnStates` 残留；旋转丢失导航栈；无归属语音事件串台。

### 6.2 第二轮（`fe8124c7` 后独立复审）

结论「PASS WITH FIXES」，无 P0，P1×2：

1. **写请求被弹栈取消**（回归）：修复前页面 ViewModel 是 Activity 作用域，写请求总能完成；改成
   按路由后，系统返回可以在保存中弹栈并取消请求。→ `746997ca` 用 BackHandler + 按钮禁用覆盖
   角色编辑/会话列表/记忆/纠错四处。
2. **`turn_end` 被整类丢弃**：App 要求 `conversation_id` 但服务端从不发。→ 服务端补齐字段
   （从轮次资源快照取），协议文档同步；App 侧过滤规则保持不变。

同轮修复的 P2：`markFailed` 身份守卫、加载中禁止发送、429 秒数透出、错误态重试禁用于生成中、
会话列表返回刷新、返回连点节流、流式滚动不抢用户上滑、`ErrorMapper` 混合英文文案、
删除死代码 `emptyBackStack()`。

### 6.3 第三轮（`746997ca` + `e2c55cda` 后独立复审）

结论「PASS WITH FIXES」，无 P0/P1；P2×8（含日志事实错误）。已修 `92eca45e`：

1. 列表自动刷新无代次守卫 → `refreshGeneration` 作废旧请求，删除成功再递增一次，旧快照不会
   把已删会话「复活」。
2. 返回节流是全局且不随导航重置 → `pushRoute` 重置；弹回根页后 400ms 内继续吞返回。
3. `BackHandler(enabled = 组合状态)` 落后一帧 → 改为始终注册、回调内同步读 `state.value`；
   四处返回按钮也改为回调内同步判断。
4. 根页删除角色时系统返回会结束 Activity → 根页补 `BackHandler`。
5. 贴底判定读不到布局且把「最后一条只露出顶部」当底部 → 改用 `canScrollForward` 的
   `derivedStateOf`，只在用户本就在底部时跟随。
6. 列表长度变化无条件跳底 → 合并为一个「仅贴底才跟随」的 effect。
7. 旧轮 `turn_end` 可能晚于新轮 `turn_start` → 服务端改为先发 `turn_end` 再清理 ASR/TTS；
   App 记录 `turn_start.turn_id` 并丢弃不匹配的 `turn_end`。
8. 测试有效性 → 新增「旧列表不复活已删除会话」「旧语音轮 `turn_end` 不覆盖新轮」两条。

日志事实订正（本文件）：删除「去掉废弃 `rememberSaveable` key」的表述（该 key 在 `fe8124c7`
已不存在）；「连点两次返回不会一次弹两级」限定为深度 ≥ 3，深度 2 的问题已由本轮修复；
「保存成功后事件流触发弹栈」只适用于角色编辑页。`protocol_v2.md` 5.3 补 `asr_final` 也带
`conversation_id`。

### 6.4 已知未修（记录在案）

- 没有 Compose/Robolectric 级「导航后旧 ViewModel 被清」的端到端用例；现有测试只覆盖
  `NavigationViewModel` 本身与页面 ViewModel 逻辑（复审明确指出这点）。
- `BackHandler`、返回节流、根页返回窗口都只有代码层保证，没有 `OnBackPressedDispatcher` 级用例。
- UI 门控（`enabled = online && …`）没有 UI 测试，只有 VM 守卫测试。
- `AssistantEdit` 保存成功事件与用户点返回同时发生时依赖 `backStack.lastOrNull() == route` 守卫，
  没有并发用例。
- `markFailed` 的身份守卫、以及「新一轮开始后旧轮取消清理」的服务端快照行为没有专门用例；
  第二轮补的测试里有 3 条（`ChatRepositoryTest` 的 `done` 后 IO、`AppWsManagerTest` 的断线清态、
  `ChatViewModelTest` 的无 `conversation_id` 过滤）在修复前就能通过，属于回归防护而非失败测试。
- 进程被回收后恢复到「聊天页」时，若会话已在别处删除，会显示加载失败 + 返回，符合预期但无测试。
- 服务端 `voice.event` 的广播范围依赖「单管理员」假设，多账号化时需要按 `token_id` 定向。

## 7. 未完成 / 风险

- **真机未验**：旋转、弱网切换、后台 5 分钟、长回答滚动、输入法遮挡都只在代码层保证。
- **真实流式未验**：Mock SSE 覆盖了分帧/终态/取消，但真实供应商的首 token 延迟与 120s 超时的匹配需实测。
- 从聊天页返回再进入会重新拉详情（设计如此），长会话在弱网下会有一次明显 loading。

## 8. 下一步

进入 4C（设备列表 + 遥控）：

1. 服务端 `GET /api/devices` 增加 `online`；`device.report` 视为在线，`device.status(online=false)` 权威离线；
   离线时不发 `remote.get_status`。
2. App 遥控：200ms 节流、`remote.ack` 只表示「已受理」、动作结果以 `action.result` 为准、校准页、
   离线禁用、动作目录来自 `/api/actions`。
3. 先写 4C 计划（设备在线态、ack/result 区分、节流与急停优先级、catalog 渲染），再实施。
