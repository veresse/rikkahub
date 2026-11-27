# 23 · 音频 Block 设计（ASR / TTS / barge-in）

- 状态：**设计待批准**（批准后才写实施计划与代码）
- 日期：2026-11-26
- 分支：`feature/companion-robot-v2`
- 范围：服务端音频链路 + 协议补齐 + 模拟器 + 离线测试
- 不在范围：Android App、ESP32 固件实现、真实云/真机验收（本机无 Key、无真机）

## 1. 现状与约束

- v1 服务端只有文字对话与网页管理，**没有任何音频链路**：没有 ASR/TTS 适配器、没有 Opus 编解码、
  没有分句 TTS、没有打断/`segment_played`/5 分钟超时（见 `companion/docs/v1_to_v2_audit.md:104`）。
- 当前 `DeviceGateway._on_message()` 对二进制帧直接 `return`（`device_gateway.py:823-825`），
  `StarletteChannel` 只有 `send_json()`（`routers/ws.py:36-38`），没有 `send_bytes()`。
- 协议 §6 已定义 12 字节帧头、半双工、旧帧过滤、`cancel`/`cancel_ack` 语义，
  但 §5 上行消息表没有定义 `voice.start` / `voice.end` / `voice.cancel` / `voice.segment_played`，
  §4 也没有 `voice.segment` / `voice.cancel_ack` / `voice.error`。这些是本次要补齐的契约。
- `ChatOrchestrator.stream_chat()` 只在正常结束后写 `MessageNode`（`orchestrator.py:302-355`）；
  被取消时这一轮用户文本与部分回复会丢，无法展示“语音中断标记”。
- barge-in 取消聊天生成时，现有动作调度器会在 `CancelledError` 路径发
  `action.stop(server_cancel)`（`action_orchestrator.py:171-177/188-193`）；
  协议 §6.2 要求 barge-in 发 `barge_in`。两者必须合并成一条，不能重复下发。
- 约束：`tilt ∈ [-20,45]`、`pan ∈ [-90,90]`、普通动作 ≤3000ms、`dance` ≤5000ms 不变；
  原始音频不落盘、不入库、不打印；不读取/提交根 `local.properties`。

## 2. 目标与非目标

**目标**

1. 设备二进制帧可解析/编码，服务端能收上行麦克风帧、发下行 TTS 帧。
2. 一轮完整语音对话：`voice.start` → 上行帧 → ASR 临时/最终结果 → LLM → 分句 TTS →
   下行帧 → `voice.segment_played` → 回 IDLE。
3. 打断：设备 `voice.cancel(barge_in)` 后立即停 TTS/LLM、清空下行队列、回 `cancel_ack`、
   下发一次 `action.stop(barge_in)`、落库并标记 `interrupted`。
4. 半双工、旧帧过滤、5 分钟静默超时、单轮最大时长、资源清理。
5. 全程离线可测：Mock ASR/TTS + 模拟器；真实 DashScope 适配器用本地假服务器验证。
6. App 能通过 `voice.event` 看到 ASR 文本、TTS 分段、轮次结束与打断标记。

**非目标**

- 不实现 AEC 全双工；保持设备侧 `SPEAKING` 暂停采集。
- 不做逐字口型、摄像头、主动问候、4G。
- 不在服务端做音频存储、转码归档或语音克隆。
- 不在本轮做真实云/真机验收；只把适配器和验收步骤准备好。

## 3. 总体架构

```text
Device WS ──binary──> DeviceGateway._on_message()
                         │  AudioFrame.parse()
                         ▼
                    VoiceSession (每设备至多 1 轮)
                    ├── AsrStream: Ogg muxer → DashScope fun-asr-realtime
                    ├── ChatBridge: ChatOrchestrator.stream_chat()
                    ├── SentenceSegmenter: 流式分句
                    ├── TtsStream: DashScope TTS SSE → Ogg demuxer
                    └── DownlinkSender: 12B 帧 + 1x 节流 → DeviceSession.send_audio()
                         │
                         └── voice.event ──> EventBus ──> AppGateway ──> App WS
```

