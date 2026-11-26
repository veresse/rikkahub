import pytest

from companion.server.app.audio.framing import (
    FLAG_LAST,
    FLAG_SILENCE,
    KIND_DOWNLINK,
    KIND_UPLINK,
    MAX_PAYLOAD_BYTES,
    AudioFrame,
    FrameError,
)


def test_frame_roundtrip_preserves_fields():
    frame = AudioFrame(KIND_UPLINK, FLAG_LAST, 7, 3, b"\x78\x01")
    parsed = AudioFrame.parse(frame.encode())
    assert parsed == frame
    assert parsed.last is True
    assert parsed.silence is False


def test_frame_header_is_big_endian():
    raw = AudioFrame(KIND_DOWNLINK, 0, 0x01020304, 0x05060708, b"x").encode()
    assert raw[:4] == bytes([0xA1, 0x01, 0x02, 0x00])
    assert raw[4:8] == b"\x01\x02\x03\x04"
    assert raw[8:12] == b"\x05\x06\x07\x08"

    parsed = AudioFrame.parse(raw)
    assert parsed.seq == 0x01020304
    assert parsed.turn_seq == 0x05060708


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xa1\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00",
        b"\xa2\x01\x01\x00" + b"\x00" * 8,
        b"\xa1\x02\x01\x00" + b"\x00" * 8,
        b"\xa1\x01\x03\x00" + b"\x00" * 8,
        b"\xa1\x01\x01\x04" + b"\x00" * 8,
        b"\xa1\x01\x00\x00" + b"\x00" * 8,
    ],
)
def test_frame_rejects_bad_header(raw):
    with pytest.raises(FrameError):
        AudioFrame.parse(raw)


def test_frame_rejects_empty_payload_without_silence():
    with pytest.raises(FrameError):
        AudioFrame.parse(b"\xa1\x01\x01\x00" + b"\x00" * 8)


def test_frame_rejects_reserved_flags_with_payload():
    raw = bytes([0xA1, 0x01, 0x01, 0x04]) + b"\x00" * 8 + b"x"
    with pytest.raises(FrameError):
        AudioFrame.parse(raw)


def test_frame_accepts_empty_silence_payload():
    frame = AudioFrame.parse(bytes([0xA1, 0x01, 0x02, FLAG_SILENCE]) + b"\x00" * 8)
    assert frame.silence is True
    assert frame.payload == b""


def test_frame_rejects_oversize_payload():
    raw = bytes([0xA1, 0x01, 0x01, 0x00]) + b"\x00" * 8 + b"x" * (MAX_PAYLOAD_BYTES + 1)
    with pytest.raises(FrameError):
        AudioFrame.parse(raw)


def test_frame_parse_accepts_memoryview():
    raw = bytearray(AudioFrame(KIND_UPLINK, 0, 1, 2, b"abc").encode())
    parsed = AudioFrame.parse(memoryview(raw))
    assert parsed.seq == 1
    assert parsed.turn_seq == 2
    assert parsed.payload == b"abc"
