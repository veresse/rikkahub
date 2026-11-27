# Companion 音频 Block C（真实 DashScope 适配器）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在 Block A/B 的离线语音管线之上接入真实 DashScope `fun-asr-realtime`（WebSocket）与 `qwen-audio-3.0-tts-flash`（HTTP SSE）适配器；全部行为用本地假 WS/SSE 服务器验证，真实云与真机验收只准备脚本/步骤并标记待办。

**Architecture:** 新增 `companion/server/app/audio/dashscope.py`，实现与 `providers.py` 中 `AsrProvider/AsrStream/TtsProvider` 协议兼容的 `DashScopeAsrProvider`、`DashScopeAsrStream`、`DashScopeTtsProvider`。ASR 采用懒连接 + 后台读循环（`websockets`），把 `result-generated` 映射为 `partial/final`；TTS 用 `httpx` 流式读取 SSE，把 base64 音频块原样交给现有 `OggOpusDemuxer`。`binding._build_providers()` 按 `VOICE_PROVIDER` 装配，缺 Key 时明确降级为 unavailable，绝不静默连云。

**Tech Stack:** Python 3.11+、asyncio、`websockets`（uvicorn[standard] 已间接提供，显式写入 requirements）、`httpx`、pytest（`asyncio_mode=auto`）。

**Spec:** `docs/语音机器人需求文档/v2_migration_log/23_audio_block_design.md` §7.3/§7.4/§12/§14 子块 C

**Depends on:** Block B（提交 `a5e8a4e4` 及之前）：`VoiceSession` 完整管线、`providers.py` 协议、`binding.build_voice_runtime()`、`OggOpusMuxer/Demuxer`。

## Global Constraints

- 原始音频只在内存；不落盘、不入库、不打印 payload/ASR 全文/TTS 文本/Key。
- 所有测试离线可跑；`pytest` 从仓库根运行，解释器
  `C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest`。
- 不读取/提交根 `local.properties`；不请求真实 DashScope。
- 云不可用/缺 Key 时必须回 `asr_unavailable`/`tts_unavailable`，不得无限重试。
- 真实云/真机结论只能写“待验证”，不得伪造通过。
- 保持 Block A/B 的 403 例全量与 `audio_block_b.py` 15 个变异通过。

## Review Focus

1. ASR 握手/结束超时有界；`close()` 幂等，取消后不再读 WS、不泄漏任务。
2. `result-generated` 只产生 partial/final，不把心跳当文本；`sentence_end`/`task-finished` 语义与管线 `_asr_last_text` 一致。
3. TTS SSE 只接受流式 base64；遇到 `audio.url`、HTTP 非 200、坏 JSON 都要有界失败，不写日志正文。
4. 缺少 Key/未知 provider 的降级路径可测且不尝试联网。
5. 集成测试用真实 `VoiceSession` + 假服务器跑完整轮次（含 Ogg 解复用与下行帧）。

---

### Task 1: 配置与 URL 解析

**Files:**
- Modify: `companion/server/app/config.py`
- Create: `companion/server/app/audio/dashscope.py`（本任务只放 URL/常量/错误映射工具）
- Test: `companion/server/tests/test_dashscope_config.py`

**Interfaces:**
- `dashscope_asr_url(workspace_id: str, override: str = "") -> str`：
  override 非空时原样返回（测试用 `ws://127.0.0.1:PORT`）；否则 workspace 非空 →
  `wss://{workspace}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`；否则
  `wss://dashscope.aliyuncs.com/api-ws/v1/inference`。
- `dashscope_tts_url(override: str = "") -> str`：override 或
  `https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer`。
- 新增 settings：`DASHSCOPE_WORKSPACE_ID: str = ""`、`DASHSCOPE_ASR_WS_URL: str = ""`、
  `DASHSCOPE_TTS_HTTP_URL: str = ""`。

- [x] **Step 1: 写失败测试**：默认/工作区/override 三种 URL；settings 默认值存在。
- [x] **Step 2: 运行确认失败**。
- [x] **Step 3: 实现**（纯函数，无 IO）。
- [x] **Step 4: 通过并提交**。

### Task 2: DashScope ASR（WebSocket）

**Files:**
- Modify: `companion/server/app/audio/dashscope.py`
- Test: `companion/server/tests/test_dashscope_asr.py`

