# Companion 音频 Block A（协议 / 帧 / Ogg / 传输）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让服务端与模拟器能够按 `protocol_v2.md` 收发 12 字节音频帧，并提供纯 Python 的 Ogg Opus 封装/拆包与 `VoiceSession` 帧门控骨架，为 Block B 的 Mock ASR/TTS 管线打底。

**Architecture:** 新增 `companion/server/app/audio/`：`framing.py` 只做帧编解码，`ogg.py` 只做容器与 TOC 时长，`pipeline.py` 先实现每设备一个 `VoiceSession` 的状态与旧帧过滤。`DeviceSession` 增加 `send_audio()` 与二进制分派，`StarletteChannel` 增加 `send_bytes()`；设备模拟器补齐帧收发。音频数据只在内存中，不落盘、不入库、不打印。

**Tech Stack:** Python 3.11+、asyncio、FastAPI/Starlette、websockets、pytest（`asyncio_mode=auto`）；无新增第三方依赖。

**Spec:** `docs/语音机器人需求文档/v2_migration_log/23_audio_block_design.md`（§4 协议、§5 编解码、§6 VoiceSession、§14 子块 A）

## Global Constraints

- 帧头固定 12 字节大端：`0xA1`、`0x01`、kind、flags、seq、turn_seq、payload。
- `kind` 仅 `0x01` 上行麦克风 / `0x02` 下行 TTS；`flags` 仅 bit0=最后一帧、bit1=静音帧。
- 上行 16 kHz、下行 24 kHz、单声道；本块不改采样率协商。
- `turn_seq` 每轮从 0 开始；服务端丢弃同一轮内回退的帧。
- 原始音频不落盘、不入库、不写日志；日志只允许 `device_id/turn_id/kind/seq/turn_seq/len(payload)/错误码`。
- 不读取、不打印、不提交根 `local.properties` 或任何真实 Key。
- 不破坏现有 231 例测试基线；所有测试从仓库根运行。
- 测试解释器：`C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest`。
- 新增依赖：无。

## Review Focus

1. 保留位/未知 kind 的帧必须被拒绝，不能进入 ASR。
2. Ogg 页被拆成任意网络片段时，不得丢包、重包或把不完整包交给 TTS 下行。
3. 回退/重复的 `turn_seq` 不得转发；新一轮从 0 重新开始。
4. 二进制帧与 JSON 控制消息共用连接时，下行顺序与 seq 必须稳定，不能交错损坏。
5. 畸形/超长二进制帧不能让读循环崩溃，也不能错误关闭仍在正常工作的控制通道。

---

### Task 1: 音频帧编解码

**Files:**
- Create: `companion/server/app/audio/__init__.py`
- Create: `companion/server/app/audio/framing.py`
- Test: `companion/server/tests/test_audio_framing.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `FRAME_MAGIC=0xA1`、`FRAME_VERSION=0x01`、`KIND_UPLINK=0x01`、`KIND_DOWNLINK=0x02`、`FLAG_LAST=0x01`、`FLAG_SILENCE=0x02`、`FRAME_HEADER_SIZE=12`、`MAX_PAYLOAD_BYTES=16384`
  - `class FrameError(ValueError)`
  - `@dataclass(frozen=True, slots=True) class AudioFrame`，字段 `kind:int, flags:int, seq:int, turn_seq:int, payload:bytes`；属性 `last:bool`、`silence:bool`；类方法 `parse(data)->AudioFrame`；方法 `encode()->bytes`

- [ ] **Step 1: 写失败测试**

```python
# companion/server/tests/test_audio_framing.py
import pytest

from companion.server.app.audio.framing import (
    FLAG_LAST, FLAG_SILENCE, KIND_DOWNLINK, KIND_UPLINK,
    MAX_PAYLOAD_BYTES, AudioFrame, FrameError,
)

def test_frame_roundtrip_preserves_fields():
    frame = AudioFrame(KIND_UPLINK, FLAG_LAST, 7, 3, b"\x78\x01")
    parsed = AudioFrame.parse(frame.encode())
    assert parsed == frame
    assert parsed.last is True and parsed.silence is False

