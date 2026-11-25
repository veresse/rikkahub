# companion.v2 设备与 App 通信协议规范

- 版本：2.0.0
- 状态：Phase 1 定稿（实现以本文件为准）
- 动作库：`companion/shared/actions.json`
- 适用端：ESP32-S3 固件、FastAPI 服务端、Android App

> 说明：本仓库的 v1 **只实现了文字对话与网页管理，没有实现设备 WebSocket 与音频链路**。因此本规范在保留 v1 设计意图（`/device/ws`、二进制 Opus 帧、半双工、按键打断、5 分钟超时）的前提下，把音频帧头与状态机也一并定稿，作为服务端、固件与模拟器的共同契约。v1 兼容仅指「v1 设备只走音频通道、不接收 v2 控制消息」。

---

## 1. 传输与鉴权

### 1.1 设备通道

- 地址：`wss://<host>/device/ws`
- 鉴权：HTTP Header `Authorization: Bearer <device_token>`
  - **禁止**把 Token 放在 URL Query（防止 Nginx/代理访问日志泄露）。
  - 服务端只存 `device_token` 的 SHA-256 哈希，不存明文。
- 连接建立后 5 秒内必须发送 `hello`，否则服务端关闭连接（`4408 hello_timeout`）。
- 心跳：客户端每 20 秒发送 WebSocket Ping；服务端每 25 秒发送 Ping。连续 3 次无 Pong（约 75 秒）判定离线并清理会话。
- 单设备单连接：同一 `device_id` 新连接会踢掉旧连接（旧连接关闭码 `4409 replaced`）。

### 1.2 App 通道

- 地址：`wss://<host>/app/ws`
- 鉴权：HTTP Header `Authorization: Bearer <app_token>`
  - **禁止** Query 传 Token。
- `app_token` 由 `POST /api/auth/login` 签发，存 Android Keystore；服务端存哈希，可独立撤销。
- 撤销后服务端立即关闭该 Token 的全部 `/app/ws` 连接（关闭码 `4401 token_revoked`），后续 REST 返回 401。

### 1.3 配对与设备 Token 签发

1. App 调用 `POST /api/devices/pair-code`（需 App Bearer Token）生成 6 位数字配对码。
   - 有效期 300 秒；一次性；最多 5 次错误尝试；同一管理员每分钟最多生成 5 个。
   - 配对码只存哈希/带签名摘要，不落明文日志。
2. 设备经可信 HTTPS 调用 `POST /api/devices/pair`：

```json
{
  "code": "284915",
  "device_name": "桌面机器人",
  "firmware_version": "2.0.0",
  "protocol_version": 2,
  "capabilities": ["action", "volume", "expression"]
}
```

3. 成功返回（**仅此一次返回明文 Token**）：

```json
{
  "device_id": "uuid",
  "device_token": "<64 hex chars>",
  "protocol_version": 2,
  "action_catalog_version": "2.0.0"
}
```

4. 失败统一返回 `400 invalid_pair_code` / `410 pair_code_expired` / `429 rate_limited`，不区分「码不存在」与「码错误」。

### 1.4 权限隔离

| 凭据 | 可访问 | 严禁访问 |
| :--- | :--- | :--- |
| `device_token` | `/device/ws`、`/api/devices/pair`（仅配对时） | 管理/角色/会话/记忆/导入/App 接口 |
| `app_token` | `/api/**`（除 `/api/devices/pair`）、`/app/ws` | `/device/ws` |
| 无 Token | `/api/health` | 其他全部 |

---

## 2. 消息信封

所有 JSON 控制消息统一字段：

```json
{
  "v": 2,
  "type": "action.play",
  "session_id": "uuid-or-null",
  "turn_id": "uuid-or-null",
  "ts": 1764000000000
}
```

- `v`：协议版本，v2 固定 `2`。
- `type`：消息类型，见下表。
- `session_id`：一次设备连接的逻辑会话 ID，由 `hello_ack` 下发；断线重连后新开。
- `turn_id`：一轮「用户说话 → 助手回复」的 ID；与音频帧的 `turn_seq` 关联。
- 未知 `type` 必须忽略并记录 debug 日志，不得断开连接。
- 未知字段必须忽略（向前兼容）。

---

## 3. 握手

### 3.1 设备 → 服务端 `hello`

```json
{
  "v": 2,
  "type": "hello",
  "device_id": "uuid",
  "firmware_version": "2.0.0",
  "action_catalog_version": "2.0.0",
  "capabilities": ["action", "volume", "expression"],
  "audio": {
    "uplink": {"codec": "opus", "sample_rate": 16000, "channels": 1, "frame_ms": 60},
    "downlink": {"codec": "opus", "sample_rate": 24000, "channels": 1, "frame_ms": 60}
  },
  "ts": 1764000000000
}
```

