# 30 · Phase 4-4C（App）：设备列表 / 遥控

- 状态：**已完成**（App 两轮只读审查待回填；真机与真实舵机未验证）
- 日期：2026-11-27
- 分支：`feature/companion-robot-v2`
- 设计依据：`docs/superpowers/specs/2026-11-27-companion-app-phase4-design.md`
- 实施计划：`docs/superpowers/plans/2026-11-27-companion-app-phase4c.md`
- 前置：日志 29（4B App：会话列表 / 流式聊天 / 记忆 / 导航作用域）、日志 27（4A 登录与加密会话）

## 1. 目标与范围

4C 的目标是让 App 能管理机器人并真正遥控：

- 设备列表：配对码（带倒计时）、改名、绑定角色/会话、撤销、在线状态实时合并；
- 遥控：音量/静音、急停、动作目录（强度/速度/重复）、云台角度、待机小动作、
  云台校准（预览/保存）、结果条；
- 语义：`remote.ack` 只表示「已受理」，成功/失败/超时一律以 `action.result` 为准；
- 门控：手机离线、WS 未连接、设备离线时所有控件置灰，ViewModel 二次校验；
- 计划外但必须做：设备恢复在线后补拉状态、急停作废在途动作的迟到回执。

不在本轮：真机遥控、真实固件校准写入、多设备同时遥控（规格未要求）、素材/备份/设置（4D）。

## 2. 提交与改动文件

| 提交 | 说明 |
| :--- | :--- |
| `9c7464ba` | 4C 实施计划（TDD 任务拆分） |
| `008f4ea9` | Task 1：`GET /api/devices` 每项新增 `online` |
| `2d5e7f6c` | Task 2：`WsSink`/`WsFrame.Writable`、`AppWsManager.send` |
| `9eac1a9a` | Task 3：设备/动作目录 DTO、`CompanionApi` 6 个端点、`HttpDeviceRepository` |
| `d51a86b0` | Task 4：`DevicesViewModel`/`DevicesScreen`、路由与导航接线、角色页入口 |
| `c7298643` | Task 5：`RemoteCommands` + `RemoteViewModel` 遥控状态机、WS 报告解析兼容 |
| `55766307` | Task 6：`RemoteScreen`、门控纯函数、`AppNav` 接真遥控页 |
| `b737fa9e` | 自查修复：急停后的 ack 超时、失败状态查询恢复、结果超时放行返回键 |
| `79c0aa4e` | 自查修复：App WS 重连清空设备缓存、遥控页重连后重拉快照 |
| `018ba20c` | 两轮审查修复：设备列表代次刷新 + 撤销过滤、配对码在途去重、遥控双级结果超时 + 防抖取消、WS generation 防旧循环清状态 |
| `75b31f60` | 第三轮回归复核修复：设备在线态回退 + 撤销墓碑 + 重连刷新、配对码失败保留旧 ticker、遥控结果先于 ack 补显 / 动作序号裁决 / 防抖取消复位脏标记、目录与返回门控 |
| `a2a36fc1` | 复核后再修复：终态副作用复用、校准草稿全终态解冻、旧动作解锁、ack 失败/超时 seq 守卫、重连强制刷新、`earlyResults` 清理 |

主要文件：

- 服务端：`companion/server/app/routers/devices.py`、`companion/server/tests/test_devices.py`
- WS：`companion/app/src/main/java/me/rerere/companion/data/ws/{WsTransport,OkHttpWsConnector,AppWsManager,WsEvents}.kt`
- 数据：`.../data/api/CompanionApi.kt`、`.../data/api/dto/{DeviceDtos,CatalogDtos}.kt`、
  `.../data/repo/DeviceRepository.kt`、`.../core/AppContainer.kt`
- 设备页：`.../feature/devices/{DevicesViewModel,DevicesScreen}.kt`、`.../feature/assistants/AssistantsScreen.kt`
- 遥控：`.../feature/remote/{RemoteCommands,RemoteViewModel,RemoteScreen}.kt`
- 导航：`.../ui/navigation/{AppRoute,AppRouteSaver,NavigationViewModel,AppNav}.kt`
- 测试：`.../test/.../data/ws/AppWsManagerTest.kt`、`.../data/repo/DeviceRepositoryTest.kt`、
  `.../feature/devices/DevicesViewModelTest.kt`、`.../feature/remote/{RemoteCommandsTest,RemoteViewModelTest,RemoteScreenGateTest}.kt`、
  `.../testutil/{FakeDeviceRepository,WsTestHarness,MainDispatcherRule}.kt`

## 3. 关键实现

### 3.1 服务端 `online`（Task 1）