- **每个 `DeviceSession` 最多一个 `VoiceSession`**；`voice.start` 创建，轮次结束/取消/断线/停机销毁。
- `VoiceSession` 不直接依赖具体 provider：通过 `AsrProvider` / `TtsProvider` 协议注入；
  生产用 DashScope，测试用 Mock。
- `VoiceSession` 不直接依赖 `DeviceSession` 类：通过 `AudioSink` 协议（`send_control` / `send_audio`）
  下发，避免循环导入。
- 音频数据只在内存中有界缓冲；日志只记录 `kind/seq/turn_seq/len(payload)`，不记录 payload。

## 4. 协议补齐（`companion/docs/protocol_v2.md`）

### 4.1 上行（设备 → 服务端）

| type | 字段 | 说明 |
| :--- | :--- | :--- |
| `voice.start` | `turn_id` | 用户短按进入 LISTENING；`turn_id` 由设备生成（UUID），服务端校验并复用。 |
| `voice.end` | `turn_id` | 设备 VAD 判定用户说完、进入 THINKING；服务端结束 ASR 输入并等待最终结果。 |
| `voice.cancel` | `turn_id, reason` | `reason ∈ {barge_in, user_stop}`；SPEAKING 中短按为 `barge_in`，其它中止为 `user_stop`。 |
| `voice.segment_played` | `turn_id, segment_seq` | 设备播完某段 TTS，服务端据此推进轮次与 App 事件。 |

示例：

```json
{"v": 2, "type": "voice.start", "turn_id": "uuid", "ts": 1764000000000}
{"v": 2, "type": "voice.end", "turn_id": "uuid", "ts": 1764000000000}
{"v": 2, "type": "voice.cancel", "turn_id": "uuid", "reason": "barge_in", "ts": 1764000000000}
{"v": 2, "type": "voice.segment_played", "turn_id": "uuid", "segment_seq": 2, "ts": 1764000000000}
```

规则：

- 仅 `protocol_version >= 2` 接受；否则回一次 `voice.error(code="unsupported_protocol", fatal=true)`，
  之后忽略该连接的语音消息。
- 同一设备同时只允许一轮。同一 `turn_id` 的重复 `voice.start` 是幂等重传（不返回 `busy`、
  不清空已收帧）；不同 `turn_id` 的 `voice.start` 回 `voice.error(code="busy", fatal=false)`，
  原轮次不变。轮次不匹配的 `voice.end`/`cancel`/`segment_played` 回
  `voice.error(code="invalid_turn", fatal=false)`；属于刚结束/取消的上一轮的迟到
  `voice.end`/`segment_played` 静默忽略。迟到 `voice.cancel` 例外：ack 丢失时设备
  会用同一 `turn_id` 重试，服务端幂等重放 `cancel_ack`。
- `voice.start` 之前的二进制上行帧丢弃（设备此时麦克风应关闭）。
- `voice.end` 后到达的上行帧丢弃；`voice.cancel` 后到达的上行帧丢弃。
- 设备重复 `voice.segment_played` 幂等：同一 `(turn_id, segment_seq)` 只处理一次。

### 4.2 下行（服务端 → 设备）

| type | 字段 | 说明 |
| :--- | :--- | :--- |
| `voice.segment` | `turn_id, segment_seq, text, last` | 在一段 TTS 的第一帧之前发送；`text` 供静音时屏幕显示；`last=true` 表示本轮最后一段。 |
| `voice.cancel_ack` | `turn_id, reason` | 已停止本轮 LLM/TTS、清空下行队列。 |
| `voice.error` | `turn_id, code, message, fatal` | `code ∈ {busy, unbound, no_speech, asr_unavailable, tts_unavailable, idle_timeout, invalid_turn, invalid_frame, unsupported_protocol, internal_error}`。`fatal=true` 时设备回 IDLE；`fatal=false` 只否定触发它的这条消息，设备保持当前状态。 |