- v1 设备：`v: 1` 或不含 `capabilities`。服务端按纯音频降级，不注入动作工具、不下发 v2 控制消息，`hello_ack.capabilities` 返回 `[]`。
- `action_catalog_version` 与服务端不一致时：服务端仍接受连接，但在 `hello_ack.warnings` 中返回 `action_catalog_mismatch`，并下发当前版本号；固件应记录并提示升级。

### 3.2 服务端 → 设备 `hello_ack`

```json
{
  "v": 2,
  "type": "hello_ack",
  "session_id": "uuid",
  "server_time": 1764000000123,
  "protocol_version": 2,
  "capabilities": ["action", "volume", "expression"],
  "action_catalog_version": "2.0.0",
  "audio": {
    "uplink": {"codec": "opus", "sample_rate": 16000, "channels": 1, "frame_ms": 60},
    "downlink": {"codec": "opus", "sample_rate": 24000, "channels": 1, "frame_ms": 60}
  },
  "state": {
    "volume": 70,
    "idle_enabled": true,
    "pan_offset": 0.0,
    "tilt_offset": 0.0
  },
  "limits": {
    "pan": [-90.0, 90.0],
    "tilt": [-20.0, 45.0],
    "volume": [0, 100],
    "default_max_duration_ms": 3000,
    "hard_max_duration_ms": 5000,
    "rate_limit_per_second": 10
  },
  "warnings": []
}
```

### 3.3 关闭码

| 码 | 含义 |
| :--- | :--- |
| `4401` | Token 无效/已撤销 |
| `4403` | 权限不匹配（App Token 连设备通道等） |
| `4408` | hello 超时 |
| `4409` | 被同设备新连接替换 |
| `4429` | 连接/消息速率超限 |
| `1011` | 服务端内部错误 |

---

## 4. 下行控制消息（服务端 → 设备）

所有控制消息都带 `action_id`（服务端生成的 UUID）用于 `action.result` 关联。

### 4.1 `action.play`

```json
{
  "v": 2,
  "type": "action.play",
  "action_id": "uuid",
  "name": "nod",
  "intensity": 2,
  "speed": 2,
  "repeat": 2,
  "source": "llm",
  "session_id": "uuid",
  "ts": 1764000000000
}
```

- `name` 必须是 `actions.json` 中的 18 个动作之一。
- `intensity` ∈ `{1,2,3}`，`speed` ∈ `{1,2,3}`，`repeat` ∈ `[1,5]`；动作不支持的参数使用默认值。
- `source` ∈ `{"llm","app"}`。
- 固件执行完成后必须回 `action.result`。
- 固件硬超时 = 该动作 `max_duration_ms`（普通 3000ms，`dance` 5000ms）；超时后强制停止、回中、停 PWM，并回 `status="timeout"`。

### 4.2 `servo.move`

```json
{
  "v": 2,
  "type": "servo.move",
  "action_id": "uuid",
  "pan": -30.0,
  "tilt": 10.0,
  "speed": 500,
  "source": "app",
  "ts": 1764000000000
}
```

- `pan` ∈ `[-90, 90]`，`tilt` ∈ `[-20, 45]`，`speed` ∈ `[0, 1000]`（ms，0 表示立即到位）。
- 越界时固件截断到安全边界并回 `status="failed", detail="angle_out_of_range"`；**不得**输出越界脉宽。
- 该消息为「覆盖语义」：同一设备未下发的 `servo.move` 只保留最后一条。

### 4.3 `volume.set`

```json
{
  "v": 2,
  "type": "volume.set",
  "action_id": "uuid",
  "volume": 60,
  "source": "app",
  "ts": 1764000000000
}
```

- `volume` ∈ `[0,100]`。0 = 静音，但**不改变**播放状态机、`segment_played`、按键打断和 5 分钟计时。
- 麦克风增益独立，不受此消息影响。

### 4.4 `expression.set`

```json
{"v": 2, "type": "expression.set", "name": "surprised", "ts": 1764000000000}
```

- `name` ∈ `actions.json.expressions`。

### 4.5 `idle.set`

```json
{"v": 2, "type": "idle.set", "enabled": true, "ts": 1764000000000}
```

- 仅在设备状态为 `IDLE` 时生效；进入 `LISTENING/THINKING/SPEAKING/ERROR` 自动暂停并归位。

### 4.6 `action.stop`

```json
{"v": 2, "type": "action.stop", "reason": "user_stop", "ts": 1764000000000}
```

- `reason` ∈ `{"user_stop","barge_in","server_cancel"}`。
- **最高优先级**：清空该设备待执行动作队列；10ms 内打断当前插值；平滑回中；停 PWM；回 `action.result(status="failed", detail="stopped")`（若当时有动作）。

