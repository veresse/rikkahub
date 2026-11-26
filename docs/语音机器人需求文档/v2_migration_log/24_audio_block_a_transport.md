# 24 · 音频 Block A：协议 / 帧 / Ogg / 传输

- 状态：**已完成；独立审查（COMMENT）后全部 MAJOR/MINOR 已修复并复验**
- 日期：2026-11-26
- 分支：`feature/companion-robot-v2`
- 设计依据：`23_audio_block_design.md`（§4 协议、§5 编解码、§6 VoiceSession、§14 子块 A）
- 实施计划：`docs/superpowers/plans/2026-11-26-companion-audio-block-a.md`

## 1. 目标与范围

本块只做「音频进入服务端的第一段路」，不接真实 ASR/TTS：

- 12 字节音频帧解析/编码与严格校验。
- 纯 Python 的 Ogg Opus 封装/拆包（给 Block C 的 DashScope 适配器使用）。
- `WSChannel.send_bytes()`、`DeviceSession.send_audio()`、二进制帧分派。
- 每设备一个 `VoiceSession` 骨架：轮次开始/结束/取消、`turn_seq` 严格递增过滤。
- 协议补齐 `voice.start/end/cancel/segment_played` 与
  `voice.segment/cancel_ack/error`，并更新状态机。
- 设备模拟器支持二进制帧收发和 `voice.*` 消息。
- 全部测试离线可跑，不依赖任何云 Key 或真机。

不在本块：ASR/TTS 适配器、流式分句、下行节流、`voice.event` 轮次事件、取消落库、
barge-in 的 `action.stop` 去重（Block B/C）。

## 2. 改动文件

### 新增

| 文件 | 说明 |
| :--- | :--- |
| `companion/server/app/audio/__init__.py` | 音频包入口 |
| `companion/server/app/audio/framing.py` | 帧常量、`FrameError`、`AudioFrame.parse/encode` |
| `companion/server/app/audio/ogg.py` | Ogg CRC、OpusHead/Tags、muxer/demuxer、TOC 时长 |
| `companion/server/app/audio/pipeline.py` | `VoiceSession` 状态与 `turn_seq` 门控 |
| `companion/server/tests/test_audio_framing.py` | 17 例帧测试 |
| `companion/server/tests/test_audio_ogg.py` | 23 例 Ogg 测试（含页丢失/续包/换流/坏页恢复） |
| `companion/server/tests/test_voice_transport.py` | 25 例传输/门控测试 |
| `companion/tests/mutation/audio_block_a.py` | 16 个变异用例 |

### 修改

| 文件 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | `send_bytes` 协议、`send_audio()`、`send_segment()`、二进制分派、`voice.*` 处理、`voice.error.fatal`、invalid_frame 合并、hello 下行帧长 warning、关闭时清理语音轮次 |
| `companion/server/app/services/device_registry.py` | `DeviceLink` 增加 `send_control/send_segment/send_audio` |
| `companion/server/app/routers/ws.py` | `StarletteChannel.send_bytes()`，与 JSON 共用发送锁 |
| `companion/server/app/config.py` | `VOICE_DOWNLINK_FRAME_MS = 60` |
| `companion/docs/protocol_v2.md` | §4.8 致命/非致命错误、§5.4 幂等/迟到消息、§6.1 16 KiB 上限、§6.2 旧帧与节流、§9 状态机、§10 错误码 |
| `companion/simulators/device_simulator.py` | 二进制帧收发、`voice.*` 方法、下行帧可消费游标 |
| `companion/tests/test_device_gateway.py` | FakeChannel 支持 bytes；3 例 hello_ack 帧长用例（含反向与非法配置） |
| `companion/tests/test_ws_router.py` | FakeWebSocket 支持 bytes；适配器 bytes 断言 |
| `companion/tests/test_protocol_v2_doc.py` | 新消息与 voice 流程契约断言 |
| `companion/tests/test_phase3_e2e.py` | 2 例真实 WS 音频传输用例 |

## 3. 关键实现

### 3.1 帧校验

`AudioFrame.parse()` 拒绝：长度 <12、magic 非 `0xA1`、version 非 `0x01`、
kind 非 `0x01/0x02`、flags 保留位非 0、空 payload 且无静音位、payload >16 KiB。
`encode()` 做同样的边界校验，并把 `seq/turn_seq` 越界、非 bytes payload 统一转成
`FrameError`（不再泄漏 `OverflowError`/`TypeError`）。

