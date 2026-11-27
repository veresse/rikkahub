# Companion 音频 Block B（Mock 管线 / 聊天 / 打断 / 落库）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一轮语音对话在**完全不依赖云 Key、不依赖真机**的前提下跑通：`voice.start` → 上行帧 → Mock ASR 最终文本 → Mock LLM → 流式分句 → Mock TTS → 1x 节流下行 → `voice.segment_played` → `turn_end`；同时实现打断、半双工、5 分钟静默、单轮上限、播放完成超时、取消落库与 barge-in `action.stop` 去重。

**Architecture:** Block A 的 `VoiceSession` 只做帧门控；本块在同一对象上增加可选的 `VoiceRuntime`（ASR/TTS provider、事件总线、DB 会话工厂、聊天生成器）。没有 runtime 时保持 A 的同步语义（现有 21 例传输测试不变）；有 runtime 时由 `begin()` 启动 ASR、`end_user_speech()` 触发聊天与 TTS。下行统一经 `DownlinkSender`（每轮一个任务、`voice.segment` 先于音频、按包时长 1x 节流）。取消时递增 `generation`，旧 ASR/TTS/LLM 结果按代次丢弃；聊天生成器负责 `interrupted` 落库，`ActionTurnState.cancel_reason` 负责只发一次 `action.stop(barge_in)`。

**Tech Stack:** Python 3.11+、asyncio、FastAPI/Starlette、SQLAlchemy async、pytest（`asyncio_mode=auto`）；无新增第三方依赖。

**Spec:** `docs/语音机器人需求文档/v2_migration_log/23_audio_block_design.md`（§6 生命周期、§7 provider、§8 会话与落库、§9 barge-in、§10 节流、§11 超时、§14 子块 B）

**Depends on:** Block A（提交 `6d9154c1`）：`audio/framing.py`、`audio/ogg.py`、`audio/pipeline.py` 的 `VoiceSession` 骨架、`DeviceSession.send_audio()`、模拟器二进制帧。

## Global Constraints

- 原始音频只在内存有界队列中；不落盘、不入库、不打印 payload、不记录 ASR/TTS 全文（`voice.event` 例外，`text` 只进事件总线）。
- 所有测试离线可跑：Mock ASR/TTS + 脚本化 LLM；`pytest` 从仓库根运行。
- 测试解释器：`C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest`。
- 不破坏 Block A 的 21 例传输测试与现有 278 例全量基线；不读取/提交根 `local.properties`。
- 半双工：`THINKING/SPEAKING` 期间上行帧丢弃；`voice.end/cancel` 后到下一轮 `voice.start` 前丢弃。
- `turn_seq` 下行跨该轮所有段连续递增；`flags.bit0` 只在本轮最后一帧置位。
- 普通动作 ≤3000ms、`dance` ≤5000ms、`tilt ∈ [-20,45]`、`pan ∈ [-90,90]` 不变。
- 新增依赖：无。

## Review Focus

1. 取消后旧 ASR/TTS/LLM 结果**不得**串入下一轮（`generation` 检查覆盖每一处入队/事件/落库）。
2. barge-in 只发一次 `action.stop(barge_in)`，且不影响 App 同时遥控的动作。
3. `voice.segment` 必须先于该段第一帧音频；`last` 只标记在本轮最后一帧。
4. 取消/断线/停机时聊天部分回复必须落库并带 `interrupted=true`，且不能无限期阻塞关闭。
5. 下行队列有界；设备写失败/离线时本轮有界结束，不重试到下一轮。
6. 5 分钟静默、单轮 60s、TTS 首包 5s、播放完成 10s 都必须真正触发并有测试，不能只有常量。

---

### Task 1: 流式分句器

**Files:**
- Create: `companion/server/app/audio/sentences.py`
- Test: `companion/server/tests/test_audio_sentences.py`

**Interfaces:**
- Produces:
  - `class SentenceSegmenter(min_chars: int = 4, max_chars: int = 120)`
    - `feed(delta: str) -> list[str]`：返回已经可以送去 TTS 的完整句子（保留标点）。
    - `flush() -> list[str]`：返回剩余文本（去掉首尾空白后非空才返回）。

- [x] **Step 1: 写失败测试**