- `fatal=true`：`unbound`、`no_speech`、`asr_unavailable`、`tts_unavailable`、`idle_timeout`、
  `unsupported_protocol`、`internal_error`，以及对话锁被 App 生成占用时的 `busy`。
- `fatal=false`：`invalid_turn`、`invalid_frame`，以及同一设备已有活跃语音轮次时拒绝另一个
  `voice.start` 的 `busy`。
- `voice.segment` 与后续帧在同一个 `DeviceSession._send_lock` 内按序发送（`send_segment`），
  避免“段文本晚于音频”；每轮只有一个下行发送器。
- `action.stop` 仍按 §4.6；barge-in 只发一次，`reason="barge_in"`。
- 下行帧的 `flags.bit0` 仍表示**本轮最后一帧**，不是每段最后一帧；
  段边界由 `voice.segment` 标识。

### 4.3 `voice.event`（服务端 → App）

在现有枚举上补充轮次事件；`device_id` 由网关统一附加：

| event | 额外字段 |
| :--- | :--- |
| `turn_start` | `turn_id, conversation_id` |
| `asr_partial` | `turn_id, text` |
| `asr_final` | `turn_id, text` |
| `tts_segment_start` | `turn_id, segment_seq, text` |
| `tts_segment_end` | `turn_id, segment_seq` |
| `turn_end` | `turn_id, interrupted, finish_reason` |

`finish_reason ∈ {stop, interrupted, error, timeout, playback_timeout}`。
`turn_end` 是 App 侧关闭“正在回复”状态的唯一依据；`interrupted=true` 时 App 显示打断标记。

### 4.4 状态机（服务端视角）

```text
IDLE --voice.start--> LISTENING
LISTENING --voice.end--> THINKING（ASR 收尾，最多等 300ms 最终结果）
THINKING --首段 TTS 入队--> SPEAKING
SPEAKING --segment_played(last)--> IDLE
SPEAKING --segment_played(非 last)--> SPEAKING
ANY --voice.cancel--> IDLE（落库 interrupted）
LISTENING --静默 5 分钟--> IDLE（voice.error idle_timeout）
ANY --单轮 > 60s--> IDLE（voice.error internal_error/timeout）
```

设备侧 §9 状态机不变；服务端只通过 `voice.segment`、下行帧、`voice.error` 驱动它。

## 5. 传输与编解码

### 5.1 帧

- 帧头严格按 §6.1：`0xA1`、`0x01`、kind、flags、seq、turn_seq、payload。
- `AudioFrame.parse()` 校验：长度 ≥12、magic/version、kind ∈ {1,2}、flags 保留位为 0、
  payload 非空（`bit1` 静音帧允许空）。
- `seq`：每方向每连接单调递增；服务端不因缺口断开，只计数并记录 debug。
- `turn_seq`：每个 `turn_id` 从 0 开始；服务端丢弃同一轮内 `turn_seq` 回退的帧。
- 下行 `seq` 由服务端分配；`turn_seq` 跨该轮所有段连续递增。
- `WS_MAX_MESSAGE_BYTES` 仍为应用层上限；超限帧由 `StarletteChannel` 走 4429。

### 5.2 纯 Python Ogg Opus（不新增原生依赖）

上行设备发的是**裸 Opus 包**，而 DashScope ASR 的 `format=opus` 要求 **Ogg 封装**；
因此服务端需要：

- `OggOpusMuxer`：OpusHead（1 声道、16kHz、pre_skip=312、mapping family 0）+ OpusTags +
  音频页；每页最多 50 个包，granule 按 48kHz 累加，CRC32 按 Ogg 规范计算。
- `OggOpusDemuxer`：增量解析 `OggS` 页、校验 CRC、跳过 OpusHead/OpusTags、
  处理 lacing 续包，输出裸 Opus 包；TTS 的 `format=opus` 流直接喂给它。
- `opus_packet_duration_ms()`：解析 TOC 得到包时长（20/60ms），用于 granule 与帧长校验。

不引入 `av`/`opuslib`：当前 venv 没有 pip，且原生库在 Debian/Windows 上都要额外安装。
纯 Python 只做容器与分帧，不做 Opus 编解码；真实解码在设备和 DashScope 侧。

