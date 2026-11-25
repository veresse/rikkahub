# 08 · Phase 2 二次审查修复（终态去重 / 取消范围 / 覆盖语义）

- 日期：2026-11-25
- 状态：✅ 完成（Important 全部修复；部分 Minor 记录为已知限制）
- 分支：`feature/companion-robot-v2`
- 审查对象：`c8ceb3b9`（07 提交）

## 1. 目标与范围

第二轮独立审查对 07 的结论为 **No（暂缓合并）**，Critical 为 0，Important 3 条：

| 编号 | 问题 | 影响 |
| :--- | :--- | :--- |
| I1 | 聊天请求取消时无条件下发设备级 `action.stop` | 会清空 App 与 LLM 共享队列，打断 App 遥控 |
| I2 | 执行超时后设备迟到的 `action.result` 会二次广播 | 同一 `action_id` 两条终态，且 `source=""` 越出协议枚举 |
| I3 | 队列满时先判容量、后做覆盖 | 新的 `servo.move` 被 `queue_full` 拒绝，旧角度继续执行 |

另有 Minor：`inactive` 提示不可达、断连丢失最后一条上报、配置死常量、
`create_task` 无强引用、`action.result` 仍在设备读循环内同步发布、audio 白名单过宽、
日志对 `queue_full` 描述错误、测试未真正验证 App 端收到服务端终态。