覆盖：`。！？!?；;\n` 分句；不拆英文缩写（`Mr.` 这类至少不产生 1 字符段）；`min_chars` 合并短句；`max_chars` 硬切；跨 chunk 分句；`flush()` 只返回一次；空输入返回空列表；纯标点不产出。

- [x] **Step 2: 运行确认失败**

Run: `... -m pytest companion/server/tests/test_audio_sentences.py -q`
Expected: FAIL（模块不存在）。

- [x] **Step 3: 实现**

逐字符扫描缓冲；句末标点后若缓冲长度 ≥ `min_chars` 则切出；缓冲超过 `max_chars` 时在最近的逗号/空格切，找不到就硬切；不丢弃字符。`flush()` 清空缓冲。

- [x] **Step 4: 运行确认通过并提交**

Run: `... -m pytest companion/server/tests/test_audio_sentences.py -q`
Expected: PASS。

```bash
git add companion/server/app/audio/sentences.py companion/server/tests/test_audio_sentences.py
git commit -m "feat(companion): add the streaming sentence segmenter"
```

---

### Task 2: ASR / TTS 抽象与 Mock provider

**Files:**
- Create: `companion/server/app/audio/providers.py`
- Test: `companion/server/tests/test_audio_providers.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class AsrEvent`：`kind: str`（`partial|final|error`）、`text: str = ""`、`code: str = ""`。
  - `class AsrStream(Protocol)`：`send(ogg_bytes) / finish() / close() / events() -> AsyncIterator[AsrEvent]`。
  - `class AsrProvider(Protocol)`：`open(*, device_id, turn_id, sample_rate=16000) -> AsrStream`。
  - `class TtsProvider(Protocol)`：`synthesize(text, *, turn_id, segment_seq) -> AsyncIterator[bytes]`。
  - `class MockAsrProvider(script)`：脚本项 `{"after_pages": int, "partial": str}`、`{"final": str}`、`{"final_delay": float}`、`{"error": "asr_unavailable"}`；默认 `finish()` 后立即 final；提供 `received_bytes` 供断言。
  - `class MockTtsProvider(packets_per_segment=4, delay=0.0, fail_at=None, error="tts_unavailable")`：用 `OggOpusMuxer` 产出 Ogg Opus；`fail_at` 指定第几个 segment 抛错；`delay` 模拟首包/段间延迟；提供 `synthesized: list[str]`。
  - `class UnavailableAsrProvider(UnavailableTtsProvider)`：`open()` 立即产出 `error`；供未配置真实 provider 时使用。

- [x] **Step 1: 写失败测试**

覆盖：Mock ASR 收到 Ogg 页后按页数发 partial、`finish()` 发 final、error 路径、`received_bytes` 增长；Mock TTS 输出可被 `OggOpusDemuxer` 还原、`packets_per_segment` 数量正确、`fail_at` 抛错、取消后停止产出；Unavailable 立即 error。

- [x] **Step 2: 运行确认失败**

Run: `... -m pytest companion/server/tests/test_audio_providers.py -q`
Expected: FAIL。

- [x] **Step 3: 实现并确认通过**

Mock 只用 `framing`/`ogg` 的公开接口；不得记录文本到日志。`MockTtsProvider` 的假 Opus 包用固定 20ms TOC（`b"\x78" + b"\x00" * 9`），保证 `opus_packet_duration_ms` 可解析。

Run: `... -m pytest companion/server/tests/test_audio_providers.py -q`
Expected: PASS。

- [x] **Step 4: 提交**

```bash
git add companion/server/app/audio/providers.py companion/server/tests/test_audio_providers.py
git commit -m "feat(companion): add mock ASR and TTS providers"
```

---

### Task 3: 下行发送器（顺序 / 节流 / 有界队列）