**风险**：TTS `format=opus` 的实际容器格式未在无 Key 环境验证。适配器按 Ogg 实现，
并保留 `DASHSCOPE_TTS_CONTAINER=ogg|raw` 配置；`raw` 模式下把每个 SSE 音频块当作一个包。
真实烟测时若发现不是 Ogg，只改适配器，不改管线与协议。

### 5.3 帧长协商

- 上行走 hello 协商的 16kHz/60ms（或 20ms）。
- 下行 TTS 的 Opus 包时长由 provider 决定；hello_ack 的 `audio.downlink.frame_ms`
  以服务端实际配置为准（`VOICE_DOWNLINK_FRAME_MS`），与设备请求不同则带
  `warnings: ["downlink_frame_ms_adjusted"]`。设备必须按 hello_ack 的值播放。
- 服务端逐包校验时长；与协商值不一致时记录 warning 并按包内 TOC 时长节流，
  不重编码（不引入原生 Opus 编码器）。

## 6. VoiceSession 生命周期

```python
class VoiceSession:
    turn_id: str
    state: Literal["listening", "thinking", "speaking", "ending", "closed"]
    generation: int          # 每次 cancel/新轮递增，旧任务据此丢弃结果
    asr: AsrStream | None
    llm_task: asyncio.Task | None
    tts_task: asyncio.Task | None
    downlink: DownlinkSender | None
    segments: dict[int, SegmentState]
    last_activity: float
```

- `start()`：校验 ready/protocol/capabilities、创建 ASR、启动 idle/max-turn 计时、广播 `turn_start`。
- `feed(frame)`：仅 `listening` 且 `turn_seq` 合法时接受；写 ASR（经 muxer）。
- `end_user_speech()`：置 `thinking`，结束 ASR 输入，等待最终结果（最多 300ms）；
  空文本 → `voice.error(no_speech)`；否则启动 `_generate()`。
- `_generate()`：
  1. 解析设备绑定的会话（见 §8）；拿不到 → `voice.error(code="unbound")`。
  2. `ChatOrchestrator.stream_chat()` 产出 `content`，喂 `SentenceSegmenter`。
  3. 每句启动 TTS；TTS 包经 demuxer 进入 `DownlinkSender`；首段入队时置 `speaking`。
  4. 正常结束：等待最后一段 `segment_played`（上限 `VOICE_PLAYBACK_DONE_TIMEOUT_SECONDS`）；
     超时则广播 `turn_end(finish_reason="playback_timeout")` 并关闭本轮。
- `cancel(reason)`：
  1. `generation += 1`；取消 `llm_task` / `tts_task`；清空下行队列；关闭 ASR/TTS。
  2. 发 `voice.cancel_ack`；`barge_in` 时发一次 `action.stop(barge_in)`（见 §9）。
  3. 聊天生成器按 §8 落库并标记 `interrupted`；广播 `turn_end(interrupted=true)`。
  4. 置 IDLE，取消计时器。
- `close()`：幂等；断线/停机/新连接替换时调用，不发 `cancel_ack`（链路已不可写），
  但必须落库已生成的部分并释放任务。

**并发**：所有状态变更在 `VoiceSession._lock` 内；`feed` 只做入队/转发，不等待 TTS/LLM。
`generation` 用于丢弃取消后迟到的 ASR/TTS/LLM 结果，避免旧声音串入下一轮。

## 7. ASR / TTS 抽象

### 7.1 接口

```python
class AsrProvider(Protocol):
    async def open(self, turn: AsrTurnConfig) -> AsrStream: ...

class AsrStream(Protocol):
    async def send(self, ogg_bytes: bytes) -> None: ...
    async def finish(self) -> None: ...
    async def close(self) -> None: ...
    def events(self) -> AsyncIterator[AsrEvent]: ...   # partial/final/error

class TtsProvider(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...  # Ogg Opus 或 raw
```

### 7.2 Mock（测试默认）