### 4.7 校准消息

```json
{"v": 2, "type": "servo.calibrate_preview", "pan_offset": 1.5, "tilt_offset": -2.0}
{"v": 2, "type": "servo.calibrate_save", "action_id": "uuid", "pan_offset": 1.5, "tilt_offset": -2.0}
```

- `calibrate_preview` 仅实时叠加偏移，不持久化；`calibrate_save` 写入 NVS 并回 `action.result(status="done")` + `device.report`。
- 偏移范围 `[-30, 30]`；叠加后物理角度必须再次硬限位到 `[0,180]`。

---

## 5. 上行消息（设备 → 服务端）

### 5.1 `action.result`

```json
{
  "v": 2,
  "type": "action.result",
  "action_id": "uuid",
  "status": "done",
  "detail": "",
  "ts": 1764000000000
}
```

- `status` ∈ `{"done","failed","timeout"}`。
- `detail` 建议值：`""`、`angle_out_of_range`、`rate_limited`、`stopped`、`unknown_action`、`servo_error`。
- 服务端把结果回传给 `ActionOrchestrator`（LLM 工具结果）与所有在线 App。

### 5.2 `device.report`

```json
{
  "v": 2,
  "type": "device.report",
  "volume": 60,
  "pan": 0.0,
  "tilt": 0.0,
  "idle": true,
  "power": "usb",
  "state": "idle",
  "pan_offset": 0.0,
  "tilt_offset": 0.0,
  "ts": 1764000000000
}
```

- `state` ∈ `{"idle","listening","thinking","speaking","error"}`。
- 触发时机：连接成功后、音量变化、动作完成、校准保存、状态机切换、每 30 秒心跳上报。
- 服务端以此更新 `devices.last_report_json` 作为 `robot.get_status` 的快照来源。

### 5.3 `voice.event`（可选，用于 App 展示 ASR 文本）

```json
{"v": 2, "type": "voice.event", "event": "asr_final", "turn_id": "uuid", "text": "今天天气怎么样"}
```

- `event` ∈ `{"asr_partial","asr_final","tts_segment_start","tts_segment_end","turn_end"}`。
- 默认不下发原始音频；`text` 允许 App 展示与纠正。

---

## 6. 二进制音频帧

### 6.1 帧头（12 字节，大端）

| 偏移 | 长度 | 字段 | 说明 |
| :--- | :--- | :--- | :--- |
| 0 | 1 | magic | 固定 `0xA1` |
| 1 | 1 | version | 固定 `0x01` |
| 2 | 1 | kind | `0x01` 上行麦克风 Opus；`0x02` 下行 TTS Opus |
| 3 | 1 | flags | bit0=本轮最后一帧；bit1=静音帧 |
| 4 | 4 | seq | 帧序号，单调递增，溢出回绕 |
| 8 | 4 | turn_seq | 轮次序号，每个 `turn_id` 从 0 开始 |
| 12 | N | payload | Opus 包 |

### 6.2 音频参数

- 上行：16kHz、单声道、20/60ms（以 `hello` 协商为准）。
- 下行：24kHz（协商可降为 16kHz）、单声道。
- 服务端只做短暂内存缓冲，**不落盘、不入库、不打印原始音频**。
- 半双工：设备处于 `SPEAKING` 时暂停麦克风采集与上行；服务端忽略该期间的上行帧。
- 旧帧过滤：服务端丢弃 `turn_seq` 小于当前播放代次的帧；设备丢弃 `turn_seq` 小于当前轮次的 TTS 帧。
- 打断：设备短按立即本地停扬声器、递增播放代次、发送 `cancel`；服务端取消 LLM/TTS 并回 `cancel_ack`，同时下发 `action.stop(reason="barge_in")`。

---

## 7. 双源指令仲裁（LLM vs App）

每个在线设备在服务端维护**唯一**调度队列：

| 消息 | 语义 | 规则 |
| :--- | :--- | :--- |
| `action.stop` | 最高优先级 | 立即清空待执行动作队列并下发；当前动作标记为 `stopped` |
| `volume.set` / `servo.move` | 覆盖（Last-Write-Wins） | 同类型未发出的指令只保留最后一条；仍受 10 次/秒限速 |
| `action.play` | FIFO 排队 | 按入队顺序下发；默认队列上限 8 条，超出回 `failed(queue_full)` |
| 设备离线 | 直接失败 | 返回 `failed(device_offline)`；**严禁**缓存等上线补发 |

限速：

