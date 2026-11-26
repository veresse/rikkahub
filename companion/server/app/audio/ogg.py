"""纯 Python 的 Ogg Opus 封装 / 拆包。

设备上行是裸 Opus 包，而 DashScope `fun-asr-realtime` 的 `format=opus`
要求 Ogg 封装；TTS 的 Opus 流也按 Ogg 容器解析。这里只做容器与分帧，
不做 Opus 编解码，因此不需要 `av`/`opuslib` 等原生依赖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

OGG_CAPTURE_PATTERN = b"OggS"
OGG_HEADER_SIZE = 27
OPUS_HEAD_MAGIC = b"OpusHead"
OPUS_TAGS_MAGIC = b"OpusTags"
HEADER_TYPE_CONTINUED = 0x01
HEADER_TYPE_BOS = 0x02
HEADER_TYPE_EOS = 0x04
OPUS_SAMPLE_RATE = 48000


class OggError(ValueError):
    """Ogg 容器错误；不携带包内容。"""


def _crc_table() -> list[int]:
    table = []
    for index in range(256):
        value = index << 24
        for _ in range(8):
            if value & 0x80000000:
                value = ((value << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                value = (value << 1) & 0xFFFFFFFF
        table.append(value)
    return table


_CRC_TABLE = _crc_table()


def ogg_crc32(data: bytes | bytearray | memoryview) -> int:
    """Ogg 页 CRC（多项式 0x04C11DB7），不是 zlib 的 CRC32。"""
    crc = 0
    for byte in bytes(data):
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) & 0xFF) ^ byte]
    return crc


def build_opus_head(
    *, channels: int = 1, pre_skip: int = 312, sample_rate: int = 16000
) -> bytes:
    if not (1 <= channels <= 255):
        raise OggError("invalid_channels")
    return (
        OPUS_HEAD_MAGIC
        + bytes((1, channels))
        + int(pre_skip).to_bytes(2, "little")
        + int(sample_rate).to_bytes(4, "little")
        + (0).to_bytes(2, "little")
        + bytes((0,))
    )


def build_opus_tags(*, vendor: bytes = b"rikkahub-companion") -> bytes:
    vendor_bytes = bytes(vendor)[:255]
    return (
        OPUS_TAGS_MAGIC
        + len(vendor_bytes).to_bytes(4, "little")
        + vendor_bytes
        + (0).to_bytes(4, "little")
    )


def _frame_size_ms(config: int) -> float:
    if config < 12:
        return (10, 20, 40, 60)[config & 0x03]
    if config < 16:
        return 10 if config % 2 == 0 else 20
    return (2.5, 5, 10, 20)[config & 0x03]


def opus_packet_duration_ms(packet: bytes) -> float | None:
    """按 Opus TOC 计算包时长（毫秒）；无法解析时返回 None。"""
    if not packet:
        return None
    toc = packet[0]
    config = toc >> 3
    code = toc & 0x03
    frame_ms = _frame_size_ms(config)
    if code == 0:
        frames = 1
    elif code in (1, 2):
        frames = 2
    else:
        if len(packet) < 2:
            return None
        frames = packet[1] & 0x3F
        if frames == 0:
            return None
    return float(frame_ms * frames)


@dataclass(frozen=True)
class OggPage:
    header_type: int
    granule_position: int
    serial: int
    sequence: int
    segments: tuple[bytes, ...]

    @property
    def bos(self) -> bool:
        return bool(self.header_type & HEADER_TYPE_BOS)

    @property
    def eos(self) -> bool:
        return bool(self.header_type & HEADER_TYPE_EOS)

    @property
    def continued(self) -> bool:
        return bool(self.header_type & HEADER_TYPE_CONTINUED)


def _build_page(
    header_type: int,
    granule_position: int,
    serial: int,
    sequence: int,
    packets: list[bytes],
) -> bytes:
    lacing = bytearray()
    body = bytearray()
    for packet in packets:
        remaining = len(packet)
        while remaining >= 255:
            lacing.append(255)
            remaining -= 255
        lacing.append(remaining)
        body.extend(packet)
    if len(lacing) > 255:
        raise OggError("too_many_segments")

    page = bytearray(
        OGG_CAPTURE_PATTERN
        + bytes((0, header_type))
        + int(granule_position).to_bytes(8, "little")
        + int(serial).to_bytes(4, "little")
        + int(sequence).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + bytes((len(lacing),))
        + bytes(lacing)
        + bytes(body)
    )
    page[22:26] = ogg_crc32(page).to_bytes(4, "little")
    return bytes(page)


def parse_ogg_page(data: bytes | bytearray | memoryview) -> tuple[OggPage | None, int]:
    """解析一个完整页；数据不足返回 `(None, 0)`，格式/CRC 错误抛 `OggError`。"""
    raw = bytes(data)
    if len(raw) < OGG_HEADER_SIZE:
        return None, 0
    if raw[:4] != OGG_CAPTURE_PATTERN:
        raise OggError("bad_capture_pattern")
    if raw[4] != 0:
        raise OggError("unsupported_ogg_version")

    segment_count = raw[26]
    table_end = OGG_HEADER_SIZE + segment_count
    if len(raw) < table_end:
        return None, 0
    lacing = raw[OGG_HEADER_SIZE:table_end]
    body_size = sum(lacing)
    total = table_end + body_size
    if len(raw) < total:
        return None, 0

    expected_crc = int.from_bytes(raw[22:26], "little")
    crc_page = bytearray(raw[:total])
    crc_page[22:26] = b"\x00\x00\x00\x00"
    if ogg_crc32(crc_page) != expected_crc:
        raise OggError("crc_mismatch")

    segments: list[bytes] = []
    offset = table_end
    for length in lacing:
        segments.append(raw[offset : offset + length])
        offset += length

    return (
        OggPage(
            header_type=raw[5],
            granule_position=int.from_bytes(raw[6:14], "little"),
            serial=int.from_bytes(raw[14:18], "little"),
            sequence=int.from_bytes(raw[18:22], "little"),
            segments=tuple(segments),
        ),
        total,
    )


class OggOpusMuxer:
    """把裸 Opus 包封成 Ogg 页。

    默认每包一页（实时链路首包延迟最小）；`max_packets_per_page > 1` 时
    缓冲到指定包数再出页，`flush()` 输出残留包。
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        serial: int | None = None,
        pre_skip: int = 312,
        max_packets_per_page: int = 1,
    ) -> None:
        if max_packets_per_page < 1:
            raise OggError("invalid_page_size")
        self.sample_rate = sample_rate
        self.channels = channels
        self.serial = serial if serial is not None else int.from_bytes(os.urandom(4), "big")
        self.pre_skip = pre_skip
        self.max_packets_per_page = max_packets_per_page
        self._sequence = 0
        self._granule = 0
        self._pending: list[tuple[bytes, float]] = []
        self._header_cache = b""
        self._headers_sent = False

    def header_bytes(self) -> bytes:
        if self._header_cache:
            return self._header_cache
        head_page = _build_page(
            HEADER_TYPE_BOS,
            0,
            self.serial,
            0,
            [build_opus_head(
                channels=self.channels,
                pre_skip=self.pre_skip,
                sample_rate=self.sample_rate,
            )],
        )
        tags_page = _build_page(0, 0, self.serial, 1, [build_opus_tags()])
        self._sequence = 2
        self._header_cache = head_page + tags_page
        self._headers_sent = True
        return self._header_cache

    def push(self, packet: bytes, *, duration_ms: float | None = None) -> bytes:
        if duration_ms is None:
            duration_ms = opus_packet_duration_ms(packet)
        if duration_ms is None:
            raise OggError("unknown_packet_duration")
        prefix = b""
        if not self._headers_sent:
            prefix = self.header_bytes()
            self._headers_sent = True
        self._pending.append((bytes(packet), float(duration_ms)))
        if len(self._pending) < self.max_packets_per_page:
            return prefix
        return prefix + self._emit_pending()

    def flush(self) -> bytes:
        if not self._pending:
            return b""
        prefix = b""
        if not self._headers_sent:
            prefix = self.header_bytes()
            self._headers_sent = True
        return prefix + self._emit_pending()

    def _emit_pending(self) -> bytes:
        packets = [packet for packet, _ in self._pending]
        for _, duration_ms in self._pending:
            self._granule += round(duration_ms * OPUS_SAMPLE_RATE / 1000.0)
        page = _build_page(0, self._granule, self.serial, self._sequence, packets)
        self._sequence += 1
        self._pending.clear()
        return page