**Files:**
- Create: `companion/server/app/audio/downlink.py`
- Test: `companion/server/tests/test_audio_downlink.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class SegmentStart`：`segment_seq: int, text: str, last: bool`。
  - `@dataclass(frozen=True) class AudioPacket`：`payload: bytes, duration_ms: float, last_in_turn: bool = False`。
  - `class DownlinkClosed(RuntimeError)`：sink 写失败。
  - `class DownlinkSender(sink, *, buffer_seconds=5.0, frame_ms=60, generation=0)`
    - `start() -> None`；`enqueue(item, *, generation) -> bool`；`stop() -> None`
    - `wait_idle(timeout=None) -> bool`：队列空且当前包已发完。
    - `last_sent_segment: int | None`；`sent_frames: int`。
  - `Sink` 协议：`send_control(payload)`、`send_segment(segment, first_payload, *, turn_seq, flags=0)`、`send_audio(payload, *, turn_seq, flags=0)`。
  - `SegmentStart` 必须与该段第一帧通过 `send_segment()` 原子发送（Block A 已提供，锁内先 JSON 后 bytes）。

- [x] **Step 1: 写失败测试**

用 `RecordingSink` 记录 `control` 与 `audio` 调用顺序。覆盖：
1. `SegmentStart` 一定先于该段 `AudioPacket`。
2. `turn_seq` 从 0 连续递增，跨段不重置。
3. `last_in_turn=True` 的包带 `FLAG_LAST`，其它不带。
4. 队列满时生产者阻塞，消费后继续（用小 buffer 与慢 sink）。
5. `generation` 不匹配的项不入队。
6. sink 返回 False → worker 抛 `DownlinkClosed` 并停止；`stop()` 后 `enqueue` 返回 False。
7. 节流：两包 20ms，发送间隔 ≥ 10ms（留余量，避免 CI 抖动）。

- [x] **Step 2: 运行确认失败 → 实现 → 通过**

Run: `... -m pytest companion/server/tests/test_audio_downlink.py -q`

实现要点：单 worker 任务；`asyncio.Queue(maxsize=frame_capacity)`；`_last_audio_at`/`next_send_at` 节流；`stop()` 取消 worker 并清空队列，阻塞中的 `enqueue` 立即返回 False（用关闭事件 + `put` 超时或队列替换）；所有 await 都有取消保护。

- [x] **Step 3: 提交**

```bash
git add companion/server/app/audio/downlink.py companion/server/tests/test_audio_downlink.py
git commit -m "feat(companion): add the throttled downlink sender"
```

---

### Task 4: VoiceSession 完整管线（正常轮次 + 超时 + 半双工）

**Files:**
- Modify: `companion/server/app/audio/pipeline.py`
- Create: `companion/server/app/audio/runtime.py`
- Test: `companion/server/tests/test_audio_pipeline.py`

**Interfaces:**
- Produces:
  - `@dataclass class VoiceConfig`：`idle_timeout=300.0`、`max_turn=60.0`、`asr_final_grace=0.3`、`tts_first_packet_timeout=5.0`、`segment_idle_timeout=15.0`、`playback_done_timeout=10.0`、`downlink_buffer_seconds=5.0`、`frame_ms=60`、`tts_container="ogg"`、`sample_rate=16000`。
  - `class VoiceRuntime`：`bus`、`asr`、`tts`、`session_factory`、`resolve_conversation`、`chat_factory`、`config`。
  - `VoiceSession(device_id, *, on_frame=None, runtime=None, config=None)` 新增：
    - `async begin() -> None`（解析会话/锁忙检查/打开 ASR/发 `turn_start`/启动 idle 与 max-turn 计时）
    - `async accept_frame(frame)`：A 的门控 + 写入 ASR（Ogg muxer）
    - `end_user_speech()`：置 `thinking` 并调度 `_finish_turn()`
    - `cancel_turn(reason)`：同步置 IDLE + 递增 generation，后台有界清理
    - `segment_played(seq)`：A 的幂等 + 末段触发 `_complete_turn()`
    - `async close()`：有界关停
  - `VoiceSession._emit(event, **fields)`：向 `bus` 发 `voice.event`（`device_id/turn_id/ts` 自动附加）。
  - `VoiceSession.conversation_id`、`last_finish_reason`。

- [x] **Step 1: 写失败测试（Mock runtime）**