**Interfaces:**
- `class DashScopeAsrProvider`: `__init__(api_key, model="fun-asr-realtime", url="", workspace_id="", handshake_timeout=5.0, finish_timeout=2.0)`；`open(*, device_id, turn_id, sample_rate=16000) -> DashScopeAsrStream`。
- `class DashScopeAsrStream`：
  - 懒连接：`send()`/`events()`/`finish()` 首次调用时 `websockets.connect`，发 `run-task` 并等 `task-started`（超时 → `ProviderError("asr_unavailable")`）。
  - `send(ogg_bytes)`：WS 二进制；未连接成功则抛 `ProviderError`。
  - `finish()`：`finish-task` + 等内部 final（≤ `finish_timeout`），超时用累计文本补发一次 final；幂等。
  - `close()`：取消读任务/等待任务 + 关闭 WS；幂等，吞掉关闭异常。
  - `events()`：`partial`（每句中间结果）、`final`（累计全文，仅一次）、`error(asr_unavailable)`。
- `run-task` payload（严格按设计 §7.3）：`header.action=run-task`、`header.streaming=duplex`、
  `payload.task_group=audio`、`task=asr`、`function=recognition`、`model`、`parameters.format=opus`、
  `sample_rate`、`max_sentence_silence=800`、`language_hints=["zh"]`、`payload.input={}`。
- 鉴权头：`Authorization: bearer <key>`。
- `websockets` 版本兼容：≥14 用 `additional_headers`，否则 `extra_headers`。

- [x] **Step 1: 写失败测试**（本地 `websockets.serve` 假服务器）
  - 握手：收到 run-task、返回 task-started；断言 payload/头（不记录 Key 值到日志）。
  - 音频转发：客户端 `send()` 的字节等于假服务器收到的二进制。
  - 结果：partial 事件；`sentence_end` 后 `task-finished` → 单次 final 为累计文本。
  - `finish()` 发出 finish-task 且幂等；无文本时 final 为空。
  - `task-failed` → error `asr_unavailable`。
  - 连接拒绝/握手超时 → `ProviderError("asr_unavailable")`，且不挂起。
  - `close()` 后读任务结束、WS 关闭、重复 close 安全。
  - caplog 不含识别全文/Key。
- [x] **Step 2: 运行确认失败**。
- [x] **Step 3: 实现**（读循环 `asyncio.create_task`；队列 + 哨兵；所有 await 有界）。
- [x] **Step 4: 通过**。

### Task 3: DashScope TTS（HTTP SSE）

**Files:**
- Modify: `companion/server/app/audio/dashscope.py`
- Test: `companion/server/tests/test_dashscope_tts.py`

**Interfaces:**
- `class DashScopeTtsProvider`: `__init__(api_key, model="qwen-audio-3.0-tts-flash", voice="cherry", url="", sample_rate=24000, bit_rate=32, client_factory=None)`。
- `synthesize(text, *, turn_id, segment_seq) -> AsyncIterator[bytes]`：
  `POST url`，头 `Authorization: Bearer <key>`、`X-DashScope-SSE: enable`、`Content-Type: application/json`；
  body `{"model", "input": {"text", "voice", "format": "opus", "sample_rate", "bit_rate"}}`。
- SSE 解析：只处理 `data:` 行；`[DONE]` 结束；`output.type=="sentence-synthesis"` 的
  `output.audio.data` base64 → yield；`output.audio.url` 非空 → `ProviderError("tts_unavailable")`；
  `finish_reason=="stop"` → 结束。
- 非 200、连接错误、JSON/Base64 解析错误 → `ProviderError("tts_unavailable")`；日志只写状态码/事件类型。
- `client_factory` 默认 `httpx.AsyncClient(timeout=...)`；测试注入 `MockTransport`。
- 取消：生成器被 `aclose()` 时 `async with` 自动关闭流。

- [x] **Step 1: 写失败测试**（`httpx.MockTransport` + 自定义 `AsyncByteStream` 假 SSE）
  - 正常两块 + stop：yield 解码后的字节，顺序一致；断言请求头/body。
  - `output.audio.url` → tts_unavailable，不下载。
  - HTTP 401/500 → tts_unavailable。
  - 坏 JSON/坏 base64 → tts_unavailable。
  - 中途 `aclose()` 不挂起；caplog 无文本/Key。
- [x] **Step 2: 运行确认失败**。
- [x] **Step 3: 实现**。
- [x] **Step 4: 通过**。

