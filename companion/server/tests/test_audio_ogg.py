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