def test_frame_header_is_big_endian():
    raw = AudioFrame(KIND_DOWNLINK, 0, 0x01020304, 0x05060708, b"x").encode()
    assert raw[:4] == bytes([0xA1, 0x01, 0x02, 0x00])
    assert raw[4:8] == b"\x01\x02\x03\x04"
    assert raw[8:12] == b"\x05\x06\x07\x08"

@pytest.mark.parametrize("raw", [
    b"", b"\xa1\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00",
    b"\xa2\x01\x01\x00" + b"\x00" * 8, b"\xa1\x02\x01\x00" + b"\x00" * 8,
    b"\xa1\x01\x03\x00" + b"\x00" * 8, b"\xa1\x01\x01\x04" + b"\x00" * 8,
])
def test_frame_rejects_bad_header(raw):
    with pytest.raises(FrameError):
        AudioFrame.parse(raw)

def test_frame_rejects_empty_payload_without_silence():
    with pytest.raises(FrameError):
        AudioFrame.parse(b"\xa1\x01\x01\x00" + b"\x00" * 8)

def test_frame_accepts_empty_silence_payload():
    frame = AudioFrame.parse(bytes([0xA1, 0x01, 0x02, FLAG_SILENCE]) + b"\x00" * 8)
    assert frame.silence is True and frame.payload == b""

def test_frame_rejects_oversize_payload():
    raw = bytes([0xA1, 0x01, 0x01, 0x00]) + b"\x00" * 8 + b"x" * (MAX_PAYLOAD_BYTES + 1)
    with pytest.raises(FrameError):
        AudioFrame.parse(raw)
```

- [ ] **Step 2: 运行确认失败**

Run: `C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest companion/server/tests/test_audio_framing.py -q`
Expected: FAIL / collection error，`companion.server.app.audio` 不存在。

- [ ] **Step 3: 实现 `framing.py`**

`AudioFrame.parse()` 先校验长度、magic/version、kind、保留 flag 位、payload 上限；空 payload 仅允许在 `FLAG_SILENCE` 置位时。`encode()` 用 `int.to_bytes(4, "big")`。不要记录 payload。

`companion/server/app/audio/__init__.py` 保持空文件（避免导入即加载后续模块）。

- [ ] **Step 4: 运行确认通过**

Run: `... -m pytest companion/server/tests/test_audio_framing.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add companion/server/app/audio/__init__.py companion/server/app/audio/framing.py companion/server/tests/test_audio_framing.py
git commit -m "feat(companion): add the 12-byte audio frame codec"
```

---

### Task 2: 纯 Python Ogg Opus 封装 / 拆包 / 包时长

**Files:**
- Create: `companion/server/app/audio/ogg.py`
- Test: `companion/server/tests/test_audio_ogg.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `class OggError(ValueError)`
  - `ogg_crc32(data: bytes) -> int`
  - `opus_packet_duration_ms(packet: bytes) -> float | None`
  - `build_opus_head(*, channels:int=1, pre_skip:int=312, sample_rate:int=16000) -> bytes`
  - `build_opus_tags(*, vendor:bytes=b"rikkahub-companion") -> bytes`
  - `class OggOpusMuxer(sample_rate:int=16000, channels:int=1, serial:int|None=None, pre_skip:int=312, max_packets_per_page:int=1)`
    - `header_bytes() -> bytes`（OpusHead BOS 页 + OpusTags 页；幂等）
    - `push(packet: bytes, *, duration_ms: float|None=None) -> bytes`（首次自动带 header；按 `max_packets_per_page` 出页）
    - `flush() -> bytes`（输出残留包）
  - `class OggOpusDemuxer()`
    - `feed(data: bytes) -> list[bytes]`（返回完整裸 Opus 包，自动跳过 OpusHead/Tags）
    - `reset() -> None`

- [ ] **Step 1: 写失败测试**