`_serialize_device()` 在读取时计算 `online = is_active && device_link_is_online(link)`：
`device_link_is_online` 复用设备 WS 的 `ready && !closing/closed` 判定，不从数据库列推断。
已撤销设备即使链路对象残留（关闭失败/竞态）也恒为 `false`，避免 App 显示「已撤销但在线」
导致所有命令必然失败。测试覆盖无链路、ready、closing、未 ready、已撤销五种状态。

`GET /api/devices` 的旧字段全部保留（v1 网页端不受影响），只追加 `online`。

### 3.2 WS 可写句柄与发送（Task 2）

- `WsFrame.Writable(sink)`：传输层在 `onOpen` 后下发当前连接的写句柄；
  `OkHttpWsConnector` 只在 socket 打开时发一次，断线/失败后管理器把 `sink` 置空。
  顺序固定为「先 `Writable` 再 `Open`」：管理器一进入 `Connected` 就能 `send()`，
  否则紧跟 `Connected` 的命令会因句柄未发布而报「未送达」；缓冲已关闭时不下发 `Open` 并 `cancel()`。
- `AppWsManager.send(text): Boolean`：`sink?.send(text) ?: false`；调用方必须把 `false`
  当作「未送达」处理，不乐观入队。
- 旧句柄在 `WsFrame.Closed/Failed`、`runLoop` 退出、`stop()` 立刻失效，重连后命令
  不会写进已关闭的 socket。
- 每次 `Open` 都清空 `devices` 缓存（与 `turnStates` 同理）：上一连接看到的在线态可能
  已过期，等新连接的 `device.status`/`device.report` 重新填充。

### 3.3 设备与动作目录数据层（Task 3）

`DeviceDto` 覆盖 `GET /api/devices` 的字段（含 `online`、`volume`、`idle_enabled`、
`pan_offset`/`tilt_offset`、`state`、`last_seen_at`）；`ActionCatalogDto` 只声明渲染面板
需要的字段（`categories`、`limits`、`actions[].supports/max_duration_ms`），未知字段忽略。

`CompanionApi` 追加：`listDevices`、`createPairCode`、`bindDevice`、`renameDevice`、
`revokeDevice`、`actionCatalog`。`HttpDeviceRepository` 通过 `SessionApi.call {}` 统一
把 HTTP/网络错误映射成 `AppError`，UI 不解析响应体；`AppContainer` 新增 `deviceRepository`。

### 3.4 设备列表页（Task 4）

- `DevicesViewModel`：`GET /api/devices` 快照 + `/app/ws` 的 `device.status`/
  `device.report` 实时合并，`DeviceRow.online` 是合并后的权威展示值（不改嵌套 `DeviceDto`）。
- 列表刷新用代次（`refreshGeneration`）：写操作成功后递增，晚到的旧快照直接丢弃；
  列表只展示 `isActive` 设备（服务端保留已撤销行用于审计）。撤销成功会作废在途请求，
  由撤销分支自己清 `loading`，避免刷新按钮永久置灰。
- 角色名单独并行加载：失败时保留上一次已知名字，绑定行显示「已绑定角色（名称未知）」，
  不再把已绑定设备显示成「未绑定」。
- 配对码：`PairCodeUi` 由页内 ticker 驱动，按毫秒递减、向上取整显示；过期后保留弹窗并置
  `expired`，可重新生成；旧 ticker 通过 `code` 二次守卫，不覆盖新码；`expiresIn <= 0` 视为一出现即过期。
  请求在途时 `pairCreating` 双保险阻止重复 POST（服务端配对码有 5 次/60 秒限流）。
- 写操作（配对码/改名/绑定/撤销）：先过 `isOnline()` 门控；`busyId` 串行化，第二笔写操作
  明确提示「上一个操作还在进行中」（不再静默丢弃），成功后刷新列表。
- `openRemote` 离线也允许进入：遥控页会整页置灰并说明原因，比「点了没反应」清楚。
- 顶栏入口：`AssistantsScreen` 增加「设备」按钮 → `AppRoute.Devices`。

### 3.5 遥控命令构造（Task 5）

`RemoteCommands.kt` 用 `buildJsonObject` 生成协议 §8.1 的命令，信封固定
`v=2 / type / request_id / device_id / ts`：

- `remote.action.play`：`name`、`intensity 1..3`、`speed 1..3`、`repeat 1..5`；
- `remote.servo.move`：`pan [-90,90]`、`tilt [-20,45]`、`speed [0,1000]`（默认 500）；
- `remote.volume.set`：`volume [0,100]`；`remote.idle.set`：`enabled`；
- `remote.action.stop`：`reason`（急停用 `user_stop`），自身不带 `action_id`；
- `remote.calibrate.preview/save`：偏移夹在 `[-30,30]`（`REMOTE_OFFSET_LIMIT` 与 UI 共用）；
- `remote.get_status`：只带信封。