复用 `VoiceRuntime` + `RecordingSink`（实现 `send_control/send_audio/get_status`）。覆盖：
1. `begin()` 未绑定 → `voice.error(unbound, fatal=true)` + `turn_end(error)` + IDLE。
2. 会话锁被占用 → `voice.error(busy, fatal=true)`。
3. 正常轮次：start → 3 帧 → end → ASR final → LLM 两段文本 → `voice.segment`×2 → 下行帧 → 模拟 `segment_played(0)`/`(1)` → `turn_end(stop, interrupted=False)`；`voice.event` 顺序含 `turn_start/asr_final/tts_segment_start/tts_segment_end/turn_end`。
4. 空 ASR final → `voice.error(no_speech)`，不调用 LLM。
5. ASR 立即 error → `voice.error(asr_unavailable)` + `turn_end(error)`。
6. TTS 首包超时（`asr` 正常、tts delay > timeout）→ `voice.error(tts_unavailable)`。
7. 播放完成超时：最后一段发出后不回 `segment_played` → `turn_end(playback_timeout)`（把 `playback_done_timeout` 调到 0.05）。
8. 静默超时：`idle_timeout=0.05`，不喂帧 → `voice.error(idle_timeout)` + `turn_end(timeout)`。
9. 单轮上限：`max_turn=0.05`，持续喂帧 → `turn_end(timeout)` 且 generation 递增。
10. 半双工：`thinking/speaking` 期间帧被丢弃，ASR 字节数不增长。
11. 没有 runtime 的 `VoiceSession` 仍保持 A 语义（现有 21 例必须全绿）。
12. 事件里的 `text` 不进入日志（caplog 断言无句子正文）。

- [x] **Step 2: 运行确认失败**

Run: `... -m pytest companion/server/tests/test_audio_pipeline.py companion/server/tests/test_voice_transport.py -q`
Expected: 新文件 FAIL，旧 21 例 PASS。

- [x] **Step 3: 实现**

要点：
- `begin()` 由网关 `create_task` 调用；ASR 未就绪时上行帧进入**有界**待发列表（上限按 5s 帧数，丢最旧），`asr` 就绪后按序 flush；`generation` 变化后丢弃。
- `_finish_turn()`：`asr.finish()` → 等 final（`asr_final_grace`）→ 无文本 `no_speech`；有文本发 `asr_final` → `_generate()`。
- `_generate()`：`async with session_factory() as db` 调 `chat_factory`；`ActionTurnState(cancel_reason="barge_in")`；content 增量喂分句器；句子进 TTS 队列（保留一句，确保 `last=True` 落在最后一段/最后一帧）。
- `_speaker()`：逐段调 `tts.synthesize`，首包 `wait_for(tts_first_packet_timeout)`，后续 `wait_for(segment_idle_timeout)`；Ogg 容器走 demuxer，`raw` 每个 chunk 当一包；包时长用 `opus_packet_duration_ms` 回退 `frame_ms`；`voice.segment` 与 `AudioPacket` 入 `DownlinkSender`。
- `segment_played(seq)`：非末段只发 `tts_segment_end`；末段且 state==speaking → `_complete_turn("stop")`。
- `_complete_turn(reason)`：发 `turn_end`，置 IDLE、`turn_id=None`，停 `DownlinkSender`，关 ASR/TTS，取消计时器。
- 计时器任务统一登记在 `self._timers`，每个等待后先校验 `generation` 与 `turn_id`。
- `_emit` 只允许白名单字段；不记录 `text` 到 logger。

- [x] **Step 4: 运行确认通过**

Run: `... -m pytest companion/server/tests/test_audio_pipeline.py companion/server/tests/test_voice_transport.py -q`
Expected: 全 PASS。

- [x] **Step 5: 提交**

```bash
git add companion/server/app/audio/pipeline.py companion/server/app/audio/runtime.py companion/server/tests/test_audio_pipeline.py
git commit -m "feat(companion): implement the mock voice pipeline"
```

---

### Task 5: 取消落库与 barge-in `action.stop` 去重

**Files:**
- Modify: `companion/server/app/services/orchestrator.py`
- Modify: `companion/server/app/services/action_orchestrator.py`
- Test: `companion/server/tests/test_chat_cancel.py`
- Test: `companion/server/tests/test_action_orchestrator.py`（新增 cancel_reason 用例）