```python
# companion/server/tests/test_audio_ogg.py
import pytest

from companion.server.app.audio.ogg import (
    OggError, OggOpusDemuxer, OggOpusMuxer, ogg_crc32,
    opus_packet_duration_ms,
)

PACKET_20MS = b"\x78" + b"\x00" * 9       # config=15 hybrid 20ms, code=0
PACKET_10MS = b"\x60" + b"\x00" * 9       # config=12 hybrid 10ms
PACKET_60MS = b"\x18" + b"\x00" * 9       # config=3 SILK 60ms

@pytest.mark.parametrize("packet,expected", [
    (PACKET_20MS, 20.0), (PACKET_10MS, 10.0), (PACKET_60MS, 60.0),
])
def test_opus_packet_duration(packet, expected):
    assert opus_packet_duration_ms(packet) == expected

def test_opus_packet_duration_rejects_empty():
    assert opus_packet_duration_ms(b"") is None

def test_muxer_header_starts_with_ogg_and_opus_head():
    muxer = OggOpusMuxer(serial=1234)
    header = muxer.header_bytes()
    assert header.startswith(b"OggS")
    assert b"OpusHead" in header and b"OpusTags" in header
    assert muxer.header_bytes() == header

def test_muxer_demuxer_roundtrip_three_packets():
    muxer = OggOpusMuxer(serial=7)
    demuxer = OggOpusDemuxer()
    packets = [PACKET_20MS, PACKET_20MS * 2, PACKET_60MS]
    out = muxer.header_bytes()
    for packet in packets:
        out += muxer.push(packet)
    assert demuxer.feed(out) == packets

def test_demuxer_accepts_one_byte_at_a_time():
    muxer = OggOpusMuxer(serial=7)
    stream = muxer.header_bytes() + muxer.push(PACKET_20MS)
    demuxer = OggOpusDemuxer()
    got = []
    for i in range(len(stream)):
        got.extend(demuxer.feed(stream[i:i + 1]))
    assert got == [PACKET_20MS]

def test_demuxer_supports_continued_packet():
    big = PACKET_20MS + b"\x00" * 600
    muxer = OggOpusMuxer(serial=7)
    stream = muxer.header_bytes() + muxer.push(big)
    assert OggOpusDemuxer().feed(stream) == [big]

def test_demuxer_rejects_crc_corruption():
    muxer = OggOpusMuxer(serial=7)
    stream = bytearray(muxer.header_bytes() + muxer.push(PACKET_20MS))
    stream[-1] ^= 0xFF
    with pytest.raises(OggError):
        OggOpusDemuxer().feed(bytes(stream))

def test_multi_packet_page_has_one_granule():
    muxer = OggOpusMuxer(serial=7, max_packets_per_page=3)
    muxer.header_bytes()
    assert muxer.push(PACKET_20MS) == b""
    assert muxer.push(PACKET_20MS) == b""
    page = muxer.push(PACKET_20MS)
    assert page.startswith(b"OggS")
    assert OggOpusDemuxer().feed(page) == [PACKET_20MS] * 3

def test_ogg_crc32_is_not_zlib():
    # 只钉住「按 Ogg 规范的 0x04C11DB7 多项式」，防止误用 zlib.crc32。
    assert ogg_crc32(b"") == 0
    assert ogg_crc32(b"OggS") != 0
```

- [ ] **Step 2: 运行确认失败**

Run: `... -m pytest companion/server/tests/test_audio_ogg.py -q`
Expected: FAIL，`audio.ogg` 不存在。

- [ ] **Step 3: 实现 `ogg.py`**

实现要点（不复制大段代码到计划）：
- CRC 使用多项式 `0x04C11DB7` 的查表实现；页 CRC 计算前把页头 22..25 置 0。
- Ogg 页头 27 字节 + lacing 表；包长度用 255 续段，最后 `<255` 结束。
- OpusHead：`b"OpusHead" + version(1) + channels + pre_skip(LE16) + sample_rate(LE32) + gain(LE16) + mapping(0)`。
- OpusTags：`b"OpusTags" + len(vendor) + vendor + 0 comments`。
- granule 以 48 kHz 计：`duration_ms * 48`。
- 拆包遇到非 `OggS` 起始、截断页、CRC 错误均抛 `OggError`；截断时保留缓冲等待下一次 `feed`。
- `max_packets_per_page` 默认 1，保证实时链路每帧即出页。

- [ ] **Step 4: 运行确认通过**