`remoteAckError`/`deviceOfflineReason` 把 ack 错误码与离线原因映射成中文，未知码回落通用文案，
不把英文码直接展示给用户。

### 3.6 遥控状态机（Task 5）

`RemoteViewModel`（每设备一个 VM，按路由作用域）：

- **ack ≠ 成功**：`remote.ack(ok=true, action_id=...)` 只显示「已受理：<动作>」并继续
  `sendingAction`；只有 `action.result` 才结算结果条。
- **按 `action_id` 对回请求**：`awaitingResult[action_id]`；同一 `action_id` 的迟到/重复结果
  因查不到 pending 被丢弃；`settled`（上限 128）兜住急停/断线后晚到的结果。
- **终态映射**：`done` → DONE「<动作> 完成」；`timeout` → TIMEOUT；`failed` 的
  `superseded`/`stopped` 视为预期行为（INFO「已被新的指令覆盖」/「已停止」）；
  `device_offline` 同时把面板翻成离线。
- **滑条**：`volume`/`pan-tilt`/校准预览 200ms 防抖，松手 `commit*` 立即下发并取消防抖；
  静音是显式一次性点击，立即下发。非用户可见命令（音量/云台/待机/预览/取状态）的受理与
  正常结果不弹条，只有失败/超时才占用结果条，避免持续拖动刷屏。
- **草稿标记**：`volumeDirty`/`panTiltDirty`/`idleDirty`/`calibrationDirty` 在命令交给 socket
  后清除；`device.report` 只在没有草稿时回填滑条。校准草稿保留到 `calibrate.save` 的
  `action.result(done)`，防止「预览 → 旧上报」把滑条拽回去；`savedOffset*` 记录已持久化值，
  取消校准会恢复并再预览一次持久化偏移。
- **急停**：本地先取消防抖、把在途结果记入 `settled` 并清空，再发 `remote.action.stop`；还没收到
  ack 的动作连超时一起取消并从 `awaitingAck` 移除，因此 5 秒后不会用「未确认」覆盖「急停已下发」
  （急停本身只表示「已下发」——帧写出不等于设备已归位，ack 被限速拒绝时也不谎报成功）。
- **超时（两级）**：ack 5s 未到 → FAILED「<动作>：未确认」并清 `sendingAction`；收到 ack 后
  15s 仍无 `action.result` → 只释放 UI 锁（返回键）并提示「结果未确认」，**不记入 `settled`**，
  真实结果晚到仍会把结果条更新成终态；45s（服务端排队上限 30s + 动作上限 5s，留余量）才按丢失
  结算并丢弃迟到结果，避免 pending 永久占内存、结果条反复跳。
- **断线/离线**：WS 非 Connected 或 `device.status(online=false)` 清空在途命令并提示
  「命令未确认」，同时取消未发出的防抖（否则 200ms 后会用「当前离线」覆盖断线提示，甚至把旧云台
  命令补发出去）；`device_offline` ack 直接翻离线。设备离线→在线时补发一次 `get_status`；
  App WS 重连时重新拉 `GET /api/devices` 快照再校准运行态——只看内存里的旧在线标记会漏掉
  「断线期间才上线」的设备，导致遥控页一直置灰。
- **取状态**：`get_status` 的 ack 无 `action_id`，ack 即终态；成功或失败都会清除
  `statusRequestPending`，否则一次失败会让本页在重连前不再刷新。服务端先发扁平
  `device.report`（`source="server"`）再回 ack；该快照可能晚于 offline 事件到达，
  所以它只刷新运行指标，**不把离线设备翻回在线**（在线态只认 `device.status` 与 REST）。
- **LLM 动作**：不是本页发起的 `action.result` 中，`source="llm"` 显示「机器人动作：…」，
  设备自身动作（如待机动画，`source=device`）静默。

### 3.7 遥控页 UI 与门控（Task 6）

- `canUseRemote(connection, deviceOnline, networkOnline) = Connected && deviceOnline && networkOnline`；
  `canStop` 与它同条件（不依赖目录/动作是否加载成功）。动作/滑条按钮只看 `canUseRemote`：
  目录加载失败也允许急停与滑条，只有「重试/重新加载」按钮额外受门控，离线时不可点。
