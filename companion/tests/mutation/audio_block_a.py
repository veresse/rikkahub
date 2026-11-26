"""Audio Block A mutation checks: framing, Ogg container, and frame gating.

Each case rewrites one source file in place, runs the single test that should
catch it, and restores the file in `finally`. Run from the repository root with
the project venv:

    python companion/tests/mutation/audio_block_a.py

The string matches are intentionally strict: when the code changes, this script
fails loudly instead of silently mutating the wrong place.
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
FRAMING = ROOT / "companion/server/app/audio/framing.py"
OGG = ROOT / "companion/server/app/audio/ogg.py"
PIPELINE = ROOT / "companion/server/app/audio/pipeline.py"
DEV = ROOT / "companion/server/app/services/device_gateway.py"
T_FRAME = "companion/server/tests/test_audio_framing.py"
T_OGG = "companion/server/tests/test_audio_ogg.py"
T_VOICE = "companion/server/tests/test_voice_transport.py"

CASES = [
    (
        "frame magic is wrong",
        FRAMING,
        [("FRAME_MAGIC = 0xA1", "FRAME_MAGIC = 0xA2")],
        f"{T_FRAME}::test_frame_header_is_big_endian",
    ),
    (
        "reserved flag bits accepted",
        FRAMING,
        [(
            "        flags = raw[3]\n"
            "        if flags & ~_ALLOWED_FLAGS:\n"
            '            raise FrameError("reserved_flags")\n',
            "        flags = raw[3]\n",
        )],
        f"{T_FRAME}::test_frame_rejects_reserved_flags_with_payload",
    ),
    (
        "empty payload accepted without the silence flag",
        FRAMING,
        [(
            "        if not payload and not (flags & FLAG_SILENCE):\n"
            '            raise FrameError("empty_payload")\n',
            "        if not payload and not (flags & FLAG_SILENCE):\n"
            "            pass\n",
        )],
        f"{T_FRAME}::test_frame_rejects_empty_payload_without_silence",
    ),
    (
        "seq parsed as little-endian",
        FRAMING,
        [('int.from_bytes(raw[4:8], "big")', 'int.from_bytes(raw[4:8], "little")')],
        f"{T_FRAME}::test_frame_header_is_big_endian",
    ),
    (
        "Ogg page CRC not verified",
        OGG,
        [(
            "    if ogg_crc32(crc_page) != expected_crc:\n"
            '        raise OggError("crc_mismatch")\n',
            "    if False:\n"
            '        raise OggError("crc_mismatch")\n',
        )],
        f"{T_OGG}::test_demuxer_rejects_crc_corruption",
    ),
    (
        "lacing continuation ignored",
        OGG,
        [("                if len(segment) < 255:", "                if True:")],
        f"{T_OGG}::test_demuxer_supports_continued_packet",
    ),
    (
        "granule counted in milliseconds",
        OGG,
        [(
            "            self._granule += round(duration_ms * OPUS_SAMPLE_RATE / 1000.0)",
            "            self._granule += round(duration_ms)",
        )],
        f"{T_OGG}::test_muxer_granule_counts_48k_samples",
    ),
    (
        "replayed turn_seq accepted",
        PIPELINE,
        [("        if frame.turn_seq <= self._last_turn_seq:", "        if False:")],
        f"{T_VOICE}::test_voice_start_then_frames_are_gated_by_turn_seq",
    ),
    (
        "audio accepted before voice.start",
        DEV,
        [(
            '        if voice is None or voice.state != "listening":\n'
            "            return\n",
            "        if voice is None:\n"
            "            voice = VoiceSession(session.device_id)\n"
            "            session.voice = voice\n"
            '            voice.start("mutant")\n'
            '        if voice.state != "listening":\n'
            "            return\n",
        )],
        f"{T_VOICE}::test_binary_before_voice_start_is_dropped",
    ),
    (
        "downlink seq never advances",
        DEV,
        [(
            "            self._audio_seq = (self._audio_seq + 1) & 0xFFFFFFFF",
            "            pass",
        )],
        f"{T_VOICE}::test_send_audio_assigns_sequence_and_downlink_kind",
    ),
]


def apply(text: str, replacements) -> str:
    for old, new in replacements:
        assert old in text, f"pattern not found: {old[:70]!r}"
        assert text.count(old) == 1, f"pattern not unique: {old[:70]!r}"
        text = text.replace(old, new, 1)
    return text


def main() -> int:
    failures = []
    hangs = []
    for name, path, replacements, target in CASES:
        original = path.read_text(encoding="utf-8")
        mutated = apply(original, replacements)
        assert mutated != original, f"{name}: mutation did not apply"
        try:
            path.write_text(mutated, encoding="utf-8", newline="\n")
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", target],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                print(f"[{name}] TIMEOUT -> HARNESS FAILURE")
                hangs.append(name)
                continue
            tail = [line for line in proc.stdout.strip().splitlines() if line.strip()]
            print(f"[{name}] exit={proc.returncode}")
            print("\n".join(tail[-2:]))
            if proc.returncode == 0:
                failures.append(name)
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")
            assert path.read_text(encoding="utf-8") == original, (
                f"{name}: restore failed"
            )
            print(f"[{name}] restored")

    print("MUTATIONS THAT STAYED GREEN:", failures or "none")
    print("MUTATIONS THAT HUNG:", hangs or "none")
    return 1 if failures or hangs else 0


if __name__ == "__main__":
    raise SystemExit(main())
