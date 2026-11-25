# 06 · Block D-2：App WebSocket 网关（`/app/ws`）与调度器超时强停

- 日期：2026-11-25
- 状态：✅ 完成
- 分支：`feature/companion-robot-v2`

## 1. 目标与范围

1. 新增 `/app/ws`：App Token 握手鉴权、遥控指令转发、`remote.ack`、设备事件广播。
2. App 遥控与 LLM 工具调用共用同一设备串行队列，遵守第 7 节仲裁（FIFO、覆盖、急停、限速）。
3. Token 撤销时立即断开该 Token 的全部 App 连接（`4401 token_revoked`）。
4. 补上 Block C 遗留项：动作执行超时后主动下发 `action.stop(server_cancel)`。

## 2. 改动文件清单

### 新增

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/app_gateway.py` | App 会话、遥控解析/校验/入队、事件广播、Token 撤销断开 |
| `companion/server/tests/test_app_gateway.py` | 11 个 App 通道行为测试 |

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/action_orchestrator.py` | `enqueue()`（立即回 `queued`）、排队超时丢弃、超时后 `action.stop`、`get_device_scheduler()`、`_drop_superseded()` |
| `companion/server/app/routers/ws.py` | 新增 `app_gateway` 与 `/app/ws` 路由 |
| `companion/server/app/routers/auth.py` | `logout` / `tokens/{id}/revoke` 后断开对应 WS 连接 |
| `companion/server/app/main.py` | 关闭时同时清理 App 连接 |
| `companion/docs/protocol_v2.md` | 新增 8.4 服务端实现补充 |
| `companion/README.md` | 更新 WS 网关与后续 Block 描述 |

## 3. 行为说明

### 3.1 遥控转发

| App 消息 | 下发指令 | 队列语义 |
| :--- | :--- | :--- |
| `remote.action.play` | `action.play`，`source="app"` | FIFO |
| `remote.servo.move` | `servo.move` | 覆盖（Last-Write-Wins） |
| `remote.volume.set` | `volume.set` | 覆盖 |
| `remote.idle.set` | `idle.set` | FIFO |
| `remote.action.stop` | `action.stop` | 立即清空队列 |
| `remote.calibrate.preview` | `servo.calibrate_preview` | 直接下发，不排队 |
| `remote.calibrate.save` | `servo.calibrate_save` | FIFO，设备回 `action.result` |
| `remote.get_status` | 无下行消息 | 广播当前缓存 `device.report` |

- 参数非法分别返回 `unknown_action` / `invalid_arguments` / `angle_out_of_range`。
- 设备离线立即 `remote.ack(ok=false, error="device_offline")`，不缓存、不补发。
- 受理成功仅代表**已入队**（`ok=true`）；最终结果通过 `action.result`/`device.report` 事件到达。
- 缺失 `request_id`/`device_id` 回 `error(invalid_request)`，连接保持。
- 二进制帧与非 `remote.*` 消息忽略。

### 3.2 排队与超时

- `_DeviceScheduler.enqueue()` 立即返回 `queued`，不阻塞 App 请求。
- 排队超过各自 `timeout_ms`（动作 = `max_duration_ms + 1500ms`；其余 3000ms）的命令在出队时被丢弃。
- `submit()`（LLM 路径）超时后除返回 `timeout` 外，主动下发 `action.stop(reason="server_cancel")`，
  解决 Block C 记录的「超时不强制停止」偏差。

### 3.3 鉴权与撤销

- `/app/ws` 只接受 App Token；设备 Token 冒用回 `4403 wrong_token_type`，未知/撤销/过期回 `4401`。
- `POST /api/auth/tokens/{id}/revoke` 与 `POST /api/auth/logout` 成功后立即关闭对应连接。
- 事件广播按 Token 分组保存会话；最后一个会话断开时从 EventBus 退订。

## 4. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_app_gateway.py
...........                                                              [100%]
11 passed in 0.33s
```

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_action_orchestrator.py
.............                                                            [100%]
13 passed in 1.30s
```

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q
........................................................................ [ 54%]
.............................................................            [100%]
133 passed in 5.29s
```

```text
$ & "...\companion-venv\Scripts\python.exe" -c "from companion.server.app.routers import ws; ..."
[('APIWebSocketRoute', '/device/ws'), ('APIWebSocketRoute', '/app/ws')]
```

## 5. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| `remote.ack` 只表示入队/受理，不等待执行完成 | 遥控要低延迟；App 可从事件流看结果 | App 需要订阅 `action.result` 才能显示完成态 |
| `remote.get_status` 回服务端缓存快照，不新增下行消息 | 协议没有定义「请求上报」消息 | 若设备实际状态比缓存新，App 会短暂滞后（设备状态变化会主动上报） |
| `calibrate.preview` 绕过队列直接下发 | 拖动滑杆需要即时反馈，且无 `action_id`、不等回执 | 与正在执行的动作可能竞争串口，由固件/设备层串行化保证 |
| 排队超时的遥控命令直接丢弃 | 避免很久以前的遥控动作突然执行 | App 只会收到 `action.result(failed/timeout)` 或依靠自身超时 |
| Token 撤销只断开已建立连接，不做周期复验 | 协议只要求撤销时立即断开 | 长连接期间 Token 过期不会主动断开，直到下次重连 |
| 未实现 App 连接时推送设备列表/快照 | 协议未要求；REST `GET /api/devices` 已覆盖 | App 需自行拉取初始状态 |

## 6. 未完成项 / 阻塞点

- 音频帧、半双工、打断（后续音频 Block）。
- 真实 uvicorn + 双端 WebSocket 端到端测试（Phase 3 模拟器）。
- App 按 Token 分组目前只用于断开；未做每 Token 连接数上限。

## 7. 下一步

- 汇总 Block D 审查意见并修复。
- 进入音频链路 Block（上行帧解析、ASR、TTS 下行、`voice.event` 状态机）。
