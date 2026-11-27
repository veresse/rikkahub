# 26 · 音频 Block C：真实 DashScope 适配器（本地假服务器验证）

- 状态：**已完成（三轮独立审查修复；真实云/真机验收待办）**
- 日期：2026-11-27
- 分支：`feature/companion-robot-v2`
- 设计依据：`23_audio_block_design.md` §7.3/§7.4/§12/§14 子块 C
- 实施计划：`docs/superpowers/plans/2026-11-26-companion-audio-block-c.md`
- 前置：Block B（`a5e8a4e4`，403 passed / mutation 15/15）

## 1. 目标与范围

在 Block A/B 的离线语音管线之上接入真实 DashScope：

- ASR：`fun-asr-realtime` WebSocket，`run-task` / Ogg 二进制上行 / `finish-task`；
- TTS：`qwen-audio-3.0-tts-flash` HTTP SSE，流式 base64 Opus；
- `VOICE_PROVIDER=dashscope` 装配；缺 Key 明确降级为 unavailable；
- 所有行为用本地假 WS/SSE 服务器验证，全程离线、无真实 Key。

不在本块：真实云调用的“通过”结论、真机音频验收、Android App 对齐。真实云与真机
只提供烟测脚本与步骤，结果一律标记为**待验证**。

## 2. 改动文件

### 新增

| 文件 | 说明 |
| :--- | :--- |
| `companion/server/app/audio/dashscope.py` | `dashscope_asr_url()` / `dashscope_tts_url()`、`DashScopeAsrProvider` / `DashScopeAsrStream`、`DashScopeTtsProvider` |
| `companion/server/tests/test_dashscope_config.py` | 6 例 URL（含 region）与配置默认值 |
| `companion/server/tests/test_dashscope_asr.py` | 14 例 ASR：握手、音频转发、partial/final、finish 幂等、超时、错误、握手期 close 竞态、close 后排队者、服务端中途断线、关闭、日志隐私 |
| `companion/server/tests/test_dashscope_tts.py` | 11 例 TTS：SSE 正常/`[DONE]`、`audio.url`、HTTP 错误、坏 JSON/base64、连接异常、缺 Key、`aclose`、日志隐私 |
| `companion/server/tests/test_audio_dashscope_pipeline.py` | 2 例真实适配器 + `VoiceSession` 完整轮次（正常 / TTS 致命错误） |
| `companion/server/tests/dashscope_fakes.py` | 测试共用的本地假 ASR 服务器与 SSE 构造器（可注入握手延迟 / 收到音频即断开） |
| `companion/server/tests/test_voice_smoke_script.py` | 9 例烟测脚本离线部分（Ogg 页计数及跨块计数、`_run_tts` 接线、ASR region 透传、缺 Key、缺文件、无动作） |
| `companion/scripts/voice_dashscope_smoke.py` | 真实云烟测脚本（`--tts` / `--asr-ogg`，不落盘、不打印正文） |

### 修改

| 文件 | 说明 |
| :--- | :--- |
| `companion/server/app/config.py` | `DASHSCOPE_REGION`、`DASHSCOPE_WORKSPACE_ID`、`DASHSCOPE_ASR_WS_URL`、`DASHSCOPE_TTS_HTTP_URL`；`VOICE_PROVIDER` 注释更新 |
| `companion/server/app/audio/binding.py` | `VOICE_PROVIDER=dashscope` 装配真实适配器并透传 region；缺 Key 降级并 warning |
| `companion/server/tests/test_audio_binding.py` | 原 “unknown=dashscope” 用例改为真正的未知 provider；新增 3 例 dashscope 装配/降级 |
| `companion/server/requirements.txt` | 显式声明 `websockets>=13.0` |
| `companion/deploy/companion.env.example` | 补齐语音开关与 `VOICE_*`/`DASHSCOPE_*` 注释示例（不含真实值） |

## 3. 关键实现

### 3.1 URL 与配置

- `dashscope_asr_url(workspace_id, override, region="cn-beijing")`：override 优先（本地假服务器）；
  否则 `wss://{workspace}.{region}.maas.aliyuncs.com/api-ws/v1/inference`；region 为空回落
  `cn-beijing`；无 workspace 用 `wss://dashscope.aliyuncs.com/api-ws/v1/inference`。
- `dashscope_tts_url(override)`：override 或官方 SpeechSynthesizer 地址。
- Key 默认空；`VOICE_PROVIDER=dashscope` 但 Key 为空时**不构造适配器、不联网**，回落到
  `UnavailableAsrProvider/UnavailableTtsProvider`。

### 3.2 ASR（WebSocket）

