# 24 · 音频 Block A：协议 / 帧 / Ogg / 传输

- 状态：**已完成，待独立审查**
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
| `companion/server/tests/test_audio_framing.py` | 14 例帧测试 |
| `companion/server/tests/test_audio_ogg.py` | 15 例 Ogg 测试 |
| `companion/server/tests/test_voice_transport.py` | 21 例传输/门控测试 |
| `companion/tests/mutation/audio_block_a.py` | 10 个变异用例 |

### 修改

| 文件 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | `send_bytes` 协议、`send_audio()`、二进制分派、`voice.*` 处理、hello 下行帧长 warning、关闭时清理语音轮次 |
| `companion/server/app/routers/ws.py` | `StarletteChannel.send_bytes()`，与 JSON 共用发送锁 |
| `companion/server/app/config.py` | `VOICE_DOWNLINK_FRAME_MS = 60` |
| `companion/docs/protocol_v2.md` | §4.8 语音下行、§5.4 语音上行、§5.3 事件、§6.2 帧校验、§9 状态机、§10 错误码、hello_ack 帧长 |
| `companion/simulators/device_simulator.py` | 二进制帧收发、`voice.*` 方法、下行帧记录 |
| `companion/tests/test_device_gateway.py` | FakeChannel 支持 bytes；新增 hello_ack 帧长调整用例 |
| `companion/tests/test_ws_router.py` | FakeWebSocket 支持 bytes；适配器 bytes 断言 |
| `companion/tests/test_protocol_v2_doc.py` | 新消息与 voice 流程契约断言 |
| `companion/tests/test_phase3_e2e.py` | 2 例真实 WS 音频传输用例 |

## 3. 关键实现

### 3.1 帧校验

`AudioFrame.parse()` 拒绝：长度 <12、magic 非 `0xA1`、version 非 `0x01`、
kind 非 `0x01/0x02`、flags 保留位非 0、空 payload 且无静音位、payload >16 KiB。
`encode()` 做同样的边界校验，`seq/turn_seq` 用大端 4 字节。

### 3.2 Ogg

- CRC 使用 Ogg 规范的 `0x04C11DB7`，不是 `zlib.crc32`。
- muxer 默认每包一页（最低首包延迟），OpusHead + OpusTags 首次自动带出；
  granule 按 48 kHz 累加。
- demuxer 增量解析：不足一页时保留缓冲，任意字节切分不丢/不重；跳过
  OpusHead/OpusTags，支持 255 lacing 续包，CRC 错误抛 `OggError`。
- `opus_packet_duration_ms()` 按 TOC 支持 SILK/Hybrid/CELT 与 1/2/N 帧。

### 3.3 传输与门控

- `DeviceSession.send_audio()` 在自己的 `_send_lock` 内分配下行 `seq`，
  与 JSON 控制消息串行，避免 `voice.segment` 与音频帧乱序。
- 二进制帧只在 `ready + protocol>=2 + VoiceSession.listening` 时解析；
  `voice.start` 之前、`voice.end/cancel` 之后、`THINKING/SPEAKING` 期间一律丢弃。
- `turn_seq` 必须严格递增：重复或回退帧丢弃；新一轮从 0 重新开始。
- 畸形帧回 `voice.error(code="invalid_frame")`，不关闭控制通道。
- hello_ack 的 `audio.downlink.frame_ms` 以 `VOICE_DOWNLINK_FRAME_MS` 为准；
  与设备请求不同时附 `downlink_frame_ms_adjusted`。

## 4. 实际命令与输出

解释器：`C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe`。

| 命令 | 结果 |
| :--- | :--- |
| `pytest companion/server/tests/test_audio_framing.py -q` | 14 passed |
| `pytest companion/server/tests/test_audio_ogg.py -q` | 15 passed |
| `pytest companion/server/tests/test_voice_transport.py companion/server/tests/test_ws_router.py -q` | 21 passed |
| `pytest companion/server/tests/test_device_gateway.py -q` | 50 passed |
| `pytest companion/tests/test_phase3_e2e.py -q -k audio` | 2 passed |
| `pytest companion/tests/test_phase3_e2e.py companion/tests/test_protocol_v2_doc.py -q` | 27 passed |
| `pytest -q` | **278 passed in 21.45s** |
| `python companion/tests/mutation/audio_block_a.py` | **10/10 exit=1，none hung** |

变异覆盖：magic、保留位、空 payload、seq 端序、Ogg CRC、lacing 续包、granule
单位、`turn_seq` 回退、`voice.start` 前收帧、下行 seq 递增。每个变异都在
`finally` 中恢复源码，恢复后重新读取比对。

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

## 6. 已知风险 / 未完成

- 真实 DashScope TTS 的 Opus 容器是否严格为 Ogg 仍未验证（无 Key）。Block C 会保留
  `DASHSCOPE_TTS_CONTAINER=ogg|raw` 开关，真实烟测后只改适配器。
- 服务端不重编码 Opus：若 TTS 包时长与 hello_ack 的 `frame_ms` 不同，只能按 TOC
  节流并在握手修正，真机需要验证抖动缓冲与播放连续性。
- 模拟器只保存下行帧到内存列表，不做真实解码/播放；音频质量、延迟、AEC 均需真机。
- 没有实现上行 `seq` 缺口检测；协议只要求 `turn_seq` 过滤，`seq` 当前仅透传。
- Block A 的 `voice.*` 只对 v2 生效；v1 设备发送这些消息会被静默丢弃（符合既有降级）。
- 原始音频不落盘/不入库/不打印；日志只记录 reason/kind/len 等白名单字段。

## 7. 下一步

Block B：`SentenceSegmenter`、`MockAsrProvider`/`MockTtsProvider`、完整 `VoiceSession`
流水线、`DownlinkSender` 1x 节流、`voice.event` 轮次事件、会话解析、取消落库、
barge-in 的 `cancel_reason` 透传、5 分钟/单轮/首包/播放完成超时，以及模拟器自动
`segment_played` 的 e2e。