**Interfaces:**
- Produces:
  - `ChatOrchestrator.stream_chat(..., cancel_reason: str = "server_cancel", client=None)`。
  - 取消/`GeneratorExit` 时 `_save_turn(db, ..., interrupted=True, finish_reason="interrupted")`，用 `asyncio.shield` + 2s 上限，只落一次（`saved` 标志）。
  - 助手消息 `assistant_msg["interrupted"] = True`；`done` chunk 之外的 `turn_end` 由语音管线负责。
  - `ActionTurnState.cancel_reason: str = "server_cancel"`；`_Job.cancel_reason`；`_DeviceScheduler.submit(..., cancel_reason=...)` 的取消/超时路径用 `job.cancel_reason` 发 `action.stop`。
  - `ActionOrchestrator.execute_tool` 把 `turn.cancel_reason` 传给 `_submit`。

- [x] **Step 1: 写失败测试**

1. `test_chat_cancel.py`：脚本化 client 产出 1 段 content 后挂起；取消消费任务；断言 DB 有用户节点与助手节点、`messages[0]["interrupted"] is True`、`tokenUsage` 缺失也不报错；会话 `updated_at` 更新；再取消一次不重复落库。
2. 正常结束不写 `interrupted`。
3. `cancel_reason="barge_in"`：在 `submit()` 等待回执期间取消 → 设备收到 `action.stop` 且 `reason == "barge_in"`；默认路径仍为 `server_cancel`；无在途动作时不发 `action.stop`。
4. 落库抛异常时取消路径仍返回（用 monkeypatch 让 commit 抛错，断言不挂起）。

- [x] **Step 2: 运行确认失败 → 实现 → 通过**

Run: `... -m pytest companion/server/tests/test_chat_cancel.py companion/server/tests/test_action_orchestrator.py -q`

实现要点：把现有落库段抽成 `_save_turn()`（正常/取消共用）；取消分支放在 `try/except BaseException` 外层，确保 `CancelledError` 先落库再抛出；`stream_chat` 的 `lock.locked()` 分支不变（语音 busy 由 pipeline 预检）。

- [x] **Step 3: 提交**

```bash
git add companion/server/app/services/orchestrator.py companion/server/app/services/action_orchestrator.py companion/server/tests/test_chat_cancel.py companion/server/tests/test_action_orchestrator.py
git commit -m "feat(companion): persist interrupted turns and pass the cancel reason"
```

---

### Task 6: 网关接线、会话绑定、配置与关闭集成

**Files:**
- Create: `companion/server/app/audio/binding.py`
- Modify: `companion/server/app/audio/runtime.py`
- Modify: `companion/server/app/services/device_gateway.py`
- Modify: `companion/server/app/routers/ws.py`
- Modify: `companion/server/app/config.py`
- Test: `companion/server/tests/test_audio_binding.py`
- Test: `companion/server/tests/test_voice_transport.py`（新增 runtime 接入用例）
- Test: `companion/server/tests/test_device_gateway.py`（关闭清理）

**Interfaces:**
- Produces:
  - `async def resolve_conversation_for_device(db, device) -> str | None`：会话级 → 角色最近会话 → 新建“与 <设备名> 的语音对话”；都没有返回 None。
  - `def build_voice_runtime(...) -> VoiceRuntime | None`：`VOICE_ENABLED` 关闭时 None；`VOICE_PROVIDER=mock|unavailable`（`dashscope` 留给 Block C）。
  - `DeviceSession.send_control(payload) -> bool`（`_safe_send` 的公开别名）。
  - `DeviceGateway(voice_runtime=...)` / `set_voice_runtime(runtime)`。
  - 配置：`VOICE_ENABLED=True`、`VOICE_PROVIDER="unavailable"`、`VOICE_IDLE_TIMEOUT_SECONDS=300`、`VOICE_MAX_TURN_SECONDS=60`、`VOICE_ASR_FINAL_GRACE_SECONDS=0.3`、`VOICE_TTS_FIRST_PACKET_TIMEOUT_SECONDS=5`、`VOICE_SEGMENT_IDLE_TIMEOUT_SECONDS=15`、`VOICE_PLAYBACK_DONE_TIMEOUT_SECONDS=10`、`VOICE_DOWNLINK_BUFFER_SECONDS=5`、`DASHSCOPE_TTS_CONTAINER="ogg"`。

- [x] **Step 1: 写失败测试**