- `MockAsrProvider`：按脚本在第 N 帧后发 partial、第 M 帧后发 final；可注入延迟/错误/超时。
  不解析 Opus，只统计收到的 Ogg 字节数；测试断言 Ogg 头与页数。
- `MockTtsProvider`：用 `OggOpusMuxer` 把合成 payload 包成 Ogg，按可配置块大小/延迟产出；
  可注入首包超时、HTTP/WS 错误、中途取消。
- 注入方式：`DeviceGateway(voice_runtime=...)`；默认 runtime 从 `settings` 构建真实 provider，
  测试显式传 Mock，避免全局状态串扰。

### 7.3 DashScope ASR（`fun-asr-realtime`）

- URL：`wss://{workspace}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`；
  未配置 workspace 时用 `wss://dashscope.aliyuncs.com/api-ws/v1/inference`。
- 握手：`run-task`（`task_group=audio`、`task=asr`、`function=recognition`、
  `model=fun-asr-realtime`、`parameters={format:"opus", sample_rate:16000,
  max_sentence_silence:800, language_hints:["zh"]}`），等 `task-started`（5s 超时）。
- 上行：Ogg 页二进制；`voice.end` 后发 `finish-task`，继续收 `result-generated` 直到
  `task-finished` 或 2s 超时。
- 解析：`payload.output.sentence.text`、`sentence_end`、`heartbeat`；忽略心跳文本。
- 错误：握手失败/任务失败 → `voice.error(asr_unavailable)`；运行中断 → 结束本轮并广播 `turn_end(error)`。
- 日志只记录事件类型、文本长度、耗时；不记录识别全文（隐私）。

### 7.4 DashScope TTS（`qwen-audio-3.0-tts-flash`）

- HTTP SSE：`POST {base}/api/v1/services/audio/tts/SpeechSynthesizer`，
  `X-DashScope-SSE: enable`，body：
  `{"model": ..., "input": {"text": ..., "voice": ..., "format": "opus",
  "sample_rate": 24000, "bit_rate": 32}}`。
- 解析 `data:` JSON：`output.type=sentence-synthesis` 的 `output.audio.data`（base64）→
  Ogg demuxer；`sentence-end`/`finish_reason=stop` 结束。首包超时 5s。
- 若返回 `output.audio.url`（非流式）则按错误处理，提示改用流式，不做二次下载。
- 取消：关闭 `httpx` stream；不保留音频。
- 音色：沿用 `DASHSCOPE_TTS_VOICE`；真实试听后再定男声，不在本轮改默认值。

## 8. 会话解析、聊天与落库

### 8.1 设备 → 会话

新增 `resolve_conversation_for_device(db, device)`：

1. `device.bound_conversation_id` 存在且会话未删除 → 用它。
2. 否则 `device.bound_assistant_id` 存在 → 取该角色最近更新的会话；没有则创建一个
   `ConversationModel`（标题“与 <设备名> 的语音对话”）。
3. 都没有 → `voice.error(code="unbound")`，不调用 LLM。

创建会话必须走现有模型字段（`assistant_id`、`title`、`created_at/updated_at`），
不修改 `devices` 表结构。

### 8.2 聊天生成与取消落库

把 `ChatOrchestrator.stream_chat()` 的落库段抽成 `_save_turn()`，并加取消保护：

- 正常结束：`_save_turn(interrupted=False)`，`finish_reason="stop"`。
- `asyncio.CancelledError` / `GeneratorExit`：用 `asyncio.shield()` 落库
  `interrupted=True`，`assistant_msg["interrupted"]=True`、`finishReason="interrupted"`，
  然后重新抛出；用一个 `saved` 标志保证只落一次。
- 用户节点始终落库；助手节点即使文本为空也落库（App 显示“已打断”）。
- 落库失败只记录异常，不让取消路径卡住；用 2s 超时保护。
- SSE 路径同样受益：客户端断开后不再丢这一轮。

语音轮次传入 `cancel_reason`（见 §9），并在 `turn_end` 中带 `finish_reason`。