- 页面自上而下：顶栏（设备名 + 设备状态 + 在线徽标 + 返回）与离线 banner；状态卡；
  音量滑条 + 静音；全宽红色「急停归位」；动作区（强度/速度/重复 + 按
  `basic/social/emotion/mimic` 排序的分组 `FlowRow`，未知类别追加在后，空态/失败态给重试）；
  云台 pan/tilt 滑条；待机小动作 `Switch`；校准入口。
- 校准 `AlertDialog`：两个 ±30° 滑条拖动即预览（VM 内 200ms 防抖），保存走二次确认；
  保存后只关对话框、不取消（旧实现「保存 + 取消」会在写入后立刻预览旧偏移）；
  「取消/点外部」恢复持久化偏移并再预览。
- 结果条：`INFO/DONE/FAILED/TIMEOUT` 四种配色，6 秒自动消失或点「知道了」。
- `AppNav`：`ownerFor(route)` + `viewModel(key = "remote-${deviceId}")`；`LaunchedEffect` 调
  `start()`；`BackHandler` 始终注册，回调里同步读 `sendingAction`，有可见动作在途时吞掉返回，
  其余情况弹栈；返回按钮同一守卫。

## 4. 验证（真实输出）

```text
cd companion\app
.\gradlew.bat testDebugUnitTest assembleDebug assembleRelease
→ BUILD SUCCESSFUL（debug 与 release 均成功）
→ suites=24 tests=223 failures=0 errors=0
```

各任务节点：

| 节点 | 结果 |
| :--- | :--- |
| Task 1 | `test_devices.py` 11 passed；服务端全量 427 passed |
| Task 2 | `AppWsManagerTest` 12 passed；全量 19 suites / 139 tests |
| Task 3 | `DeviceRepositoryTest` 8 passed；全量 20 suites / 147 tests |
| Task 4 | 全量 21 suites / 159 tests；`testDebugUnitTest assembleDebug` 成功 |
| Task 5 | 全量 23 suites / 186 tests（`RemoteCommandsTest` 8、`RemoteViewModelTest` 19） |
| Task 6 | 全量 24 suites / 190 tests（`RemoteScreenGateTest` 2、VM 21） |
| 自查修复后 | 全量 24 suites / 195 tests（`AppWsManagerTest` 13、`RemoteViewModelTest` 25） |
| 审查修复后 | 全量 **24 suites / 210 tests / 0 failures**（`DevicesViewModelTest` 17、`AppWsManagerTest` 16、`RemoteViewModelTest` 30、`NavigationViewModelTest` 6）；`testDebugUnitTest assembleDebug assembleRelease` 全部 BUILD SUCCESSFUL |
| 回归复核修复后 | 全量 **24 suites / 223 tests / 0 failures**（`DevicesViewModelTest` 23、`AppWsManagerTest` 18、`RemoteViewModelTest` 35、`RemoteScreenGateTest` 2）；`testDebugUnitTest assembleDebug assembleRelease` 全部 BUILD SUCCESSFUL |

```text
cd companion\server
python -m pytest -q
→ 427 passed in 13.45s
```

## 5. 偏差与取舍

1. **TDD 顺序**：Task 5 先写了 `RemoteCommands.kt` 再补测试，`RemoteViewModel` 则先写测试；
   命令构造器的测试随后补齐并通过，但这一处不符合「先失败测试」的要求，记录在此。
2. **`RemoteScreen` 接口扩展**：计划只列了基础回调；实际增加
   `onSetIntensity/onSetSpeed/onSetRepeat/onCancelCalibration`。参数选择是纯本地状态，
   取消校准需要单独回调（不能复用 `onDismiss` 语义），故扩展。
3. **门控按钮**：计划的静音是 `IconButton`，实现用带文字的 `TextButton`（「静音/取消静音」），
   与页面其它文字按钮一致；离线 banner 放在顶栏下方而非整页 overlay，控件本身全部禁用。
4. **返回拦截**：计划写 `BackHandler(enabled = sendingAction != null) { }`；实现改为始终注册 +
   回调内同步读状态，避免 4B 审查发现的「`enabled` 组合快照落后一帧」窗口；并补了结果超时，
   防止结果丢失导致永久无法返回。
5. **`device.report` 解析**：服务端是扁平字段；管理器接受 `payload["report"]` 嵌套（旧夹具）
   与顶层两种，嵌套优先，顶层多余的信封字段被读取方忽略。
6. **`sendingAction` 只表示可见动作**：音量/云台/待机/预览不占用它，否则滑条每动一次都会
   拦截返回键。
7. **`online` 展示**：`DeviceRow.online` 为 REST+WS 合并值，嵌套 `DeviceDto.online` 保持
   REST 快照语义（`DevicesScreen` 只读 `DeviceRow.online`）。