Run: `... -m pytest companion/server/tests/test_audio_ogg.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add companion/server/app/audio/ogg.py companion/server/tests/test_audio_ogg.py
git commit -m "feat(companion): add pure-python Ogg Opus muxing and demuxing"
```

---

### Task 3: 设备传输与 VoiceSession 帧门控

**Files:**
- Create: `companion/server/app/audio/pipeline.py`
- Modify: `companion/server/app/services/device_gateway.py`
- Modify: `companion/server/app/routers/ws.py`
- Test: `companion/server/tests/test_voice_transport.py`
- Test: `companion/server/tests/test_device_gateway.py`（仅给 `FakeChannel` 补 `send_bytes`）
- Test: `companion/server/tests/test_ws_router.py`（给 `FakeWebSocket` 补 `send_bytes`，并断言适配器）

**Interfaces:**
- Consumes: `AudioFrame`, `FrameError`, `KIND_UPLINK`, `KIND_DOWNLINK`, `FLAG_SILENCE`（Task 1）。
- Produces:
  - `WSChannel.send_bytes(data: bytes) -> None`
  - `StarletteChannel.send_bytes(data: bytes) -> None`（与 `send_json` 共用 `_send_lock`）
  - `DeviceSession.send_audio(payload: bytes, *, turn_seq: int, flags: int = 0) -> bool`（分配下行 seq；未 ready/closed/closing/protocol<2 返回 False；发送异常置 `closing`）
  - `DeviceSession.voice: VoiceSession | None`
  - `DeviceSession.close_voice() -> None`
  - `class VoiceSession(device_id: str, *, on_frame=None)`：
    - `state: str` ∈ `{"idle","listening","thinking","speaking","closed"}`
    - `turn_id: str | None`
    - `generation: int`
    - `frames_received: int`
    - `start(turn_id: str) -> bool`
    - `accept_frame(frame: AudioFrame) -> bool`
    - `end_user_speech() -> bool`
    - `mark_speaking() -> bool`
    - `segment_played(segment_seq: int) -> bool`
    - `cancel() -> None`
    - `close() -> None`

- [ ] **Step 1: 给测试桩补二进制能力并写失败测试**

在 `test_device_gateway.py::FakeChannel` 增加：

```python
self.sent_bytes: list[bytes] = []
async def send_bytes(self, data: bytes) -> None:
    self.sent_bytes.append(data)
    self.sent_event.set()
```

在 `test_ws_router.py::FakeWebSocket` 增加同名方法，把数据追加到 `self.sent_bytes`。

新建 `test_voice_transport.py`，复用 `test_device_gateway.py` 的握手模式，至少包含：

```python
async def test_send_audio_assigns_sequence_and_downlink_kind(gateway_session):
    session, channel = gateway_session
    assert await session.send_audio(b"a", turn_seq=0) is True
    assert await session.send_audio(b"bb", turn_seq=0, flags=FLAG_LAST) is True
    frames = [AudioFrame.parse(raw) for raw in channel.sent_bytes]
    assert [f.seq for f in frames] == [0, 1]
    assert all(f.kind == KIND_DOWNLINK and f.turn_seq == 0 for f in frames)
    assert frames[1].last is True

async def test_send_audio_refused_after_close(gateway_session):
    session, _ = gateway_session
    await session.close()
    assert await session.send_audio(b"a", turn_seq=0) is False

async def test_binary_before_voice_start_is_dropped(gateway_session):
    session, channel = gateway_session
    channel.push(AudioFrame(KIND_UPLINK, 0, 0, 0, b"x").encode())
    await asyncio.sleep(0.05)
    assert session.voice is None

async def test_voice_start_then_frames_are_gated_by_turn_seq(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    for seq in (0, 1, 0, 2):
        channel.push(AudioFrame(KIND_UPLINK, 0, seq, seq, b"x").encode())
    await wait_until(lambda: session.voice.frames_received == 3)
    assert session.voice.state == "listening"

async def test_voice_start_twice_returns_busy(gateway_session):
    _, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t2"})
    error = await channel.wait_sent(lambda m: m.get("type") == "voice.error")
    assert error["code"] == "busy"

async def test_voice_cancel_returns_ack_and_closes_gate(gateway_session):
    session, channel = gateway_session
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await wait_until(lambda: session.voice is not None)
    channel.push({"v": 2, "type": "voice.cancel", "turn_id": "t1", "reason": "barge_in"})
    ack = await channel.wait_sent(lambda m: m.get("type") == "voice.cancel_ack")
    assert ack["reason"] == "barge_in" and ack["turn_id"] == "t1"
    await wait_until(lambda: session.voice.state == "idle")

async def test_malformed_binary_does_not_kill_control_channel(gateway_session):
    _, channel = gateway_session
    channel.push(b"\xa1\x01")
    await asyncio.sleep(0.05)
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    # 控制消息仍可用
    channel.push({"v": 2, "type": "voice.start", "turn_id": "t1"})
    await channel.wait_sent(lambda m: m.get("type") == "voice.error" or m.get("type") == "voice.cancel_ack", timeout=0.2)
```

