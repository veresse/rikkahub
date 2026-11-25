# 07 · Phase 2 审查修复（Block C / D-1 / D-2）

- 日期：2026-11-25
- 状态：✅ 完成（Critical / Important 已修，Minor 部分保留）
- 分支：`feature/companion-robot-v2`

## 1. 目标与范围

本轮不新增功能，只处理三个独立审查子智能体对 Block C、D-1、D-2 的结论：

1. 补全「`remote.ack=queued` 之后一定会有终态」的语义；
2. 为调度器的**在途**命令增加真实超时与 `action.stop`；
3. 修掉重连 / 撤销 / 广播 / 上报路径上的竞态与阻塞；
4. 把 v1 设备与参数契约对齐协议；
5. 补齐支撑上述行为的测试与协议文档。

审查基线：

- Block C：`With fixes`（C1 `get_status` 无界等待、C2 取消后动作继续、I1 假超时/worker 卡死、I2 重连队列复活）。
- Block D-1：`With fixes`（Critical 主张「丢失 `action.result` 永久卡死队列」，由本轮终态事件 + 在途超时关闭）。
- Block D-2：`No`（C1 被丢弃命令无终态、C2 在途无超时；I1–I7）。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/action_orchestrator.py` | 终态事件、在途 `wait_for`、排队 deadline、`_stopped`、`_send_stop` 超时、取消路径 |
| `companion/server/app/services/app_gateway.py` | 全量 `remote.*` 限速、v1 门控、撤销 TTL、`action_id` 回执、广播允许列表、发送超时、兜底异常 |
| `companion/server/app/services/device_gateway.py` | 每设备安装/卸载锁、上报合并 writer、`idle/ts` 对外形状、边界校验、v1 拒绝、关闭连接忽略上报、audio 校验 |
| `companion/server/app/services/action_tools.py` | 注入原因枚举与更准确的状态提示 |
| `companion/server/app/services/orchestrator.py` | `robot.get_status` 3s 超时、`turn_id`、校验后再扣次数、离线原因、regenerate 持久化 |
| `companion/shared/catalog.py` | 不支持的参数按协议忽略/回落默认值，显式 `None` 视为缺省 |
| `companion/server/app/routers/chat.py` | 客户端断开/取消时对当前设备下发 `action.stop(server_cancel)` |
| `companion/server/app/routers/devices.py` | `DELETE /api/devices/{id}` 立即断开在线 `/device/ws`（4401） |
| `companion/server/app/main.py` | uvicorn `ws_ping_interval/ws_ping_timeout/ws_max_size` |
| `companion/deploy/companion.service` | 同上，写入 systemd 启动参数 |
| `companion/docs/protocol_v2.md` | 3.3 关闭码原因、8.1 `get_status`、8.2 `action_id`/终态事件、8.4 实现补充 |
| `companion/docs/v1_to_v2_audit.md` | 执行状态更新到本轮 |

### 测试

| 路径 | 新增覆盖 |
| :--- | :--- |
| `companion/server/tests/test_action_orchestrator.py` | 在途超时 + `action.stop` + 终态事件、排队过期终态、`invalidate` 不复活 worker |
| `companion/server/tests/test_app_gateway.py` | `action_id` 回执、被覆盖终态、v1 拒绝、撤销竞态、限速覆盖急停/查询 |
| `companion/server/tests/test_device_gateway.py` | `device.report` 的 `idle/ts` 与边界夹紧、v1 会话拒绝 v2 控制 |
| `companion/server/tests/test_devices.py` | 撤销设备时关闭在线连接 |
| `companion/tests/test_actions_catalog.py` | 不支持的参数回落默认值、显式 `None` |
| `companion/server/tests/test_chat_action_loop.py` | v1 提示改为「固件不支持」而非「离线」 |

## 3. 行为变化

### 3.1 调度器与终态

- 每条 App 命令在以下路径都会收到 `action.result(failed, detail=...)`：
  `queue_full`、`superseded`、排队 `timeout`、执行 `timeout`、`device_offline`、`internal_error`、`stopped`。
- 排队超时只决定「是否还值得下发」，不再从入队开始计算执行超时；执行超时从真正 `link.send` 开始。
- worker 用 `asyncio.wait_for(link.send(...), timeout=_command_timeout_ms(...))` 包住发送；
  超时后广播终态并下发 `action.stop(server_cancel)`。
- `submit()` 的取消（客户端断开 / 用户停止）只 abort 当前排队项；只有已经下发且仍是当前项时才急停。
- `invalidate_device_scheduler()` 会终止 current/queue 的全部 future、清空队列、置 `_stopped`，
  worker 的 `finally` 不再复活。
- `_send_stop` 自身加 1s 超时，避免 TCP 背压拖住对话流。

### 3.2 App 网关

- 所有 `remote.*`（包括 `action.stop`、`calibrate.preview`、`get_status` 与参数非法请求）先过每连接
  10 条/秒限速，超限回 `rate_limited`。
- `remote.ack` 对带 `action_id` 的命令回填 `action_id`，App 可把 `action.result` 对回请求。
- `protocol_version < 2` 的设备统一回 `unsupported_protocol`，不下发 v2 信封。
- `revoke()` 维护 1h TTL 的撤销集合；握手完成挂载时同步复核，封住「鉴权通过后才挂上」的竞态。
- 广播只转发协议允许的事件类型；每个 session 的发送有 2s 超时，并由 `gather` 并发，慢客户端不阻塞设备读循环。
- `shutdown()` 清空会话并退订；`_on_message` 有总兜底并回 `internal_error` ack。

### 3.3 设备网关

- `_install` / `_teardown` 按 `device_id` 串行化；替换连接时先关闭旧连接，只有仍是当前 link 才广播上线/离线。
- 已关闭/被替换的 session 不再处理 `device.report` 或写入数据库。
- `device.report` 进入容量 1 的合并队列，由独立 writer 落库并广播；对外形状使用协议字段 `idle` 与 `ts`，
  数字做有限性与边界夹紧（pan ±90、tilt -20..45、offset ±30）。
- v1 session 的 `send()` 直接回 `unsupported_protocol`，不会写出 v2 控制消息。
- `hello.audio` 做 codec / sample_rate / channels / frame_ms 校验，非法字段回落默认并产生 `audio_defaulted`。
- `hello_ack` 的 volume/rate limits 改为读取 `actions.json` 单一事实源。
- 服务关闭时注销 link 并失效调度器。

### 3.4 对话与工具

- `robot.get_status` 3s 超时；超时回 `{"ok": false, "error": "timeout"}`。
- `turn_id` 写入所有 LLM 控制消息；参数校验通过后才消耗单轮动作次数。
- 动作不支持的参数按协议忽略并回落默认值；显式 `null` 视为缺省。
- 注入提示区分 `unbound / inactive / offline / unsupported_protocol / unsupported_capability`。
- `regenerate` 分支改为重新赋值 `messages` 列表，避免普通 JSON 列原地 append 不落库。

## 4. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
........................................................................ [ 50%]
.......................................................................  [100%]
143 passed in 4.66s
```

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_app_gateway.py
......................                                                   [100%]
22 passed
```

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py
...................                                                      [100%]
19 passed
```