8. **重连后的设备缓存**：`AppWsManager` 在 `Open` 时清空 `devices` 缓存，遥控页重连后
   重拉 `GET /api/devices` 快照。计划只要求「补一次 `get_status`」，但旧在线标记会让
   「断线期间上线」的设备一直显示离线，故扩大为「快照 + 运行态」两次刷新。
9. **撤销后的列表权威性**：列表以服务端 `is_active` 为准过滤，并在撤销成功后作废在途列表
   请求；被作废的请求不再负责清 `loading`，由撤销分支收尾——否则刷新按钮会永久置灰。
10. **结果超时拆两级**：审查指出 12s 短于服务端排队上限 30s。改为「15s 只解锁 UI（不结算，
    真实结果仍可更新）+ 45s 硬上限才丢弃」，既不误判真实终态，也不让 pending 永久占内存。
11. **删除 `canStop`**：与 `canUseRemote` 恒等，保留只会产生「自我比较」的假测试；急停直接用
    同一门控，测试只断言门控本身。
12. **下发顺序**：`Writable` 必须先于 `Open`，否则管理器进入 `Connected` 后紧接着的 `send`
    会因句柄未发布报「未送达」；这一条在测试里用真实的 `send` 成功/失败覆盖。
13. **撤销竞态用墓碑而非只靠代次**：撤销开始后才发出的 GET 代次更新，靠代次丢弃会误杀
    正常刷新；因此撤销成功后记 `revokedIds`，所有列表结果统一按墓碑过滤。
14. **结果条按动作序号裁决**：给可见动作加单调 `seq`，`lastVisibleResultSeq` 记录结果条
    当前描述的动作；软/硬超时只有序号不旧于它时才能改文案，避免旧动作覆盖新动作终态。
15. **结果先于 ack 的兜底**：`earlyResults` 暂存 `action_id → (status, detail)`，ack 迟到时补显；
    上限 16 条，正常同 socket 不会发生，只防断线重排。
16. **草稿取消要复位脏标记**：急停/断线会取消未发出的防抖；若不清 `volumeDirty/panTiltDirty`，
    后续上报会被永久忽略，滑条停在从未下发的值上。
17. **目录重试只按连接 + 网络门控**：动作目录来自服务端 REST，与设备在线无关；用
    `deviceOnline` 门控会让「设备离线 → 顺手重拉目录」变成不可能操作。

## 6. 审查结论

前三轮均为**只读子智能体审查**（不改文件），结论都是 **PASS WITH FIXES**、无 P0。
前两轮按「修全部 P1 + 低成本 P2」处理，修复提交 `018ba20c`；第三轮是对 `018ba20c` 的回归复核，
修复随本节记录的后续提交一起提交。

### 6.1 第一轮：Task 1–4（设备列表 / WS 发送）

结论 PASS WITH FIXES：P1×4、P2×9、P0×0。

| 编号 | 问题 | 处理 |
| :--- | :--- | :--- |
| P1-1 | 已撤销设备刷新后重现（`is_active` 未参与过滤） | `refresh()` 只保留 `isActive`，补「撤销设备被过滤」用例 |
| P1-2 | 无刷新代次：写后刷新被 `loading` 早退丢弃、在途旧快照会复活已撤销行 | `refreshGeneration` + 被作废请求不再负责清 `loading`，补 2 条竞态用例 |
| P1-3 | 第二笔写操作因 `busyId` 静默返回 | `rejectWhileBusy()` 明确提示「上一个操作还在进行中」，补用例 |
| P1-4 | 空态文案与按钮/服务端流程相反 | 改为「先生成配对码，再在机器人配网页面输入它。」 |
| P2-1 | 重连不清 `_devices`、服务端不重放快照，实时在线值会长期压过 REST | `79c0aa4e` 起 Open 时清空缓存；遥控页重连重拉 REST + `get_status`；列表页进入/手动刷新（残留见 §7） |
| P2-2 | 角色列表失败返回空 map，已绑定设备显示「未绑定」且拖慢列表 | 独立并行加载；失败返回 `null` 保留旧名，行内显示「已绑定角色（名称未知）」 |
| P2-3 | 配对码无在途保护、弹窗 `busy=false` 硬编码，重复点击打满限流 | `pairCreating` 双保险 + 弹窗绑定该状态 |
| P2-4 | `Connected` 先于 sink 发布，紧跟的命令报「未送达」；`trySend` 结果被忽略 | `OkHttpWsConnector` 固定先发 `Writable` 再发 `Open`，下发失败则 `cancel()` |
| P2-5 | `stop()` 未 join，旧协程 `finally` 可清掉新 sink / 把状态改成 `Stopped` | `start()/stop()` 递增 generation，旧 `runLoop` 收尾与帧处理都校验 generation（无独立用例，靠推演 + 现有 start/stop 用例） |
| P2-6 | 测试缺口：未断言路径/方法、无 `send=false`、无 stale/busy/revoked、`viewModelKey` 用例缺失 | 全部补齐（`DeviceRepositoryTest`、`AppWsManagerTest`、`DevicesViewModelTest`、`NavigationViewModelTest`） |
| P2-7 | `expiresIn == 0` 时弹窗永远「剩余 0:00」 | 进入即置 `expired`，补用例 |
| P2-8 | 撤销对话框只有一句「需恢复出厂」，没有可见指引 | **未修**，记入 §7（固件流程本身未验证，写死文案风险更大） |
| P2-9 | 服务端 `online` 依赖进程内注册表，多 worker 不可信 | **未修代码**，约束写进 §7 |

