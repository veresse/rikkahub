# 05 · Block D-1：设备 WebSocket 网关（`/device/ws`）

- 日期：2026-11-25
- 状态：✅ 完成（设备通道；App 通道见下一份日志）
- 分支：`feature/companion-robot-v2`

## 1. 目标与范围

1. 新增 `/device/ws`：设备 Token 握手鉴权、`hello`/`hello_ack`、v1 降级、单设备单连接。
2. 建立 `DeviceLink` 的真实实现：指令下发、`action.result` 关联、设备状态快照。
3. `device.report` 落库（音量/待机/状态/校准偏移/最近快照）并广播事件。
4. 断开清理：注销链路、失败所有在途调用、清理调度队列、广播离线。
5. 服务关闭时主动断开所有设备连接。

## 2. 改动文件清单

### 新增

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/clock.py` | 统一毫秒时间戳 |
| `companion/server/app/services/events.py` | 进程内事件总线（设备网关 → App 网关） |
| `companion/server/app/services/device_gateway.py` | 握手、会话、`DeviceLink`、上报落库、上线/离线广播 |
| `companion/server/app/security/ws_auth.py` | WS 握手鉴权（设备/App Token 分表隔离、撤销/过期） |
| `companion/server/app/routers/ws.py` | Starlette 适配与 `/device/ws` 路由 |
| `companion/server/tests/test_device_gateway.py` | 13 个网关行为测试 |
| `companion/server/tests/test_ws_auth.py` | 10 个握手鉴权测试 |
| `companion/server/tests/test_ws_router.py` | 路由外壳与 Starlette 消息适配测试 |

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/config.py` | 新增 `DEVICE_HELLO_TIMEOUT_SECONDS`、`WS_MAX_MESSAGE_BYTES` |
| `companion/server/app/services/device_registry.py` | `unregister_device_link` 支持校验链路身份，避免旧连接误删新连接 |
| `companion/server/app/main.py` | 挂载 WS 路由；关闭时 `device_gateway.shutdown()` |

## 3. 关键实现说明

### 3.1 握手与降级

- `Authorization: Bearer <device_token>`，Token 只查 `devices.token_hash` 且 `is_active=true`。
- App Token 连设备通道 → 关闭码 `4403 wrong_token_type`；未知/撤销/禁用 Token → `4401`。
- 先 `accept` 再鉴权，否则 Starlette 只能回 HTTP 403，无法发送 4401/4403。
- 5 秒内必须收到 `hello`，否则 `4408 hello_timeout`；首条消息不是 `hello` → `4408 invalid_hello`。
- `hello.device_id` 与 Token 绑定的设备不一致 → `4403 device_mismatch`。
- `v=1` 或缺 `capabilities` → 降级为 v1：`hello_ack.capabilities=[]`，协议版本 1。
- `action_catalog_version` 不一致 → `warnings=["action_catalog_mismatch"]`，仍接受连接。
- 同一设备新连接先注册再关闭旧连接（`4409 replaced`）；旧连接退出不会注销新会话。

### 3.2 指令与回执

- `DeviceSession` 实现 `DeviceLink`：
  - 自动补齐 `v=2`、`session_id`、`ts`；
  - 按 `action_id` 建 Future，等待 `action.result`；
  - `action.stop` 不等回执，直接把所有在途等待者标记为 `stopped`；
  - 无 `action_id` 的指令（如校准预览）发送即返回。
- 连接断开/被替换时，所有在途 Future 以 `failed(device_offline)` 结束，LLM 工具循环不会悬挂。
- `get_status()` 返回内存快照（`volume/pan/tilt/idle_enabled/state/offsets/power/online`）。

### 3.3 状态上报

- `device.report` 更新数据库 `volume`、`idle_enabled`、`state`、`pan_offset`、`tilt_offset`、
  `last_report_json`、`last_seen_at`，并广播 `device.report` 给 App。
- 数值做边界处理：`volume` 截断到 0–100，偏移截断到 ±30，未知 `state` 不覆盖旧值。
- 二进制音频帧暂时忽略（音频 Block 实现），不阻塞控制消息。
- 未知消息类型忽略，不断开连接。

## 4. 实际执行的命令与真实输出

### 4.1 设备网关

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py
.............                                                            [100%]
13 passed in 0.53s
```

### 4.2 握手鉴权

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_ws_auth.py
..........                                                               [100%]
10 passed in 0.35s
```

### 4.3 路由外壳

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q \
    companion/server/tests/test_ws_router.py companion/server/tests/test_ws_auth.py
..............                                                           [100%]
14 passed in 0.38s
```

### 4.4 全量

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q
........................................................................ [ 60%]
................................................                         [100%]
120 passed in 4.99s
```

## 5. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| `4408` 同时表示「hello 超时」与「首条消息非法」 | 协议只定义了 4408；不再新增自定义关闭码 | 客户端日志需要看 `reason` 区分 |
| WS 先 accept 再鉴权 | 只有已升级的连接才能发送 4401/4403 关闭码 | 未授权客户端会完成一次 WS 升级（不交换任何业务数据） |
| Ping/Pong 交给 uvicorn，本层只按连接存活性判断在线 | Starlette 不暴露 Pong 事件；避免自造低层心跳 | 需要部署时确认 uvicorn 的 `ws_ping_interval` 默认值可用 |
| 二进制音频帧暂时忽略 | 音频链路不在 Block D 范围 | 音频 Block 需要补帧解析与半双工状态机 |
| `device.report` 每条都写库 | 设备上报频率低（30 秒/状态变化），实现简单可靠 | 若固件异常高频上报，需要加写库节流 |
| 超长 WS 文本直接关闭 `4429` | 防止单条消息打爆内存 | 当前上限 64KB，正常协议消息远小于该值 |

## 6. 未完成项 / 阻塞点

- `/app/ws`（AppGateway）未实现，事件目前只进内存总线。
- 动作超时强停仍未接入：等 App 通道完成后统一在调度器/设备会话层补齐。
- 音频帧、半双工与打断未实现。
- 未做真实 uvicorn + WebSocket 端到端测试（Phase 3 模拟器负责）。

## 7. 下一步

Block D-2：实现 `AppGateway`（`/app/ws`），包括遥控指令转发、`remote.ack`、事件广播、
Token 撤销时主动断开连接；随后统一审查并补协议文档与部署说明。