### 3.2 Ogg

- CRC 使用 Ogg 规范的 `0x04C11DB7`，不是 `zlib.crc32`。
- muxer 默认每包一页（最低首包延迟），OpusHead + OpusTags 首次自动带出；
  granule 按 48 kHz 累加；`finish()` 输出剩余包并追加 EOS 页；单页最多 50 包。
- demuxer 增量解析：任意字节切分不丢/不重；跳过 OpusHead/OpusTags，支持 255 lacing 续包。
- 容错：上一页有未完成包、下一页未置 CONTINUED → 丢弃残缺包；缺页后的 CONTINUED
  尾部整包跳过；坏页跳过并抛一次 `OggError`，后续 `feed()` 可继续；重复/回退
  sequence 丢弃；不同 serial 的 BOS 视为新链式流并重置头部状态。

### 3.3 传输与门控

- `DeviceSession.send_audio()` 在自己的 `_send_lock` 内分配下行 `seq`，与 JSON 控制
  消息串行；参数非法或 `seq/turn_seq` 越界返回 False，不抛异常。
- `send_segment()` 在同一把锁内先发 `voice.segment`、再发该段第一帧，保证“段文本
  先于音频”不被其它发送者插入。
- 二进制帧只在 `ready + protocol>=2 + VoiceSession.listening` 时解析；
  `voice.start` 之前、`voice.end/cancel` 之后、`THINKING/SPEAKING` 期间一律丢弃。
- `turn_seq` 必须严格递增：重复或回退帧丢弃；新一轮从 0 重新开始。
- `voice.error` 增加 `fatal`：`unbound/no_speech/asr_unavailable/tts_unavailable/
  idle_timeout/unsupported_protocol/internal_error` 为致命（设备回 IDLE）；
  `busy/invalid_turn/invalid_frame` 非致命（保持当前状态）。对话锁被 App 占用时
  Block B 会显式发 `busy, fatal=true`。
- 畸形帧每轮只回一次 `invalid_frame`，避免设备灌垃圾占满下行锁。
- 同一 `turn_id` 的重复 `voice.start` 幂等；不同 `turn_id` 才回 `busy`。
- 刚结束/取消轮次的迟到 `voice.end/cancel/segment_played` 静默忽略；同一
  `(turn_id, segment_seq)` 只处理一次。
- v1 设备发送 `voice.*` 时回一次 `unsupported_protocol(fatal=true)`，不再静默丢弃。
- hello_ack 的 `audio.downlink.frame_ms` 以 `VOICE_DOWNLINK_FRAME_MS` 为准；
  非法配置打 warning 并回退 60；与设备请求不同时附 `downlink_frame_ms_adjusted`。

## 4. 实际命令与输出

解释器：`C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe`。

| 命令 | 结果 |
| :--- | :--- |
| `pytest companion/server/tests/test_audio_framing.py -q` | 17 passed |
| `pytest companion/server/tests/test_audio_ogg.py -q` | 23 passed |
| `pytest companion/server/tests/test_voice_transport.py -q` | 25 passed |
| `pytest companion/server/tests/test_ws_router.py -q` | 7 passed |
| `pytest companion/server/tests/test_device_gateway.py -q` | 52 passed |
| `pytest companion/tests/test_phase3_e2e.py -q -k audio` | 2 passed |
| `pytest companion/tests/test_phase3_e2e.py companion/tests/test_protocol_v2_doc.py -q` | 27 passed |
| `pytest -q` | **302 passed in 22.05s** |
| `python companion/tests/mutation/audio_block_a.py` | **16/16 exit=1，none hung** |

变异覆盖：magic、保留位、空 payload、seq 端序、Ogg CRC、lacing 续包、granule
单位、`turn_seq` 回退、`voice.start` 前收帧、下行 seq 递增，以及审查后新增的
页丢失残缺包、链式流、畸形帧上报、重复 start 幂等、迟到回执、teardown 关闭、
v1 协议错误。每个变异都在 `finally` 中恢复源码，恢复后重新读取比对。

真实 e2e 的两条用例：