### 6.2 第二轮：Task 5–6（遥控状态机 / 遥控 UI）

结论 PASS WITH FIXES：P1×3（P1-1、P1-2 已由 `b737fa9e` 覆盖，P1-3 未修）、P2×12、P0×0；
并指出 `b737fa9e` 的 12s 结果超时**短于**服务端排队上限 30s，会误判真实终态。

| 编号 | 问题 | 处理 |
| :--- | :--- | :--- |
| P1-1 | 急停作废动作时未取消其 5s ack 超时，5 秒后「未确认」覆盖「已急停」 | `b737fa9e` 已修；本轮保留并补用例 |
| P1-2 | 结果丢失时 `sendingAction` 永不清空 → 返回键永久被吞；结果先于 ack 会挂死 pending | `b737fa9e` 加软超时；本轮改为 15s 只解锁 UI / 45s 才结算 + ack 查 `settled`，补 2 条用例 |
| P1-3 | `stopAll`/`clearPending`/离线不取消 `volume/panTilt/calibration` 防抖，急停后 200ms 仍补发云台/预览 | `cancelPendingDebounce()` 三处接入 + 用例 |
| P2-A | `get_status` ack 失败在 early return 前不重置 `statusRequestPending` | `b737fa9e` 已修（移到失败分支之前） |
| P2-B | `device.report` 无条件把设备标在线，`source=server` 快照可能复活离线设备 | 上报只刷新指标；App WS 缓存与遥控页两层都只认 `source != "server"`，补 2 条用例 |
| P2-C | 草稿标记在写 socket 时清除，旧上报可能把滑条拽回 | **未修**（下一次上报通常已带新值，改动面大），记待办 |
| P2-D | `catalogError` 会禁用已加载的动作，但原因/重试只在空态出现 | `actionsEnabled = usable`，重试按钮单独受门控 |
| P2-E | 校准对话框 `remember` 开关，旋转后对话框消失但 `calibrationDirty` 与预览偏移留在 VM | **未修**，记待办 |
| P2-F | 防抖已发过后松手会重复下发同值 | **未修**，记待办 |
| P2-G | `idle` 开关只在写 socket 失败时回滚，ack/result 失败后状态不一致 | **未修**，记待办 |
| P2-H | 缺 `timeout` 文案；LLM 的 `failed+timeout` 被标成 INFO | `remoteAckError("timeout") -> 超时`；`status=timeout -> TIMEOUT`，补用例 |
| P2-I | 急停帧一写出就宣称「已急停」 | 文案改「急停已下发，等待设备确认」 |
| P2-J | `device.report` 注释写「顶层优先」但代码先取嵌套；测试只有旧夹具 | 注释改为「有嵌套先取嵌套」，补扁平报文用例 |
| P2-K | ack 安装 pending 前不查 `settled`，结果先到会留下永不结算的 pending | 加 `actionId in settled` 守卫 + 用例 |
| P2-L | `canStop` 与 `canUseRemote` 恒等（测试是自我比较）；重试按钮离线仍可点 | 删除 `canStop`，急停直接用 `canUseRemote`；删除恒真测试；重试按钮加门控；`superseded` 死分支保留（对可见命令确实不可达，留着无害） |
| 测试 | 没有任何 `RemoteScreen` 的 Compose 测试，离线用例只覆盖 `playAction` | **未修**，记待办（Compose 测试需 Robolectric，成本高） |
| 超时 | 12s < 服务端 `QUEUE_MAX_WAIT_MS = 30_000` + 动作 5s | 改为 15s 解锁 UI + 45s 硬结算，见 §3.6 |

### 6.3 第三轮：回归复核（对 `018ba20c`）