class OggOpusDemuxer:
    """增量拆 Ogg 页并重组裸 Opus 包。"""

    def __init__(self) -> None:
        self._buffer = b""
        self._partial = bytearray()
        self._serial: int | None = None
        self._seen_head = False
        self._seen_tags = False

    def reset(self) -> None:
        self._buffer = b""
        self._partial.clear()
        self._serial = None
        self._seen_head = False
        self._seen_tags = False

    def feed(self, data: bytes | bytearray | memoryview) -> list[bytes]:
        self._buffer += bytes(data)
        packets: list[bytes] = []
        while True:
            page, consumed = parse_ogg_page(self._buffer)
            if page is None:
                break
            self._buffer = self._buffer[consumed:]
            if self._serial is None:
                self._serial = page.serial
            elif page.serial != self._serial:
                # 链式 Ogg 或多流复用：只处理第一条流。
                continue
            for segment in page.segments:
                self._partial.extend(segment)
                if len(segment) < 255:
                    packet = bytes(self._partial)
                    self._partial.clear()
                    if self._is_header(packet):
                        continue
                    packets.append(packet)
        return packets

    def _is_header(self, packet: bytes) -> bool:
        if not self._seen_head and packet.startswith(OPUS_HEAD_MAGIC):
            self._seen_head = True
            return True
        if self._seen_head and not self._seen_tags and packet.startswith(OPUS_TAGS_MAGIC):
            self._seen_tags = True
            return True
        return False