### 8.3 会话锁

沿用 `get_conversation_lock()`。设备语音与 App 文字共享同一把锁：

- 锁被占用 → `voice.error(code="busy", fatal=true)`，设备回 IDLE；不排队、不打断 App 的生成。
- 语音轮次持有锁直到 LLM/TTS 结束或取消；取消时锁随生成器退出释放。

## 9. barge-in 与 `action.stop` 去重

现状：聊天生成器被取消时，`_DeviceScheduler.submit()` 的 `CancelledError` 路径会发
`action.stop(server_cancel)`。若语音管线再发一条 `action.stop(barge_in)`，设备会收到两条。

设计：

- `ActionTurnState` 增加 `cancel_reason: str = "server_cancel"`；`_Job` 保存该原因；
  `submit()` 的 `CancelledError` 路径用 `job.cancel_reason`。
- 语音轮次创建 `ActionTurnState(cancel_reason="barge_in")`；SSE 路径保持默认。
- 语音管线**不再单独发** `action.stop`；由被取消的聊天生成器发一次，reason 正确。
- 若取消时没有在途动作，则不发 `action.stop`；这与“只停止本轮动作、不做全局急停”的既有 Ruling 一致，
  也避免误停 App 同时进行的遥控动作。
- `voice.cancel_ack` 仍由语音管线立即发送，保证设备知道服务端已受理。

## 10. 下行节流、背压与半双工

- `DownlinkSender` 每轮一个任务：
  - 队列上限 `VOICE_DOWNLINK_BUFFER_SECONDS`（默认 5s）对应的帧数；
  - 首帧立即发，之后按包时长 1x 节流（`next_send_at += duration`）；
  - 队列满时生产者 `await`，不丢帧；取消时清空并退出。
- 每个 `voice.segment` 先于该段第一帧发送；`last` 段最后一帧置 `flags.bit0=1`。
- 设备处于 `SPEAKING` 时服务端丢弃上行帧（半双工防御）；`voice.cancel` 后同样丢弃，
  直到下一轮 `voice.start`。
- 发送失败（`DeviceSession.send_audio()` 返回 False / 链路 closing）：
  取消本轮、关闭 ASR/TTS、不广播错误（设备已离线由 offline 事件负责）。
- 旧帧过滤：下行 `generation` 递增；旧 TTS 任务产出的帧在入队前检查 generation，不匹配直接丢弃。

## 11. 超时与资源清理

| 名称 | 默认 | 行为 |
| :--- | :--- | :--- |
| `VOICE_ASR_CONNECT_TIMEOUT_SECONDS` | 5.0 | 握手失败 → `voice.error(asr_unavailable)` |
| `VOICE_ASR_FINAL_GRACE_SECONDS` | 0.3 | `voice.end` 后等最终结果；超时用已有 partial/final |
| `VOICE_TTS_FIRST_PACKET_TIMEOUT_SECONDS` | 5.0 | → `voice.error(tts_unavailable)` + `turn_end(error)` |
| `VOICE_SEGMENT_IDLE_TIMEOUT_SECONDS` | 15.0 | TTS 段之间无数据 → 结束本轮 |
| `VOICE_IDLE_TIMEOUT_SECONDS` | 300.0 | LISTENING 无任何上行/识别活动 → `voice.error(idle_timeout)` |
| `VOICE_MAX_TURN_SECONDS` | 60.0 | 单轮硬上限 → 取消 + `turn_end(timeout)` |
| `VOICE_PLAYBACK_DONE_TIMEOUT_SECONDS` | 10.0 | 最后一段发完但设备未回 `segment_played` → `turn_end(playback_timeout)` |
| `VOICE_DOWNLINK_BUFFER_SECONDS` | 5.0 | 下行队列上限 |

- 所有计时器在 `close()`/`cancel()` 中取消；`VoiceSession` 不注册全局任务。
- 设备断线/替换/撤销/停机：`DeviceGateway._teardown()`/`shutdown()` 先 `close()` 语音轮次，
  再关连接；离线事件与既有 `pending_offline_reason` 逻辑不变。