本轮只修这些，不新增功能。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/routers/chat.py` | 移除「任何取消都全局急停」的钩子；取消语义交给调度器的定向中止 |
| `companion/server/app/services/orchestrator.py` | 说明取消只影响本轮在途命令；`inactive` 原因可达 |
| `companion/server/app/services/action_orchestrator.py` | `submit`/`enqueue` 先覆盖再判容量；终态广播改用 `publish_async` |
| `companion/server/app/services/device_gateway.py` | 迟到/未知 `action.result` 丢弃；`action.result`/`voice.event` 后台发布；下线前冲刷最后一条上报；audio 采样率/声道白名单 |
| `companion/server/app/services/device_registry.py` | 绑定解析返回含已撤销设备（按 `is_active` 优先），让 `inactive` 原因可达 |
| `companion/server/app/services/events.py` | 新增 `publish_async()`（强引用 + 异常回调）与 `EventBus.clear()` |
| `companion/server/app/config.py` | 新增 `DEVICE_STATUS_TIMEOUT_SECONDS`、`WS_PING_*` 配置 |
| `companion/server/app/main.py` | uvicorn WS 参数改为读 `settings` |
| `companion/deploy/companion.service` | 改用 `python -m companion.server.app.main`，不再复制一份 uvicorn 参数 |
| `companion/docs/protocol_v2.md` | 8.2 终态只广播一次、`queue_full` 只走 ack；8.4 取消范围与去重说明 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 08 |

### 测试

| 路径 | 新增覆盖 |
| :--- | :--- |
| `companion/server/tests/test_action_orchestrator.py` | 取消只停本轮在途动作、队列满仍可覆盖、`link.send` 异常终态、排队超时不发 `action.stop` |
| `companion/server/tests/test_app_gateway.py` | Harness 改用全局事件总线；被覆盖/超时终态必须真正到达 App 的 WebSocket |
| `companion/server/tests/test_device_gateway.py` | 已结算回执不二次广播、未知 `action_id` 不广播、断连前最后一条上报落库 |
| `companion/server/tests/test_chat_action_loop.py` | 已撤销设备得到 `inactive` 提示；取消聊天流只对本轮在途动作发 `server_cancel` |
| `companion/server/tests/test_action_tools.py` | `robot_tool_injection` 的 `inactive / offline / unbound` 原因与提示 |

## 3. 行为变化

### 3.1 取消范围（I1）

- `POST /api/chat/completions` 的 SSE 断开/停止**不再**调用 `ActionOrchestrator.stop()`。
- 取消会在 `_DeviceScheduler.submit()` 的 `CancelledError` 分支中止本轮命令：
  只有「已下发且仍是 current」时才下发 `action.stop(server_cancel)`。
- 因此 App 同时进行的 `servo.move`/`volume.set` 不会被聊天页切后台打断；
  本轮尚未下发的排队命令直接移除。

### 3.2 终态去重（I2）

- `DeviceSession.resolve_result()` 只对仍在 `_pending` 中的 `action_id` 返回来源；
  其余（服务端已超时/急停/断连，或从未下发）返回 `None`，`_on_message` 直接丢弃。
- 同一 `action_id` 的 `action.result` 因此最多广播一次，`source` 恒为 `app` 或 `llm`。
- 设备侧被打断后按协议回 `failed/stopped` 的回执仍会更新设备内部状态，只是不再打扰 App。

### 3.3 覆盖优先（I3）

- `submit()` / `enqueue()` 都先执行 `_drop_superseded()`，再判断容量。
- 队列已满且存在同类旧命令时，新命令替换旧命令；不存在同类命令时仍返回 `queue_full`。

### 3.4 其他

- `resolve_device_for_conversation()` 返回最近绑定设备（含 `is_active=false`），
  `robot_tool_injection()` 的 `inactive` 分支在生产路径可达。
- 设备正常下线前先冲刷合并队列并落库，最后一条 `device.report` 不再丢失。
- `action.result` / `voice.event` 改为后台发布，慢 App 不再阻塞设备读循环。
- `hello.audio` 采样率只接受上行 16000、下行 16000/24000，且必须单声道；否则回落默认值
  并返回 `audio_defaulted`。
- systemd 与 `python -m` 共用 `settings` 中的 WS 参数，避免两处硬编码漂移。

## 4. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
........................................................................ [ 47%]
........................................................................ [ 94%]
.........                                                                [100%]
153 passed in 4.69s
```

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_action_orchestrator.py
...................                                                      [100%]
19 passed in 1.76s
```

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py
..................                                                       [100%]
18 passed in 0.52s
```

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_app_gateway.py
.................                                                        [100%]
17 passed in 0.40s
```

## 5. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 聊天取消不发全局 `action.stop`，依赖 `submit()` 取消路径 | 共享队列的全局 stop 会误杀 App 命令；定向中止已覆盖本轮动作 | 若客户端取消时生成器恰好停在 `yield`（无在途动作），不会额外发 stop，这是期望行为 |
| 未入队请求（`queue_full`/`rate_limited`/参数非法）只回 ack | 没有 `action_id` 可关联，广播终态反而制造孤儿事件 | App 必须同时处理 ack 错误与终态事件 |
| 未知 `action_id` 的 `action.result` 一律丢弃 | 协议要求 `source ∈ {llm, app}`，服务端无法归属的消息不应外发 | 设备跨重连补发的旧结果不会出现在 App |
| `remote.ack` 与终态仍不保证严格先 ack 后 result | 需要把 ack 拆成「已受理」与「已入队」两段，改动面大 | 低概率下 App 先收到 result 再收到 ack，需按 `action_id` 容错 |

## 6. 未完成项 / 阻塞点

- 每 Token / 每设备连接数上限、连接建立速率限制、`4429` 关闭码仍未实现。
- `action.result` 的 `_persist_report` 与事件广播仍在同一 writer 任务里，数据库慢时上报会
  在合并队列中只保留最新一条（这是设计取舍）。
- 音频链路（二进制帧、半双工、barge-in）、Phase 3 双模拟器、Android App、ESP32 固件未开始。

## 7. 下一步

1. 提交本轮修改并派第三轮审查（重点：终态是否恰好一次、取消是否只影响本轮、覆盖+容量边界）。
2. 通过后进入 Phase 3：`device_simulator` + `app_simulator` + 真实 uvicorn 全链路测试。
3. 再评估音频 Block 与真机联调。