1. binding：会话级优先；角色级取最近更新；无会话时新建且标题含设备名；都没有返回 None；撤销设备不回退。
2. gateway：`voice.start` 后 `begin()` 失败（unbound/busy）会发 `voice.error` 并回 IDLE；正常 start 建 runtime 会话；`voice.cancel` 发 ack 且 generation 递增；`close()`/替换连接会取消语音任务且不泄漏（`asyncio.all_tasks()` 不增长）。
3. `build_voice_runtime(VOICE_ENABLED=False) is None`；`VOICE_PROVIDER=unavailable` 时 `voice.start` 收到 `asr_unavailable`。
4. `send_control` 与 `send_audio` 共用发送锁（顺序断言）。

- [x] **Step 2: 运行确认失败 → 实现 → 通过**

Run: `... -m pytest companion/server/tests/test_audio_binding.py companion/server/tests/test_voice_transport.py companion/server/tests/test_device_gateway.py -q`

实现要点：`voice.start` 只做同步 `start()`，随后 `create_task(voice.begin())`，不阻塞读循环；`voice.end` 调 `end_user_speech()`；`voice.cancel` 调 `cancel_turn(reason)` 后发 ack；`voice.segment_played` 调同步 `segment_played()`。`close_voice()` 改为 `await voice.close()`。`routers/ws.py` 在模块初始化时 `build_voice_runtime()` 并注入。

- [x] **Step 3: 提交**

```bash
git add companion/server/app/audio/binding.py companion/server/app/audio/runtime.py companion/server/app/services/device_gateway.py companion/server/app/routers/ws.py companion/server/app/config.py companion/server/tests/test_audio_binding.py companion/server/tests/test_voice_transport.py companion/server/tests/test_device_gateway.py
git commit -m "feat(companion): wire the voice runtime into the device gateway"
```

---

### Task 7: 模拟器自动回执、真实 e2e 与协议文档

**Files:**
- Modify: `companion/simulators/device_simulator.py`
- Modify: `companion/tests/test_phase3_e2e.py`
- Modify: `companion/docs/protocol_v2.md`（只在 B 新增/澄清的字段，如 App `voice.event` 来源）
- Test: `companion/tests/test_protocol_v2_doc.py`

**Interfaces:**
- Produces:
  - `SimulatedDevice.auto_segment_played: bool = False`：收到 `voice.segment` 且 `last` 包播完后自动回 `voice.segment_played`。
  - `SimulatedDevice.audio_turn_id` / `last_segment_seq`：按 `voice.segment` 记录当前轮。
  - e2e 辅助 `install_mock_voice(...)`：向 `ws.device_gateway` 注入带 Mock provider 的 runtime（测试进程内）。

- [x] **Step 1: 写 e2e 失败用例**

新增 5 例（真实 TCP/WS）：
1. 正常轮次：绑定会话 → `voice.start` → 2 帧 → `voice.end` → 收到 `voice.segment`/下行帧 → 自动 `segment_played` → App 收到 `turn_end(finish_reason=stop)`，DB 有本轮消息。
2. barge-in：`SPEAKING` 时 `voice.cancel(barge_in)` → `cancel_ack` + `turn_end(interrupted=True)` + 设备收到一次 `action.stop(reason="barge_in")`（本轮有 `action.play` 在途时）+ DB 助手消息 `interrupted=true`。
3. busy：会话锁被占用（App 正在生成）时 `voice.start` → `voice.error(busy)`。
4. 未绑定：`voice.start` → `voice.error(unbound)`，LLM 未被调用。
5. 断线清理：轮次进行中断开设备 WS → 服务端语音任务结束、DB 落库 `interrupted`、App 收到 offline。

- [x] **Step 2: 运行确认失败**

Run: `... -m pytest companion/tests/test_phase3_e2e.py -q -k "voice"`
Expected: FAIL。

- [x] **Step 3: 实现模拟器与注入，确认通过**

模拟器只在内存保存帧/事件；自动回执按 `segment_seq` 顺序、可配置延迟；不打印文本/音频。

Run: `... -m pytest companion/tests/test_phase3_e2e.py -q -k "voice" companion/tests/test_phase3_e2e.py::test_device_v2_handshake_contract -q`