1. 未 `voice.start` 的上行帧被丢弃；`voice.start` 后 2 帧进入轮次；
   `voice.cancel(user_stop)` 收到 `cancel_ack` 并回 IDLE。
2. 服务端 `link.send_audio()` 的真实二进制帧到达模拟器，kind/turn_seq/payload/
   last 标志完整。

## 5. 偏差与 Ruling

1. **`voice.end` 只进入 `thinking`**：Block A 没有 ASR/LLM 管线，若在这里发
   `no_speech` 或立即回 IDLE，Block B 接管时要推翻测试。明确留到 Block B 完成；
   在此期间该状态不会被真实设备触发（固件仍未接入）。
2. **`voice.segment_played` 已在协议/会话层实现，但 Block A 不会进入 `speaking`**：
   只有 Block B 的 TTS 首段会调用 `mark_speaking()`。当前实现保证重复 `segment_seq`
   幂等，为 B 留好接口。
3. **barge-in 的 `action.stop` 没有在 A 发送**：设计 §9 要求由被取消的聊天生成器透传
   `cancel_reason`，A 没有聊天生成器；`cancel_ack` 已按协议发出，`action.stop` 在 B 接。
4. **`turn_seq` 用严格递增（`<=` 丢弃）**，比设计文字「小于」更严：同一 `turn_seq`
   的重复帧同样不应重复送 ASR。e2e 用例最初两帧都用 0，正好被该规则拦住，已改为 0/1。
5. **不新增原生依赖**：Ogg 容器纯 Python；真实 Opus 编解码仍在设备与 DashScope 侧。
6. **hello_ack 帧长 warning 提前到 A 实现**：设计 §14 列在 A；避免 B 接 TTS 时
   再改握手契约。
7. **`voice.error` 增加 `fatal` 字段**（审查后补齐）：原协议只说“收到即回 IDLE”，
   与实现里 busy/invalid_turn/invalid_frame 保持轮次矛盾。现在按错误码区分，并在
   `protocol_v2.md` §4.8/§9 与现实实现一致。
8. **Ruling：`VoiceSession` 暂不加 `_lock`**。Block A 的唯一调用者是网关读循环，
   状态变更天然串行；`accept_frame()` 的 `on_frame` 在 A 为 None。Block B 引入
   ASR/TTS/LLM 后台任务时，会在管线内用单一轮次任务 + `generation` 校验串行化
   所有状态变更（计划 Task 4），避免为过渡实现引入会被 B 推翻的锁语义。
9. **Ruling：`send_audio()` 是底层原语，不做轮次门控**。它只校验链路/参数；
   “只能在当前轮次内发送”由 Block B 的 `DownlinkSender` 与 `generation` 保证。
   docstring 与协议 §6.2 已写明，Block B 会补对应门控测试。
10. **Ruling：`invalid_frame` 每轮只回一次**。持续灌垃圾的设备不应让每帧都占用下行锁；
    计数与首个错误仍在 DEBUG 日志中可见。

## 6. 已知风险 / 未完成

- 真实 DashScope TTS 的 Opus 容器是否严格为 Ogg 仍未验证（无 Key）。Block C 会保留
  `DASHSCOPE_TTS_CONTAINER=ogg|raw` 开关，真实烟测后只改适配器。
- 服务端不重编码 Opus：若 TTS 包时长与 hello_ack 的 `frame_ms` 不同，只能按 TOC
  节流并在握手修正，真机需要验证抖动缓冲与播放连续性。
- 模拟器只保存下行帧到内存列表，不做真实解码/播放；音频质量、延迟、AEC 均需真机。
- 没有实现上行 `seq` 缺口检测；协议只要求 `turn_seq` 过滤，`seq` 当前仅透传。
- 原始音频不落盘/不入库/不打印；日志只记录 reason/kind 等白名单字段，已有 caplog
  断言 payload 不出现。
- `VoiceSession` 的并发串行化依赖 Block B 的单一轮次任务（见 Ruling 8）。

## 7. 下一步

Block B：`SentenceSegmenter`、`MockAsrProvider`/`MockTtsProvider`、完整 `VoiceSession`
流水线、`DownlinkSender` 1x 节流、`voice.event` 轮次事件、会话解析、取消落库、
barge-in 的 `cancel_reason` 透传、5 分钟/单轮/首包/播放完成超时，以及模拟器自动
`segment_played` 的 e2e。

