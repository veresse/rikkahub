import pytest

from companion.server.app.audio.ogg import (
    OggError,
    OggOpusDemuxer,
    OggOpusMuxer,
    ogg_crc32,
    opus_packet_duration_ms,
)

PACKET_20MS = b"\x78" + b"\x00" * 9  # config=15 hybrid FB 20ms, code=0
PACKET_10MS = b"\x60" + b"\x00" * 9  # config=12 hybrid SWB 10ms
PACKET_60MS = b"\x18" + b"\x00" * 9  # config=3 SILK NB 60ms


@pytest.mark.parametrize(
    "packet,expected",
    [(PACKET_20MS, 20.0), (PACKET_10MS, 10.0), (PACKET_60MS, 60.0)],
)
def test_opus_packet_duration(packet, expected):
    assert opus_packet_duration_ms(packet) == expected


def test_opus_packet_duration_rejects_empty():
    assert opus_packet_duration_ms(b"") is None


def test_muxer_header_starts_with_ogg_and_opus_head():
    muxer = OggOpusMuxer(serial=1234)
    header = muxer.header_bytes()
    assert header.startswith(b"OggS")
    assert b"OpusHead" in header
    assert b"OpusTags" in header
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
        got.extend(demuxer.feed(stream[i : i + 1]))
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


def test_muxer_flush_emits_partial_page():
    muxer = OggOpusMuxer(serial=7, max_packets_per_page=3)
    muxer.header_bytes()
    assert muxer.push(PACKET_20MS) == b""
    page = muxer.flush()
    assert OggOpusDemuxer().feed(page) == [PACKET_20MS]
    assert muxer.flush() == b""


def test_muxer_requires_known_duration():
    muxer = OggOpusMuxer(serial=7)
    muxer.header_bytes()
    with pytest.raises(OggError):
        muxer.push(b"")


def test_ogg_crc32_is_not_zlib():
    assert ogg_crc32(b"") == 0
    assert ogg_crc32(b"OggS") == 0x5FB0A94F


def test_muxer_granule_counts_48k_samples():
    from companion.server.app.audio.ogg import parse_ogg_page

    muxer = OggOpusMuxer(serial=7, max_packets_per_page=3)
    muxer.header_bytes()
    muxer.push(PACKET_20MS)
    muxer.push(PACKET_20MS)
    page, _ = parse_ogg_page(muxer.push(PACKET_20MS))
    assert page is not None
    # 3 × 20 ms × 48 kHz
    assert page.granule_position == 2880


def test_demuxer_waits_for_partial_page():
    muxer = OggOpusMuxer(serial=7)
    stream = muxer.header_bytes() + muxer.push(PACKET_20MS)
    demuxer = OggOpusDemuxer()
    assert demuxer.feed(stream[:-3]) == []
    assert demuxer.feed(stream[-3:]) == [PACKET_20MS]


def _raw_page(
    segments: list[bytes],
    *,
    serial: int = 7,
    sequence: int = 2,
    header_type: int = 0,
) -> bytes:
    """构造任意 lacing 的 Ogg 页，用来模拟复用器不会产出的坏流。"""
    lacing = bytearray()
    body = bytearray()
    for segment in segments:
        lacing.append(len(segment))
        body.extend(segment)
    page = bytearray(
        b"OggS"
        + bytes((0, header_type))
        + (0).to_bytes(8, "little")
        + serial.to_bytes(4, "little")
        + sequence.to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + bytes((len(lacing),))
        + bytes(lacing)
        + bytes(body)
    )
    page[22:26] = ogg_crc32(page).to_bytes(4, "little")
    return bytes(page)


def test_demuxer_drops_partial_packet_when_next_page_is_not_continued():
    muxer = OggOpusMuxer(serial=7)
    stream = muxer.header_bytes()
    # 第 2 页声明一个 255 字节的包还有后续（lacing 255 未终结）。
    stream += _raw_page([b"A" * 255], sequence=2)
    # 第 3 页没有 CONTINUED：上一页的残缺包必须丢弃，只保留新的 45 字节包。
    stream += _raw_page([b"B" * 45], sequence=3)
    assert OggOpusDemuxer().feed(stream) == [b"B" * 45]


def test_demuxer_skips_continued_tail_after_page_gap():
    muxer = OggOpusMuxer(serial=7)
    stream = muxer.header_bytes()
    stream += _raw_page([b"A" * 255], sequence=2)
    # 跳过 sequence=3，直接收到 continued 的尾部：不能把半截包当完整包。
    stream += _raw_page(
        [b"C" * 30, b"D" * 40],
        sequence=4,
        header_type=1,
    )
    demuxer = OggOpusDemuxer()
    assert demuxer.feed(stream) == [b"D" * 40]
    assert demuxer.dropped_pages >= 1


def test_demuxer_accepts_new_chain_with_a_different_serial():
    first = OggOpusMuxer(serial=1)
    second = OggOpusMuxer(serial=2)
    packet_a = PACKET_20MS
    packet_b = PACKET_60MS
    stream = (
        first.header_bytes()
        + first.push(packet_a)
        + second.header_bytes()
        + second.push(packet_b)
    )
    demuxer = OggOpusDemuxer()
    assert demuxer.feed(stream) == [packet_a, packet_b]


def test_demuxer_recovers_after_a_corrupt_page():
    muxer = OggOpusMuxer(serial=7)
    good = muxer.push(PACKET_20MS)
    bad = bytearray(muxer.push(PACKET_60MS))
    bad[-1] ^= 0xFF
    stream = muxer.header_bytes() + bytes(bad) + good
    demuxer = OggOpusDemuxer()
    with pytest.raises(OggError):
        demuxer.feed(stream)
    # 坏页已被跳过，下一次 feed 能继续解析后面的好页。
    assert demuxer.feed(b"") == [PACKET_20MS]
    assert demuxer.crc_errors == 1


def test_demuxer_drops_duplicate_page():
    muxer = OggOpusMuxer(serial=7)
    header = muxer.header_bytes()
    page = muxer.push(PACKET_20MS)
    demuxer = OggOpusDemuxer()
    assert demuxer.feed(header + page) == [PACKET_20MS]
    assert demuxer.feed(page) == []
    assert demuxer.dropped_pages == 1


def test_muxer_finish_emits_eos_page():
    from companion.server.app.audio.ogg import parse_ogg_page

    muxer = OggOpusMuxer(serial=7)
    stream = muxer.header_bytes() + muxer.push(PACKET_20MS) + muxer.finish()
    page, _ = parse_ogg_page(stream[stream.rindex(b"OggS") :])
    assert page is not None
    assert page.eos is True
    demuxer = OggOpusDemuxer()
    assert demuxer.feed(stream) == [PACKET_20MS]
    assert demuxer.eos is True
    assert muxer.finish() == b""
    with pytest.raises(OggError):
        muxer.push(PACKET_20MS)


@pytest.mark.parametrize("packets", [0, 51])
def test_muxer_rejects_invalid_page_size(packets):
    with pytest.raises(OggError):
        OggOpusMuxer(serial=7, max_packets_per_page=packets)