两个只读子智能体分别复核设备侧与遥控侧，结论都是 PASS WITH FIXES、无 P0：
设备侧 P1×2、P2×11；遥控侧 P1×3、P2×7。本轮修完全部 P1 与低成本 P2。

| 编号 | 问题 | 处理 |
| :--- | :--- | :--- |
| 设备 P1-1 | 重连清空实时缓存后，`applyStatuses` 对缺失 id 直接保留旧值，断线期间掉线的设备永久显示「在线」 | 无 live 值时回落到 `row.device.online`；VM 订阅 `connection`，切到 `Connected` 时 `refresh()`；补用例 |
| 设备 P1-2 | 撤销在途时发出的 GET 代次更新、不会被丢弃，会把已撤销设备加回列表 | 撤销成功后记 `revokedIds` 墓碑，所有列表结果按墓碑过滤；补「撤销在途刷新」用例 |
| 设备 P2-1 | 请求发出前就取消旧 ticker，失败后旧码冻结在「剩余 0:xx」 | 创建前不再取消旧 ticker，成功时由 `startPairCountdown` 取消并替换 |
| 设备 P2-2 | 请求在途时关闭弹窗，成功后新码又被顶回来 | `pairDismissed` 标记，成功回调据此不弹窗；补用例 |
| 设备 P2-3 | VM init 与 `AppNav` 各调一次 `refresh()`，首屏固定打两次列表 + 两次角色 | 空列表 + `loading` 时复用在途请求 |
| 设备 P2-4 | `source` 只从顶层信封读，放进嵌套 `report` 时守卫失效 | 顶层优先、嵌套兜底；补嵌套 + `source=server` 用例 |
| 设备 P2-5 | `start/stop` 的 generation 守卫零覆盖 | 补「旧 runLoop 晚收尾不得清掉新 sink/连接」用例 |
| 设备 P2-6 | 角色名加载无代次守卫，旧响应可覆盖新名单 | **未修**（失败保留旧值已覆盖主要风险），记 §6.4 |
| 设备 P2-7..9 | 角色名失败保留、撤销竞态、TTL=0 时间推进缺用例 | 全部补齐 |
| 设备 P2-10 | `Open` 帧发送失败（缓冲满）仍停在半死连接 | `trySend(Open)` 失败同样 `cancel()` |
| 设备 P2-11 | 设备行已不在列表时 `startBind` 静默返回 | 补提示「设备已不在列表中，请刷新」 |
| 遥控 P1-1 | 取消防抖只终止 Job，不清 `volumeDirty/panTiltDirty`，之后的上报被永久忽略 | 取消时复位两个脏标记；补「取消后上报能回填」用例 |
| 遥控 P1-2 | 结果先于 ack 时终态被静默丢弃，动作完成/失败永远不展示 | `earlyResults` 暂存终态（上限 16 条），迟到 ack 时补显；补断言结果条文案 |
| 遥控 P1-3 | 15s 释放 UI 锁后旧动作的 45s 硬超时仍会覆盖新动作的终态 | 给可见动作加单调 `seq`，旧软/硬超时只结算不改文案；补用例 |
| 遥控 P2-1 | 有旧目录时 `catalogError` 完全不可见；设备离线时无法重试拉目录 | 错误常驻显示；重试只按「连接 + 网络」门控（`canRefreshCatalog`）；补纯函数用例 |
| 遥控 P2-2 | 返回被拦截时顶栏「返回」仍可点、点了无反应 | `sendingAction != null` 时置灰 |
| 遥控 P2-5 | 校准草稿期间 `savedOffset*` 仍被上报覆盖，「取消校准」会恢复成未保存值 | 草稿期间冻结 `savedOffset*`；补用例 |
| 遥控 P2-6 | 超时用例一次性推 45s，掩盖 15s 软超时；结果先于 ack 只断言 `sendingAction` | 拆 15s/45s 分段断言；`result before ack` 断言终态文案 |
| 遥控 P2-3 | `stopAll` 只作废 ACTION/CALIBRATE_SAVE 的未 ack 请求 | **未修**，记 §6.4（SERVO/PREVIEW 已发出无法撤回，仅影响内部 pending） |
| 遥控 P2-4 | `settled` 是无时间维度的 128 条 FIFO | **未修**，记 §6.4（受 5s ack 超时约束，实际触发不了） |

### 6.3.1 复核后的再修复（`75b31f60` → `a2a36fc1`）

复核指出上面这轮仍有 1 个 P1 和几处新引入的边角问题，已全部处理：