- 服务端启动/停止预算（10s/2s/2s/20s）不变；语音任务必须在 `close()` 中有界结束，
  不拖长停机。

## 12. 配置

在 `config.py` 增加（全部有默认值，缺 Key 时 voice 功能返回 `asr_unavailable`）：

```text
DASHSCOPE_WORKSPACE_ID: str = ""
DASHSCOPE_REGION: str = "cn-beijing"
DASHSCOPE_TTS_CONTAINER: str = "ogg"          # ogg | raw
VOICE_ENABLED: bool = True
VOICE_DOWNLINK_FRAME_MS: int = 60
VOICE_DOWNLINK_BUFFER_SECONDS: float = 5.0
VOICE_IDLE_TIMEOUT_SECONDS: float = 300.0
VOICE_MAX_TURN_SECONDS: float = 60.0
VOICE_ASR_FINAL_GRACE_SECONDS: float = 0.3
VOICE_PLAYBACK_DONE_TIMEOUT_SECONDS: float = 10.0
```

不新增真实密钥默认值；不把 Key 写入日志/数据库/文档。

## 13. 隐私与安全

- 原始音频只在内存的有界队列/缓冲区中存在；不落盘、不入库、不写日志。
- 日志字段白名单：`device_id`、`turn_id`、`kind`、`seq`、`turn_seq`、`len(payload)`、
  provider 事件类型、耗时、错误码。**不记录 ASR 全文/TTS 文本**（App 事件除外）。
- 错误信息若来自 provider，先脱敏（去掉 `Authorization`、URL query、Key 片段）。
- 设备语音消息只在 `ready` 且 `protocol_version >= 2` 时受理；未鉴权链路在握手层已被拒。
- 不新增任何 TLS 跳过选项。

## 14. 实施拆块与验收

### 子块 A：协议 + 帧 + Ogg + 模拟器

- 新增 `app/audio/framing.py`、`app/audio/ogg.py`；`WSChannel.send_bytes`；
  `DeviceSession.send_audio()`；`_on_message` 二进制分派；`VoiceSession` 骨架（只做帧路由）。
- 协议文档补 §4/§5/§6 的新消息与状态机；`hello_ack` 的帧长调整 warning。
- 模拟器：发送/接收二进制帧、按脚本产生上行帧、记录下行帧、回 `segment_played`。
- 验收：单元测试（帧/容器/路由/顺序/关闭）+ 模拟器帧往返；不接 ASR/TTS。

### 子块 B：Mock 管线 + 聊天 + 打断 + 落库

- `SentenceSegmenter`、`MockAsr/MockTts`、`VoiceSession` 完整状态机、
  `DownlinkSender`、`voice.event`、会话解析、取消落库、barge-in 去重、5 分钟/单轮超时。
- 验收：模拟器 e2e 覆盖正常轮次、打断、半双工、busy、idle_timeout、播放完成超时、
  断线清理、无音频泄漏断言。

### 子块 C：真实 DashScope 适配器

- `DashScopeAsr`、`DashScopeTts`、URL/配置/错误映射；本地假 WS/SSE 服务器测试。
- 验收：本地假服务器全部通过；真实云烟测脚本与步骤写入文档，标记“待 Key/真机”。
  不伪造真实云通过。

每个子块完成后的固定动作：全量 `pytest` → mutation（关键不变量）→ 写迁移日志（24/25/26）
→ 独立子智能体审查 → 修 → 提交推送。

## 15. 测试矩阵（汇总）

| 层级 | 覆盖 |
| :--- | :--- |
| 帧 | magic/version/kind/flags/长度/seq 回退/空 payload/超限 |
| Ogg | OpusHead/Tags、CRC、lacing 续包、分段输入、CRC 错误、granule、包时长 |
| 路由 | 二进制不进 JSON 分支、无语音轮次时丢弃、JSON 与二进制发送顺序、关闭取消 |
| 管线 | start→end→LLM→分段 TTS→segment_played→turn_end；首包延迟；空文本；busy |
| 打断 | 清空队列、cancel_ack、只发一次 action.stop(barge_in)、落库 interrupted、旧帧丢弃 |
| 超时 | ASR 连接/最终结果、TTS 首包、段间、5 分钟、单轮、播放完成 |
| 隐私 | caplog/DB 断言无 payload、无 Key、无 ASR/TTS 全文 |
| e2e | 模拟器真实 WS 帧往返、半双工、断线、停机、重连 |
| 真实适配器 | 假 WS/SSE 服务器；真实云标记待验收 |