- 懒连接：`send()` / `events()` / `finish()` 首次使用时连接，`_connect_lock` 保证只连一次；
  `_ensure_connected()` 拿到锁后与 `_connect()` 入口都会复查 `_closed`，握手完成后再复查
  一次，已关闭则立即关掉刚建立的连接并报 `asr_unavailable`，避免 WS / 读任务泄漏，
  也避免 `close()` 后排队者再发起第二次连接。
- 握手：发送 `run-task`（`task_group=audio`、`task=asr`、`function=recognition`、
  `model`、`parameters.format=opus`、`sample_rate`、`max_sentence_silence=800`、
  `language_hints=["zh"]`、`input={}`），等 `task-started`；超时/失败统一
  `ProviderError("asr_unavailable")`。
- 身份头：`Authorization: bearer <key>`；websockets ≥14 用 `additional_headers`，
  旧版回退 `extra_headers`。
- 读循环把 `result-generated` 的 `payload.output.sentence.text` 映射为 partial；
  `sentence_end=true` 的句子累计到最终文本；`task-finished` 只发一次 final；
  `task-failed` / 异常断线发一次 error。心跳/空文本忽略。
- `finish()`：发送 `finish-task` 后最多等 `finish_timeout`（默认 2s）；超时或对端直接
  关闭时用累计文本补一次 final，避免轮次悬挂。
- `close()`：幂等；先关 WS 再取消读任务，队列补哨兵；关闭后 `events()` 立即结束。
- 隐私：websockets 连接使用独立的 INFO 级别 logger，避免全局 DEBUG 时库把收发帧
  （识别文本/音频）原样打印；本模块只记录事件类型与文本长度。

### 3.3 TTS（HTTP SSE）

- 每次 `synthesize()` 用独立 `httpx.AsyncClient`（`client_factory` 可注入
  `MockTransport`），`async with` 保证取消/异常时关闭连接。
- 请求：`Authorization: Bearer <key>`、`X-DashScope-SSE: enable`；body 为
  `{"model", "input": {"text", "voice", "format":"opus", "sample_rate":24000,
  "bit_rate":32}}`。
- 只接受 `data:` 行；`[DONE]` 结束；`output.audio.data` base64 解码后原样 yield
  （由管线的 `OggOpusDemuxer` 拆包）；`output.audio.url`（非流式）直接
  `tts_unavailable`，不做二次下载；`finish_reason=stop` / `sentence-end` 结束。
- HTTP 非 200、坏 JSON、坏 base64、连接错误统一 `ProviderError("tts_unavailable")`；
  日志只写状态码/事件类型/字节数，不写文本或 Key。

### 3.4 运行时装配

- `VOICE_PROVIDER=dashscope` + 非空 Key → `DashScopeAsrProvider` / `DashScopeTtsProvider`；
  构造阶段只计算 URL，**不发起连接**。
- 未知 provider 与缺 Key 的 dashscope 都回落 unavailable。

## 4. 验证结果（本机实测）

解释器：`C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe`；
全部从仓库根运行，无真实 Key、无真机。

| 场景 | 命令 | 结果 |
| :--- | :--- | :--- |
| 新增适配器/脚本定向 | `& $py -m pytest companion/server/tests/test_dashscope_config.py companion/server/tests/test_dashscope_asr.py companion/server/tests/test_dashscope_tts.py companion/server/tests/test_audio_binding.py companion/server/tests/test_audio_dashscope_pipeline.py companion/server/tests/test_voice_smoke_script.py -q` | **55 passed** |
| 端到端稳定性 | 完整轮次用例连跑 5 次 | 2 passed ×5（0.34–0.45s） |
| 全量 | `& $py -m pytest -q` | **448 passed / 0 failed**（28.18s） |
| 烟测 `--help` | `& $py -m companion.scripts.voice_dashscope_smoke --help` | 正常打印用法，exit 0 |
| 烟测缺 Key | `& $py companion/scripts/voice_dashscope_smoke.py --tts hello` | `DASHSCOPE_API_KEY is not set; refusing to run`，exit 2，未联网 |
| 烟测缺文件 | 设假 Key 后 `--asr-ogg` 指向不存在路径 | `asr ogg file not found: ...`，exit 2，未联网 |
| Block B 回归 | `& $py companion/tests/mutation/audio_block_b.py` | **15/15 exit=1，hangs: none**（Task 7） |
| Block C 变异 | `& $py companion/tests/mutation/audio_block_c.py` | **9/9 exit=1，hangs: none**（Task 7 + 复审补充） |

## 5. 本地假服务器覆盖