- [ ] **Step 2: 运行确认失败**

Run: `... -m pytest companion/server/tests/test_voice_transport.py -q`
Expected: FAIL，`send_audio`/`voice` 不存在。

- [ ] **Step 3: 实现**

1. `WSChannel` 加 `send_bytes`；`StarletteChannel.send_bytes` 用同一个 `_send_lock` 调 `websocket.send_bytes(data)`。
2. `DeviceSession` 增加 `_audio_seq`、`voice`、`_safe_send_bytes()`、`send_audio()`；`close()` 中调用 `close_voice()`。
3. `DeviceGateway._on_message()` 二进制分支：非 `ready`/`closing`/`closed`/`protocol_version<2` 静默丢弃；`AudioFrame.parse` 失败时记 debug（不含 payload）并回 `voice.error(code="invalid_frame", turn_id=当前轮或 "")`；`kind != KIND_UPLINK` 静默丢弃；无 `voice` 或非 `listening` 静默丢弃；否则 `await voice.accept_frame(frame)`。
4. `voice.start/end/cancel/segment_played` JSON 分支按 §4.1 规则：重复 start/未知 turn → `voice.error`；cancel 回 `voice.cancel_ack`；`voice.segment_played` 重复幂等。Block A 的 `voice.end` 只把状态置 `thinking`，不启动 LLM（Block B 接管线）。
5. `VoiceSession` 在 `accept_frame()` 中只接受 `kind==KIND_UPLINK`、`state=="listening"`、`turn_seq >= 上一帧`；回退帧直接返回 False。`start()` 在 `idle` 才成功并清空 `frames_received`/`last_turn_seq`；`cancel()` 递增 `generation` 并回 `idle`；`close()` 置 `closed` 且幂等。
6. `voice.error` 的 `code` 允许 `invalid_frame/busy/invalid_turn/unsupported_protocol`。

- [ ] **Step 4: 运行确认通过**

Run: `... -m pytest companion/server/tests/test_voice_transport.py companion/server/tests/test_device_gateway.py companion/server/tests/test_ws_router.py -q`
Expected: 全部 PASS，现有网关测试无回归。

- [ ] **Step 5: 提交**

```bash
git add companion/server/app/audio/pipeline.py companion/server/app/services/device_gateway.py companion/server/app/routers/ws.py companion/server/tests/test_voice_transport.py companion/server/tests/test_device_gateway.py companion/server/tests/test_ws_router.py
git commit -m "feat(companion): route device audio frames and add the voice session gate"
```

---

### Task 4: 协议文档补齐与模拟器帧支持

**Files:**
- Modify: `companion/docs/protocol_v2.md`
- Modify: `companion/tests/test_protocol_v2_doc.py`
- Modify: `companion/simulators/device_simulator.py`
- Test: `companion/tests/test_phase3_e2e.py`（新增 2 个音频传输用例）

**Interfaces:**
- Consumes: `AudioFrame`、`KIND_UPLINK`、`FLAG_LAST`（Task 1）；`DeviceSession.send_audio`（Task 3）。
- Produces:
  - 协议新增上行 `voice.start/voice.end/voice.cancel/voice.segment_played`，下行 `voice.segment/voice.cancel_ack/voice.error`，`voice.event` 新增 `turn_start`。
  - `SimulatedDevice.downlink_frames: list[AudioFrame]`
  - `SimulatedDevice.voice_messages: list[dict]`
  - `SimulatedDevice.send_audio(payload, *, turn_seq, flags=0) -> bool`
  - `SimulatedDevice.start_voice(turn_id) / end_voice() / cancel_voice(reason) / segment_played(seq) -> bool`