### Task 4: 运行时装配与降级

**Files:**
- Modify: `companion/server/app/audio/binding.py`、`companion/server/requirements.txt`
- Test: `companion/server/tests/test_audio_binding.py`

- `VOICE_PROVIDER=dashscope` + 有 `DASHSCOPE_API_KEY` → `DashScopeAsrProvider`/`DashScopeTtsProvider`
  （URL 使用 settings override/工作区构造）。
- 无 Key → 两个 `Unavailable*` + 一次 warning；未知值同旧行为。
- requirements 增加 `websockets>=13`。

- [x] **Step 1: 改/加失败测试**（有 Key 实例类型与 URL；无 Key 降级）。
- [x] **Step 2: 运行确认失败**。
- [x] **Step 3: 实现**。
- [x] **Step 4: 通过**。

### Task 5: 真实适配器 + 假服务器的完整轮次

**Files:**
- Test: `companion/server/tests/test_audio_dashscope_pipeline.py`

- 用 `websockets.serve` 假 ASR + `httpx.MockTransport` 假 TTS + `VoiceSession`：
  `voice.start` → 2 帧 → `voice.end` → 假 ASR final → 脚本聊天 2 句 → TTS 两段（Ogg）→
  `voice.segment` + 音频 → `segment_played(last)` → `turn_end(stop)`。
- 断言：ASR 收到 run-task/finish-task 与上行 Ogg 字节；TTS 每段请求两次；下行帧含 `FLAG_LAST`；
  sink 无 `voice.error`；无 payload/文本进日志。
- 错误路径：TTS 返回 `audio.url` → `tts_unavailable`（fatal）且 `turn_end(error)`。

- [x] **Step 1: 写失败测试**。
- [x] **Step 2: 运行确认失败**。
- [x] **Step 3: 修到通过**（只改适配器/装配，不改管线语义）。
- [x] **Step 4: 全量回归**。

### Task 6: 烟测脚本与文档

**Files:**
- Create: `companion/scripts/voice_dashscope_smoke.py`
- Create: `docs/语音机器人需求文档/v2_migration_log/26_audio_block_c_dashscope.md`
- Modify: `docs/语音机器人需求文档/v2_migration_log/README.md`

- 脚本：`--tts TEXT`（真实 TTS，打印字节数/Ogg 页数，不落盘；`--print-text` 才打印文本长度）；
  `--asr-ogg PATH`（发送用户提供的 16k Ogg Opus，打印事件类型与最终文本长度）；
  Key 只从环境变量读取；缺 Key 直接退出非 0。
- 文档记录：改动文件、命令输出、假服务器覆盖、**真实云/真机待办**（含具体步骤）、限制。
- README 增加 26 行。

- [x] **Step 1: 实现脚本**（不做网络之外的副作用）。
- [x] **Step 2: `--help`/缺 Key 路径验证**（不联网）。
- [x] **Step 3: 写 26 日志 + README**。

### Task 7: 变异与最终审查

**Files:**
- Create: `companion/tests/mutation/audio_block_c.py`

- 变异（每条必须被现有用例捕获）：
  1. ASR 不发 `finish-task`。
  2. ASR 忽略 `sentence_end`，每个 result 都发 final。
  3. TTS 不发 `X-DashScope-SSE: enable`。
  4. TTS 不拒绝 `output.audio.url`。
  5. binding 在有 Key 时仍回退 unavailable。
- 运行：`audio_block_b.py`（15/15）+ `audio_block_c.py`（5 例，复审后 9 例）+ 全量。
- 写日志结果表、提交，交独立子智能体复审；修正后再提交。

- [x] **Step 1: 写 mutation 脚本并跑**。
- [x] **Step 2: 更新日志/README/计划复选框**。
- [x] **Step 3: 提交 + 子智能体审查 + 修复**。

## 验收

- [x] 全量 pytest 通过（Block B 403 + 新增用例）。
- [x] Block B 变异 15/15、Block C 变异 5/5（复审后 9/9），无挂起。
- [x] 本地假 WS/SSE 覆盖 ASR/TTS 正常、错误、超时、取消。
- [x] `VOICE_PROVIDER=dashscope` 缺 Key 明确降级；有 Key 不尝试连接（仅构造）。
- [x] 日志/DB 无音频 payload、无 ASR/TTS 全文、无 Key。
- [x] 真实云/真机验收明确标记待办，未伪造通过。