| 层 | 覆盖 |
| :--- | :--- |
| ASR 握手 | `run-task` 字段、`Authorization`、`task-started`、连接拒绝、握手超时 |
| ASR 上行 | 二进制 Ogg 字节原样转发；`finish-task` 只发一次 |
| ASR 结果 | partial；`sentence_end` 累计；单次 final；空语音 final 为空；`finish_timeout` 回退 |
| ASR 错误 | 握手 `task-failed`、结束阶段 `task-failed`、录音中途服务端直接断开、异常断线 → `asr_unavailable` |
| ASR 关闭 | `close()` 幂等、读任务结束、服务端会话关闭、关闭后 `events()` 立即结束、握手进行中 `close()` 会关掉刚建立的连接 |
| TTS 请求 | URL/头/body（model、voice、format、sample_rate、bit_rate） |
| TTS 流 | 多块 base64 顺序、`[DONE]`、`finish_reason=stop` |
| TTS 错误 | `audio.url`、HTTP 401/500、坏 JSON、坏 base64、连接异常、缺 Key（0 请求） |
| TTS 取消 | 首块后 `aclose()` 有界并关闭传输 |
| 烟测脚本 | 完整页计数、跨 chunk 半页拼接、`_run_tts` 接线、缺 Key / 缺文件友好退出、无动作报错 |
| 集成 | 真实 `VoiceSession`：`voice.start` → 上行帧 → ASR final → 聊天两句 → TTS 两段 Ogg → 下行 `FLAG_LAST` → 回执 → `turn_end(stop)`；TTS 致命错误 → `tts_unavailable(fatal)` + `turn_end(error)` |
| 隐私 | caplog 断言本模块日志无识别/合成文本、无 Key |

## 6. 真实云 / 真机验收（待办，未伪造通过）

1. 在**本机环境变量**（不要写进仓库、`.env` 提交、日志）设置
   `DASHSCOPE_API_KEY`；如使用专属 workspace，再设 `DASHSCOPE_WORKSPACE_ID` 与
   `DASHSCOPE_REGION`（默认 `cn-beijing`，其他区域域名未验证）。
2. TTS：
   `python -m companion.scripts.voice_dashscope_smoke --tts "你好，今天天气不错" --print-text`
   - 期望 `bytes > 0`；若 `ogg_pages == 0`，说明返回的是裸 Opus，记录后评估
     `DASHSCOPE_TTS_CONTAINER=raw`；
   - 若报 `tts_unavailable`，先确认模型/音色/地域与 Key 权限，再决定是否调整 URL。
3. ASR：准备一段 16kHz 单声道 Ogg Opus，运行
   `python -m companion.scripts.voice_dashscope_smoke --asr-ogg your_16k.ogg`
   - 期望出现 `partial` 与一次 `final`，`text_len > 0`；
   - 重点确认多句时 final 文本**没有重复**（本实现按 `sentence_end` 逐句拼接；
     若云端返回累计文本需调整 `_handle_result`）。
4. 真机（ESP32 / Android）：
   - 首个 `voice.segment` 到首帧音频的时延、下行节流稳定性；
   - barge-in：`cancel_ack` + 只一次 `action.stop(barge_in)`，新轮可立即开始；
   - `segment_played` 回执/末帧 `FLAG_LAST` 与 `turn_end(stop)`；
   - 5 分钟沉默自动结束、播放完成超时、断线清理；
   - App 侧 `voice.event` 展示与 `interrupted` 消息。
5. 验收记录写回本文件；只写“待验证/通过/失败 + 现象”，不写 Key、正文或音频。

## 7. 限制与已知偏差

1. **ASR 文本语义未对真实云验证**：`fun-asr-realtime` 的 `sentence.text` 是“当前句”
   还是“累计文本”只能以真实返回为准；当前实现按“当前句 + sentence_end 拼接”，
   真实烟测若发现重复需只改 `_handle_result`。
2. **TTS 容器未确认**：默认为 Ogg；若云端返回裸 Opus，`OggOpusDemuxer` 会跳过坏页，
   需要切 `DASHSCOPE_TTS_CONTAINER=raw`（适配器无需改）。
3. **无重连/重试**：单轮单连接；连接失败直接 `asr_unavailable`，不做指数退避，避免
   无限占用设备链路。
4. **SIP/代理/TLS**：沿用 `websockets` / `httpx` 默认行为，不新增跳过 TLS 的开关。
5. **烟测页数口径**：`ogg_pages` 统计所有完整 Ogg 页（含 OpusHead/OpusTags/EOS），
   跨 SSE chunk 用缓冲拼接后再计数；用于判断“是否 Ogg 容器”，不是音频包数。
6. **区域**：`DASHSCOPE_REGION` 默认 `cn-beijing`，仅按设计拼接 workspace 域名；
   其他区域的真实域名未验证，改动前先查官方文档。