## 5. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 仅「已下发」的命令超时才发 `action.stop` | 修 C2 时不能误杀正在执行的其它动作 | 排队超时完全交给 App 侧或终态事件 |
| 排队 deadline 只在 worker 出队时检查 | 单设备串行，当前动作结束前无法安全丢弃正在执行的命令 | 排队终态可能延迟到当前动作超时（最长约 6.5s）后才到达 |
| `get_status` 只回请求方并标 `source:"server"` | 避免多 App 互相覆盖 UI，也避免伪装成设备新上报 | App 需要自己区分缓存与实时上报 |
| `calibrate.preview` 仍绕过队列 | 拖动滑杆需要即时反馈，协议未定义回执 | 与正在执行的动作竞争串口，依赖设备层串行化 |
| 每 Token 连接数上限、4429 未实现 | 单管理员场景风险有限，本轮聚焦 Critical/Important | 移动端重连风暴仍可能放大资源占用 |

## 6. 未完成项 / 阻塞点

- 音频二进制帧、半双工、barge-in（后续音频 Block）。
- Phase 3：`device_simulator` / `app_simulator` 与真实 uvicorn 双端 WebSocket 端到端测试。
- Android App 与 ESP32 固件尚未开始。
- Block C 审查中属于既有问题的 usage 末帧 / `finish_reason` 硬编码、真实 DeepSeek 流式 MockTransport 测试，
  本轮未改，留待统一处理。
- 每 Token 连接数上限、连接建立速率限制、4429 关闭码。

## 7. 下一步

1. 将本轮改动提交到 `feature/companion-robot-v2`。
2. 派审查子智能体复查本轮修复，重点验证「queued 之后必有终态」与「超时一定发 stop」。
3. 进入 Phase 3：双模拟器 + 端到端测试；再评估音频 Block。
