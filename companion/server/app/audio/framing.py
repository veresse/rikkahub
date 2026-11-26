"""`protocol_v2.md` §6.1 的 12 字节音频帧编解码。

本模块只处理容器与字段校验，不做 Opus 编解码。`payload` 是裸 Opus 包，
服务端与固件都不应在日志里输出它。
"""

from __future__ import annotations

from dataclasses import dataclass

FRAME_MAGIC = 0xA1
FRAME_VERSION = 0x01
KIND_UPLINK = 0x01
KIND_DOWNLINK = 0x02
FLAG_LAST = 0x01
FLAG_SILENCE = 0x02
FRAME_HEADER_SIZE = 12
MAX_PAYLOAD_BYTES = 16 * 1024

_ALLOWED_FLAGS = FLAG_LAST | FLAG_SILENCE
_ALLOWED_KINDS = {KIND_UPLINK, KIND_DOWNLINK}


class FrameError(ValueError):
    """二进制帧格式错误；错误码只用于日志，不携带 payload。"""


@dataclass(frozen=True, slots=True)
class AudioFrame:
    kind: int
    flags: int
    seq: int
    turn_seq: int
    payload: bytes = b""

    @property
    def last(self) -> bool:
        return bool(self.flags & FLAG_LAST)

    @property
    def silence(self) -> bool:
        return bool(self.flags & FLAG_SILENCE)

    @classmethod
    def parse(cls, data: bytes | bytearray | memoryview) -> "AudioFrame":
        raw = bytes(data)
        if len(raw) < FRAME_HEADER_SIZE:
            raise FrameError("short_frame")
        if raw[0] != FRAME_MAGIC:
            raise FrameError("bad_magic")
        if raw[1] != FRAME_VERSION:
            raise FrameError("bad_version")

        kind = raw[2]
        if kind not in _ALLOWED_KINDS:
            raise FrameError("unknown_kind")

        flags = raw[3]
        if flags & ~_ALLOWED_FLAGS:
            raise FrameError("reserved_flags")

        payload = raw[FRAME_HEADER_SIZE:]
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise FrameError("payload_too_large")
        if not payload and not (flags & FLAG_SILENCE):
            raise FrameError("empty_payload")

        return cls(
            kind=kind,
            flags=flags,
            seq=int.from_bytes(raw[4:8], "big"),
            turn_seq=int.from_bytes(raw[8:12], "big"),
            payload=payload,
        )

    def encode(self) -> bytes:
        if self.kind not in _ALLOWED_KINDS:
            raise FrameError("unknown_kind")
        if self.flags & ~_ALLOWED_FLAGS:
            raise FrameError("reserved_flags")
        try:
            payload = bytes(self.payload)
        except (TypeError, ValueError) as exc:
            raise FrameError("invalid_payload") from exc
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise FrameError("payload_too_large")
        if not payload and not (self.flags & FLAG_SILENCE):
            raise FrameError("empty_payload")
        if not 0 <= int(self.seq) <= 0xFFFFFFFF:
            raise FrameError("seq_out_of_range")
        if not 0 <= int(self.turn_seq) <= 0xFFFFFFFF:
            raise FrameError("turn_seq_out_of_range")
        return (
            bytes((FRAME_MAGIC, FRAME_VERSION, self.kind, self.flags))
            + int(self.seq).to_bytes(4, "big")
            + int(self.turn_seq).to_bytes(4, "big")
            + payload
        )