- 每设备入站控制消息 ≤ 10 次/秒（滑动窗口）；超出直接丢弃并回 `failed(rate_limited)`。
- 每设备 `action.play` 同时只执行 1 条；后续排队。
- LLM 单轮动作类工具调用 ≤ 3 次（服务端配置），超出返回结构化错误给模型，不下发设备。

---

## 8. App 通道消息

### 8.1 App → 服务端（遥控）

所有消息都带 `request_id`（客户端 UUID）与 `device_id`。

| type | 字段 | 说明 |
| :--- | :--- | :--- |
| `remote.action.play` | `name,intensity,speed,repeat` | 等价于 LLM 的 `action.play` |
| `remote.servo.move` | `pan,tilt,speed` | 连续角度控制 |
| `remote.volume.set` | `volume` | 0–100 |
| `remote.idle.set` | `enabled` | 待机小动作 |
| `remote.action.stop` | `reason` | 急停 |
| `remote.calibrate.preview` | `pan_offset,tilt_offset` | 校准预览 |
| `remote.calibrate.save` | `pan_offset,tilt_offset` | 保存到 NVS |
| `remote.get_status` | — | 请求设备立即上报 |

示例：

```json
{
  "v": 2,
  "type": "remote.action.play",
  "request_id": "uuid",
  "device_id": "uuid",
  "name": "nod",
  "intensity": 3,
  "speed": 2,
  "repeat": 1,
  "ts": 1764000000000
}
```

### 8.2 服务端 → App（事件广播）

| type | 字段 | 说明 |
| :--- | :--- | :--- |
| `device.status` | `device_id, online, reason` | 上线/离线 |
| `device.report` | 同设备上报 | 实时状态 |
| `action.result` | `device_id, action_id, status, detail, source` | 执行结果 |
| `conversation.turn_state` | `conversation_id, state`（`idle/generating`） | 会话是否正在生成 |
| `remote.ack` | `request_id, ok, error` | 遥控请求受理结果 |
| `error` | `code, message, request_id?` | 统一错误 |

设备离线时，`remote.*` 立即返回：

```json
{"v": 2, "type": "remote.ack", "request_id": "uuid", "ok": false, "error": "device_offline"}
```

**不缓存、不补发。**

---

## 9. 状态机

```text
BOOT → IDLE
IDLE --短按--> LISTENING
LISTENING --检测到语音结束/VAD--> THINKING
THINKING --收到首段 TTS--> SPEAKING
SPEAKING --segment 播放完--> SPEAKING/IDLE
ANY --长按(≥1500ms)--> IDLE（结束本轮）
ANY --静默 5 分钟--> IDLE（结束本轮）
SPEAKING --短按--> IDLE（本地停声 + cancel）
ERROR --短按--> LISTENING（重试）
```

- 上电/待机：麦克风关闭，仅保留按键与 WS 心跳。
- `SPEAKING`：暂停麦克风采集与上传（半双工）。
- 动作与 TTS 并行：`action.play` 不阻塞音频播放。
- 音量 0：状态机照常流转，屏幕显示回复文字（默认开启）。

---

## 10. 错误码

| code | 场景 |
| :--- | :--- |
| `unauthorized` | 缺少/无效 Token |
| `token_revoked` | Token 已撤销 |
| `forbidden` | Token 类型不匹配 |
| `invalid_pair_code` | 配对码错误/不存在 |
| `pair_code_expired` | 配对码过期 |
| `device_offline` | 设备不在线 |
| `queue_full` | 动作队列满 |
| `rate_limited` | 超过 10 次/秒 |
| `angle_out_of_range` | 角度越界（服务端/固件均校验） |
| `unknown_action` | 动作名不在动作库 |
| `action_limit_exceeded` | 单轮动作工具调用超过 3 次 |
| `timeout` | 动作/工具超时 |
| `internal_error` | 未预期异常 |

---

## 11. 安全与隐私红线

1. 禁止明文 Token 出现在 URL、日志、数据库；服务端只存哈希。
2. 禁止在 App/固件中提供跳过 TLS 校验的开关；只信任系统/内置根证书。
3. 原始音频只在内存短暂缓冲，不落盘、不入库、不打印。
4. 设备通道禁止执行任意主机命令；本期不支持远程 OTA。
5. 角色 `system_prompt` 只读导入，不自动改写。
6. 所有 REST 统一错误格式 `{"detail": "<code>", "message": "<可读文本>"}`，不返回堆栈。

---

## 12. 版本与兼容

- `v2` 设备：完整动作/音量/表情/校准。
- `v1` 设备：只走音频通道；服务端不注入动作工具、不下发 v2 控制消息；`/api/actions/catalog` 仍可被 App 使用。
- 动作库版本变更：只改 `actions.json` 的 `version` 与档位/关键帧，不改消息 envelope；固件在构建期从同一文件生成动作表。