7. 真实云/真机的时延、音质、AEC、网络抖动均未验证；本块不声称通过。

## 8. 提交与下一步

| 提交 | 内容 |
| :--- | :--- |
| `eb4210a2` | Task 1：URL 与配置 |
| `83702f22` | Task 2：ASR WebSocket 适配器 |
| `33f8449f` | Task 3：TTS SSE 适配器 |
| `901101db` | Task 4：运行时装配与降级 |
| `a63a4df0` | Task 5：真实适配器 + 假服务器完整轮次 |
| `53bc2efe` | Task 6/7：烟测脚本、迁移日志、README、变异（15/15 + 5/5） |
| `d42fcfa1` | 记录 Block C 结果 |
| `0fe92337` | 首轮独立审查修复：握手期 close 竞态、跨 chunk 页计数、缺文件友好退出、区域配置、部署样例、补断线/连接异常用例 |
| `e1fbf78e` | 复审修复：烟测 region 透传、`_run_tts` 接线用例、close 后排队者不再二次连接、binding region 断言、变异补到 7 例 |
| `7f50a04e` | 三轮复审修复：双 send 排队仅连接一次、烟测 region 接线用例、坏页清缓冲、变异补到 9 例 |

### 8.1 首轮独立审查修复（全部 Minor，无 Blocker/Major）

| # | 问题 | 修复 |
| :--- | :--- | :--- |
| 1 | `_connect()` 握手完成后未复查 `_closed`，与 `close()` 并发会泄漏 WS/读任务 | 握手后复查 `_closed`，已关闭则 `_close_socket` 并报 `asr_unavailable`；新增 `test_close_during_connect_closes_the_socket` |
| 2 | 烟测逐 chunk 计页，跨 chunk 的页被整页丢弃 | 新增 `_OggPageCounter` 保留半页缓冲；新增跨块计数用例 |
| 3 | 文档称覆盖“异常断线/连接异常”，但套件缺用例 | 新增 ASR 中途断线、TTS `ConnectError` 两例；假服务器支持握手延迟/收到音频即断开 |
| 4 | `--asr-ogg` 路径不存在时抛 traceback | 先检查 `is_file()`，打印一行错误并 exit 2；新增用例 |
| 5 | 部署样例缺少语音开关与超时项 | `companion.env.example` 补齐注释示例（无真实值） |
| 6 | 设计 §12 的 `DASHSCOPE_REGION` 未实现 | 新增配置项并传入 URL 构造；新增 region 用例，空值回落 `cn-beijing` |

修复后：定向 **51 passed**、全量 **444 passed**、Block B 变异 **15/15**、
Block C 变异 **5/5**、烟测 `--help` exit 0 / 缺 Key exit 2 / 缺文件 exit 2。

### 8.2 复审修复（第二、三轮，全部 Minor）

| # | 问题 | 修复 |
| :--- | :--- | :--- |
| 1 | 烟测 ASR 未透传 `DASHSCOPE_REGION`，非默认区域会连错域名 | `_run_asr` 传入 `settings.DASHSCOPE_REGION`；§6 步骤补充区域说明 |
| 2 | 跨块计页只测了计数器类，`_run_tts` 接线回退不会被发现 | 新增 `test_run_tts_counts_pages_across_chunks`（7 字节切片，断言 `ogg_pages=6`），并纳入变异 |
| 3 | `close()` 后排队等锁的第二个握手仍会再发起一次连接 | `_ensure_connected()` 拿到锁后与 `_connect()` 入口复查 `_closed`；原握手后复查保留 |
| 4 | binding 的 region 透传无断言 | workspace 用例改为 `cn-shanghai` 并断言 URL；删掉透传行会失败 |
| 5 | 修复 3 的两层 `_closed` 复查无守护用例 | 新增 `test_close_during_handshake_only_connects_once`（双 `send` 排队 + 假服务器只接受 1 次连接），并纳入变异 |
| 6 | 烟测 region 透传无守护用例 | 新增 `test_run_asr_passes_configured_region`（monkeypatch provider 捕获 kwargs），并纳入变异 |
| 7 | `_OggPageCounter` 遇坏页后缓冲滞留 | `OggError` 时清空缓冲，避免裸 Opus 诊断时滞留内存；副作用是坏页后同块剩余数据一并丢弃，需新的页对齐块才能恢复计数（诊断脚本可接受） |

三轮后：定向 **55 passed**、全量 **448 passed**、Block B 变异 **15/15**、
Block C 变异 **9/9**、无挂起；烟测 `--help` exit 0 / 缺 Key exit 2 / 缺文件 exit 2。

下一步：真实云/真机验收（待 Key/设备）→ Android App 侧语音 UI 与回执对齐。