- [x] **Step 4: 协议文档与契约测试**

补充：`voice.event` 由服务端主动发布给 App 的字段（`conversation_id/interrupted/finish_reason`）、`voice.segment_played` 末段结束轮次、取消后 `action.stop(barge_in)` 只发一次。`test_protocol_v2_doc.py` 增加对应断言。

Run: `... -m pytest companion/tests/test_protocol_v2_doc.py -q`

- [x] **Step 5: 提交**

```bash
git add companion/simulators/device_simulator.py companion/tests/test_phase3_e2e.py companion/docs/protocol_v2.md companion/tests/test_protocol_v2_doc.py
git commit -m "test(companion): cover the voice pipeline end to end"
```

---

### Task 8: 全量验证、变异测试与日志 25

**Files:**
- Create: `companion/tests/mutation/audio_block_b.py`
- Create: `docs/语音机器人需求文档/v2_migration_log/25_audio_block_b_pipeline.md`
- Modify: `docs/语音机器人需求文档/v2_migration_log/README.md`

- [x] **Step 1: 针对性回归**

```powershell
$py="C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe"
& $py -m pytest companion/server/tests/test_audio_sentences.py companion/server/tests/test_audio_providers.py companion/server/tests/test_audio_downlink.py companion/server/tests/test_audio_pipeline.py companion/server/tests/test_audio_binding.py companion/server/tests/test_chat_cancel.py companion/server/tests/test_voice_transport.py companion/server/tests/test_device_gateway.py -q
& $py -m pytest companion/tests/test_phase3_e2e.py -q
```

- [x] **Step 2: 全量测试**

Run: `& $py -m pytest -q`
Expected: 0 failed。

- [x] **Step 3: 变异脚本（≥10 个，全部 exit=1、无挂起）**

候选：
1. `sentences.py` `flush()` 不返回剩余文本。
2. `sentences.py` 超过 `max_chars` 不硬切。
3. `downlink.py` `last_in_turn` 不置 `FLAG_LAST`。
4. `downlink.py` `turn_seq` 不自增。
5. `downlink.py` `SegmentStart` 在音频后才发（顺序反转）。
6. `pipeline.py` `end_user_speech()` 不停止上行帧。
7. `pipeline.py` cancel 不递增 generation（旧 TTS 串轮）。
8. `pipeline.py` 播放完成计时器不启动。
9. `orchestrator.py` 取消落库不带 `interrupted`。
10. `action_orchestrator.py` 取消路径固定 `server_cancel`。
11. `binding.py` 角色级回退取最旧会话。
12. `device_gateway.py` `voice.cancel` 不发 ack。

Run: `& $py companion/tests/mutation/audio_block_b.py`
Expected: `12/12 exit=1`，`hangs: none`。

- [x] **Step 4: 写日志 25 并更新索引**

日志包含：改动文件、真实命令输出摘要、偏差/Ruling、已知风险（无真实 ASR/TTS/真机）、下一步 Block C。不包含音频 payload、Key、Token、ASR/TTS 全文。README 补 25。

- [x] **Step 5: 检查并提交**

```bash
git status --porcelain
git add companion/tests/mutation/audio_block_b.py "docs/语音机器人需求文档/v2_migration_log/25_audio_block_b_pipeline.md" "docs/语音机器人需求文档/v2_migration_log/README.md"
git commit -m "test(companion): verify audio block B and record the migration log"
```

---

## Self-Review

- Spec coverage：§6 生命周期（Task 4）、§7 provider/Mock（Task 2）、§8 会话与取消落库（Task 5/6）、§9 barge-in 去重（Task 5）、§10 节流/背压（Task 3/4）、§11 超时（Task 4）、§14 子块 B 的 e2e（Task 7）。
- 未纳入：DashScope 真实 ASR/TTS（Block C）、Android App 对齐。
- 兼容性：无 runtime 时 `VoiceSession` 的同步 API 不变，Block A 的 21 例传输测试必须继续通过；有 runtime 时新增异步方法不影响旧断言。
- 类型一致性：`SegmentStart/AudioPacket`（Task 3）由 Task 4 生产；`VoiceRuntime`（Task 4）由 Task 6 构建；`cancel_reason`（Task 5）由 Task 4 传入。