## 16. 风险与待定

1. **TTS Opus 容器未知**：无 Key 无法确认是 Ogg 还是裸包；适配器留 `DASHSCOPE_TTS_CONTAINER`
   开关，真实烟测后只改适配器。
2. **Opus 包时长**：TTS 可能输出 20ms 包；服务端按 TOC 节流并在 hello_ack 修正 `frame_ms`，
   不重编码；真机需验证抖动缓冲。
3. **ASR VAD 语义**：`fun-asr-realtime` 的 `sentence_end` 与设备 `voice.end` 可能不同步；
   以设备 `voice.end` 为准，ASR 只提供文本。
4. **barge-in 与 App 遥控**：`action.stop` 只在本轮有在途动作时发；若 App 同时在遥控，
   可能被本轮 stop 影响。这是协议 §6.2 的既有语义，本轮不扩大到全局急停。
5. **App 未实现**：`voice.event` 的字段先按本文档定稿，Android 端后续对齐。
6. **真实云/真机未验收**：本机无 Key、无硬件；所有“真实”结论标记为待办，不写成已通过。

## 17. 预计改动文件

| 路径 | 说明 |
| :--- | :--- |
| `companion/docs/protocol_v2.md` | 新增 voice.* 消息、事件、状态机、帧长/容器说明 |
| `companion/server/app/config.py` | 语音配置项 |
| `companion/server/app/audio/__init__.py` | 包入口 |
| `companion/server/app/audio/framing.py` | 12 字节帧解析/编码 |
| `companion/server/app/audio/ogg.py` | Ogg Opus mux/demux + TOC 时长 |
| `companion/server/app/audio/providers.py` | Asr/Tts 协议 + Mock |
| `companion/server/app/audio/dashscope.py` | DashScope ASR WS / TTS SSE |
| `companion/server/app/audio/sentences.py` | 流式分句 |
| `companion/server/app/audio/pipeline.py` | VoiceSession / DownlinkSender / voice.event |
| `companion/server/app/services/device_gateway.py` | send_bytes、二进制分派、生命周期接入 |
| `companion/server/app/services/orchestrator.py` | 取消落库、会话解析、cancel_reason |
| `companion/server/app/services/action_orchestrator.py` | `ActionTurnState.cancel_reason` 透传 |
| `companion/server/app/routers/ws.py` | `StarletteChannel.send_bytes` |
| `companion/simulators/device_simulator.py` | 二进制帧、voice.*、segment_played |
| `companion/tests/...` | 单元/集成/e2e/mutation |
| `docs/语音机器人需求文档/v2_migration_log/24..26_*.md` | 子块过程记录 |

## 18. 需要批准的设计决策

1. **新增 `voice.end`**（设备 VAD 说完）——没有它，服务端无法可靠区分“ASR 断句”与“用户说完了”。
2. **新增 `voice.segment` / `voice.cancel_ack` / `voice.error`**——段边界、打断受理与统一错误。
3. **`voice.segment_played` 以 `segment_seq` 为准**，重复幂等；最后一段决定 `turn_end`。
4. **barge-in 只发一次 `action.stop(barge_in)`**，通过 `ActionTurnState.cancel_reason` 透传，
   不再由语音管线另发。
5. **取消时也落库**，助手消息带 `interrupted=true`；SSE 与语音共用。
6. **纯 Python Ogg**，不新增原生依赖；TTS 容器留配置开关。
7. **无 Key/无真机**：真实适配器只做本地假服务器验证，真实云/真机验收明确待办。