## 8. 独立审查与修复记录

审查结论：COMMENT（2 MAJOR + 若干 MINOR/测试缺口）。逐条处理：

| # | 级别 | 问题 | 处理 |
| :--- | :--- | :--- | :--- |
| 1 | MAJOR | 协议“任何 `voice.error` → 设备 IDLE”与实现“保持原轮次”矛盾 | 增加 `fatal` 字段，按错误码区分；协议 §4.8/§9 与设计 §4.1/§4.2 同步 |
| 2 | MAJOR | demuxer 忽略 CONTINUED，页丢失后拼出坏包 | 检测缺页/非续页并清空 `_partial`；新增 2 例页丢失测试 |
| 3 | MAJOR | 换 serial 后永久丢弃新链式流 | BOS 换 serial 时 `_begin_chain()` 重置状态；新增换流测试 |
| 4 | MINOR | CRC 失败后坏页留在缓冲；重复/乱序页未校验 | 坏页跳过/重同步；sequence 重复与缺口检查；新增恢复与重复页测试 |
| 5 | MINOR | `encode()` 抛 `OverflowError/TypeError`；`send_audio` 越界 | `encode()` 统一转 `FrameError`；`send_audio` 校验 32 位上界并捕获；新增参数化负例 |
| 6 | MINOR | 无原子的“段文本 + 首帧”发送 | 新增 `DeviceSession.send_segment()`，锁内两写；新增顺序测试 |
| 7 | MINOR | 迟到 `segment_played` 变 `invalid_turn`；注释错误 | 记录 `last_turn_id`，迟到消息静默忽略；新增幂等/迟到测试 |
| 8 | MINOR | `voice.error` 无合并/限速 | `invalid_frame` 每轮一次；新增测试 |
| 9 | MINOR | 下行帧无跨轮身份，设备旧帧规则不可用 | 协议 §6.2 明确服务端保证不跨轮、设备按 `voice.segment.turn_id` 切轮 |
| 10 | MINOR | `VoiceSession` 无 `_lock` | Ruling 8：A 串行调用；B 用单一轮次任务串行化 |
| 11 | MINOR | 非法 `VOICE_DOWNLINK_FRAME_MS` 静默回退；测试依赖全局默认 | 打 warning；新增反向/非法配置测试并 monkeypatch 固定 |
| 12 | MINOR | `send_audio` 无轮次门控 | Ruling 9 + docstring/协议说明；B 补门控测试 |
| 13 | MINOR | `DeviceLink` 未暴露 `send_audio` | 协议补 `send_control/send_segment/send_audio` |
| 14 | MINOR | v1 `voice.*` 静默忽略，与设计不符 | 回一次 `unsupported_protocol(fatal=true)`；新增测试 |
| 15 | NIT | muxer 无 EOS；50 包上限未校验 | `finish()` 输出 EOS；构造参数校验 1..50；新增测试 |
| 16 | NIT | 模拟器 `_audio_event` 死代码；`wait_for_audio` 不消费 | 删除事件；改为游标消费 |
| 17 | NIT | 日志“21 例”误记；16 KiB 上限未入协议；`invalid_frame` 未入设计 | 本文件与协议/设计已更正 |
| 18 | MINOR | 畸形帧测试是空转（在 start 之前发） | 改为 start 后发，断言 `invalid_frame` 一次且后续帧仍可收 |

新增测试覆盖了审查列出的缺口：teardown 关闭、JSON/二进制顺序、`segment_played`
幂等、`send_audio` 负例、demuxer 鲁棒性、v1 协议错误、hello_ack 双向帧长、
payload 不落日志。

## 9. 提交

| 提交 | 内容 |
| :--- | :--- |
| `1f890188` | 帧编解码 |
| `4f39018e` | Ogg mux/demux |
| `90c9e95c` | 传输与 VoiceSession 门控 |
| `28e60a98` | 协议、模拟器、2 例 e2e |
| `6d9154c1` | 变异脚本、日志 24、README |
| （本次） | 审查修复：fatal/幂等/原子发送/demuxer 鲁棒性/测试补齐 |