- [ ] **Step 1: 先改协议契约测试（失败）**

在 `test_protocol_declares_all_device_messages` 的消息列表加入：

```python
"voice.start", "voice.end", "voice.cancel", "voice.segment_played",
"voice.segment", "voice.cancel_ack", "voice.error", "turn_start",
```

再增加：

```python
def test_protocol_declares_voice_flow_rules():
    doc = read_protocol()
    assert "voice.segment_played" in doc
    assert "barge_in" in doc
    assert "cancel_ack" in doc
    assert "turn_seq" in doc
```

Run: `... -m pytest companion/tests/test_protocol_v2_doc.py -q`
Expected: FAIL（缺新消息）。

- [ ] **Step 2: 更新协议文档**

- §4 增加 `voice.segment` / `voice.cancel_ack` / `voice.error` 的字段表。
- §5.3 事件列表加入 `turn_start`。
- 新增 §5.4「设备语音上行」：四种 `voice.*` 消息、示例、turn 校验/幂等/旧帧规则。
- §6.2 明确：仅接受 `kind=0x01` 上行帧；`flags` 保留位必须为 0；空 payload 仅静音帧允许；畸形帧回 `voice.error(invalid_frame)` 但不关闭连接。
- §9 状态机补 `voice.start/end/cancel/segment_played` 的转换。
- 不写入任何真实 Key；音频隐私条款保持在内存。

Run: `... -m pytest companion/tests/test_protocol_v2_doc.py -q`
Expected: PASS。

- [ ] **Step 3: 模拟器支持二进制（先写 e2e 失败用例）**

在 `test_phase3_e2e.py` 末尾新增：

```python
async def test_device_audio_frames_are_gated_by_voice_start(e2e):
    device = await e2e.new_device()
    assert await device.send_audio(b"\x78\x00", turn_seq=0) is True  # 未 start：服务端丢弃
    assert await device.start_voice("turn-a") is True
    assert await device.send_audio(b"\x78\x01", turn_seq=0) is True
    assert await device.send_audio(b"\x78\x02", turn_seq=0) is True
    assert await device.cancel_voice("user_stop") is True
    ack = await device.wait_for(lambda m: m.get("type") == "voice.cancel_ack")
    assert ack["turn_id"] == "turn-a"

async def test_server_downlink_audio_reaches_the_simulator(e2e):
    device = await e2e.new_device()
    await device.start_voice("turn-b")
    link = get_device_link(device.device_id)
    assert link is not None
    assert await link.send_audio(b"\x78\x03", turn_seq=0, flags=FLAG_LAST) is True
    frame = await device.wait_for_audio(timeout=2.0)
    assert frame.kind == KIND_DOWNLINK
    assert frame.payload == b"\x78\x03"
    assert frame.last is True
```

`test_phase3_e2e.py` 顶部补导入：`from companion.server.app.audio.framing import FLAG_LAST, KIND_DOWNLINK`、`from companion.server.app.services.device_registry import get_device_link`。

Run: `... -m pytest companion/tests/test_phase3_e2e.py -q -k "audio"`
Expected: FAIL，`send_audio`/`start_voice` 不存在。

- [ ] **Step 4: 实现模拟器与 e2e 支持**

- `SimulatedDevice._receive_loop()`：`bytes` 走 `AudioFrame.parse()`，成功追加到 `downlink_frames`，失败忽略；`voice.segment`/`voice.cancel_ack`/`voice.error` 追加到 `voice_messages`。
- 新方法 `send_audio()` 用独立 `_audio_seq` 编码二进制帧并 `ws.send(bytes)`；`start_voice/end_voice/cancel_voice/segment_played` 发送对应 JSON。
- `wait_for_audio(predicate=None, timeout=5.0)` 轮询 `downlink_frames`。
- 不记录原始音频到日志；模拟器只保存测试用内存列表。