| 编号 | 问题 | 处理 |
| :--- | :--- | :--- |
| P1 | `onResultSlow`/`onResultLost` 的 seq 守卫写在清理 `visibleInFlight` 之前：被抑制的旧动作永远留在“执行中”，`sendingAction` 永久非空 → 返回键/动作按钮被锁死 | 清理与 `refreshSending()` 移到守卫之前；补「被抑制的旧动作仍解除返回锁」用例 |
| P2-1 | 结果先于 ack 的 replay 分支漏掉终态副作用（校准解冻、设备离线翻面板） | 抽 `applyTerminalSideEffects()`，正常路径与 replay 共用；补「早到结果仍解冻草稿」用例 |
| P2-2 | `calibrate.save` 结果丢失/ack 超时/发送失败时 `calibrationDirty` 永久冻结 | 所有终态（成功/失败/超时/未发出）都解冻；`clearPending` 在保存未知时也解冻 |
| P2-3 | ack 失败与 ack 超时无条件写结果条，会覆盖更新动作的终态 | 可见动作加 `seq` 守卫；滑条类失败仍保持可见 |
| P2-4 | `applyStatuses` 在实时缓存清空时回退到**更旧的** REST 快照，可能把刚离线的设备翻回在线 | 保留最后已知值；重连 `refresh(force = true)` 取消在途请求并重拉快照；补两条重连用例 |
| P2-5 | `refresh()` 的早退会吞掉重连刷新（断网时的请求挂起时） | 新增 `refreshJob`，强制刷新先取消旧请求 |
| P2-6 | `earlyResults` 在断线清空 pending 时滞留 | `clearPending` 一并清空（仍有 16 条上限） |
| P2-7 | `OkHttpWsConnector` 的 `Open` 投递失败分支无测试 | **未修**，记 §6.4（需注入假 WebSocket，成本高） |
| P2-8 | 真实 `action.result` 仍可覆盖更新动作的结果条，与超时路径的 seq 策略不完全一致 | **保留**：真实终态是新信息，按“终态优先”处理；在 §5 记录语义 |

### 6.4 待办（已记录、未修 P2）

1. 校准对话框开关与 `calibrationDirty` 的进程/旋转恢复（P2-E）。
2. `idle` 开关在 ack/`action.result` 失败时回滚（P2-G）。
3. 防抖后 commit 重复下发同值命令（P2-F，浪费 10 次/秒预算）。
4. 草稿标记改在结果/匹配上报后清除（P2-C）。
5. `RemoteScreen`/`AppNav` 的 Compose 级测试（门控、返回守卫、结果条）。
6. 撤销后的「恢复出厂」可见指引（P2-8，等固件流程定稿）。
7. `loadAssistantNames` 的请求代次守卫（设备 P2-6）。
8. `stopAll` 对 SERVO/PREVIEW 未 ack 请求的内部清理（遥控 P2-3）。
9. `settled` 改为带时间戳过期（遥控 P2-4，当前实现无害）。
10. `OkHttpWsConnector` 的 `Open` 投递失败分支测试（需注入假 WebSocket）。
11. 「真实终态覆盖更新动作结果条」与超时 seq 策略的语义统一。

## 7. 未完成 / 风险

- **真机未验**：没有真实舵机/固件，`pan/tilt` 实际角度、校准写入 NVS、急停归位手感、
  待机小动作开关都只在协议层验证。
- **10 次/秒限速下的手感未验**：连续拖动靠 200ms 防抖与「后写覆盖」，真实网络抖动下
  `superseded` 的频率与提示是否吵闹需真机观察。
- **恢复出厂流程**：撤销后需固件侧恢复出厂，本轮只做了「撤销 + 提示」，固件流程未验证。
- **设备列表在线态时效**：重连会清空实时缓存并重拉 REST；停留在列表页时仍依赖 WS 事件，
  若服务端建连不补发离线设备的 `device.status`，列表最多显示到下一次手动刷新/重连前。
  因此撤回了「断线期间掉线的设备永久显示在线」这一旧风险。
- **`online` 仅单 worker 正确**：服务端设备注册表在进程内，多 worker 部署时 `online`
  会分裂；本轮已约束所有遥控在单 worker 语义下使用。
- **多设备同时遥控**：每个遥控页独立 VM，但 `/app/ws` 是单连接、每连接 10 条/秒，
  多页同时拖动会互相挤占配额；规格未要求，未做全局节流。

## 8. 下一步

1. 4D：备份导入导出、设置页、素材；继续沿用「纵向切片先能用」的顺序。
2. 真机联调清单：配对 → 列表在线态 → 遥控音量/动作/角度 → 急停 → 校准保存 → 断网重连。
3. 并行清理 §6.4 记录的 P2（Compose 级测试、`idle` 回滚、草稿标记时机等）。