Run: `... -m pytest companion/tests/test_phase3_e2e.py -q -k "audio"`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add companion/docs/protocol_v2.md companion/tests/test_protocol_v2_doc.py companion/simulators/device_simulator.py companion/tests/test_phase3_e2e.py
git commit -m "feat(companion): document voice messages and add simulator frame support"
```

---

### Task 5: 全量验证、变异测试与过程记录

**Files:**
- Create: `companion/tests/mutation/audio_block_a.py`
- Create: `docs/语音机器人需求文档/v2_migration_log/24_audio_block_a_transport.md`
- Modify: `docs/语音机器人需求文档/v2_migration_log/README.md`

**Interfaces:**
- Consumes: 前四个任务的全部接口。
- Produces: 可复现的验证记录；无运行时接口。

- [ ] **Step 1: 运行针对性回归**

Run:

```powershell
$py="C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe"
& $py -m pytest companion/server/tests/test_audio_framing.py companion/server/tests/test_audio_ogg.py companion/server/tests/test_voice_transport.py companion/server/tests/test_ws_router.py companion/tests/test_protocol_v2_doc.py -q
& $py -m pytest companion/server/tests/test_device_gateway.py -q
& $py -m pytest companion/tests/test_phase3_e2e.py -q -k "audio"
```

Expected: 全部 PASS。

- [ ] **Step 2: 运行全量测试**

Run: `& $py -m pytest -q`
Expected: 现有 231 例 + 新增用例全部 PASS；0 failed。

- [ ] **Step 3: 写并运行 Block A 变异脚本**

`companion/tests/mutation/audio_block_a.py` 至少包含 10 个文本变异，每个变异：复制仓库到临时目录 → 替换一处源码 → 运行对应测试 → 断言退出码非 0。覆盖：

1. `framing.py` magic 改成 `0xA2`
2. `framing.py` 去掉保留 flag 位校验
3. `framing.py` 允许空 payload 无静音位
4. `framing.py` seq 改成小端
5. `ogg.py` CRC 改成 `zlib.crc32`
6. `ogg.py` 跳过 lacing 续包
7. `ogg.py` granule 按毫秒而非 48 kHz
8. `device_gateway.py` 删除 `turn_seq` 回退检查
9. `device_gateway.py` 未 `voice.start` 也把帧交给 session
10. `device_gateway.py` `send_audio` 不递增 seq

Run: `& $py companion/tests/mutation/audio_block_a.py`
Expected: `10/10 exit=1`，无挂起（脚本自身 60s 超时）。

- [ ] **Step 4: 写日志 24 并更新索引**

日志包含：改动文件、真实命令输出摘要、设计偏差、已知风险、下一步（Block B）。不包含原始音频、Key、Token、密码。README 索引从 23 补到 24。

- [ ] **Step 5: 检查工作区并提交**

Run:

```bash
git status --porcelain
git diff --stat
```

Expected: 只含本任务预期文件；无 `local.properties`、无音频二进制、无密钥。

```bash
git add companion/tests/mutation/audio_block_a.py "docs/语音机器人需求文档/v2_migration_log/24_audio_block_a_transport.md" "docs/语音机器人需求文档/v2_migration_log/README.md"
git commit -m "test(companion): verify audio block A and record the migration log"
```

---

## Self-Review

- Spec coverage：§4 协议（Task 4）、§5.1 帧（Task 1）、§5.2 Ogg（Task 2）、§6 VoiceSession 骨架（Task 3）、§14 子块 A 的模拟器与测试（Task 4/5）均有任务。
- 未纳入本计划：ASR/TTS 适配器、分句、下行节流、`voice.event` 轮次事件、取消落库、barge-in `action.stop` —— 属于 Block B/C，按设计 §14 后续计划实施。
- 类型一致性：`AudioFrame`、`VoiceSession`、`send_audio` 的签名在 Task 1/3/4 中一致；模拟器与网关都使用同一 `framing` 模块。
- 已知偏差：Block A 的 `voice.end` 只进入 `thinking`，不会产生 `no_speech`/TTS；这是为了让 Block B 接管管线时不推翻 A 的测试。
