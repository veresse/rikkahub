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
        [(
            "            if len(segment) < 255:\n"
            "                packet = bytes(self._partial)\n",
            "            if True:\n"
            "                packet = bytes(self._partial)\n",
        )],
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
            "        self._audio_seq = (self._audio_seq + 1) & 0xFFFFFFFF",
            "        pass",
        )],
        f"{T_VOICE}::test_send_audio_assigns_sequence_and_downlink_kind",
    ),
    (
        "partial packet spliced across a missing continuation",
        OGG,
        [(
            "            if self._partial:\n"
            "                # 上一页声明还有后续，但下一页没续：残缺包不可用。\n"
            "                self._partial.clear()\n",
            "            if False:\n"
            "                self._partial.clear()\n",
        )],
        f"{T_OGG}::test_demuxer_drops_partial_packet_when_next_page_is_not_continued",
    ),
    (
        "chained stream with a new serial dropped",
        OGG,
        [(
            "                if page.bos:\n"
            "                    self._begin_chain(page.serial)\n"
            "                else:\n"
            "                    self.dropped_pages += 1\n"
            "                    continue\n",
            "                if page.bos:\n"
            "                    self.dropped_pages += 1\n"
            "                    continue\n"
            "                else:\n"
            "                    self.dropped_pages += 1\n"
            "                    continue\n",
        )],
        f"{T_OGG}::test_demuxer_accepts_new_chain_with_a_different_serial",
    ),
    (
        "malformed frame not reported",
        DEV,
        [(
            "            await self._report_invalid_frame(session, voice)\n"
            "            return\n"
            "        if frame.kind != KIND_UPLINK:\n",
            "            return\n"
            "        if frame.kind != KIND_UPLINK:\n",
        )],
        f"{T_VOICE}::test_malformed_binary_while_listening_reports_once_and_recovers",
    ),
    (
        "duplicate voice.start rejected as busy",
        PIPELINE,
        [(
            "        if self.state == LISTENING and self.turn_id == turn_id:\n"
            "            return True\n",
            "        if False:\n"
            "            return True\n",
        )],
        f"{T_VOICE}::test_duplicate_voice_start_same_turn_is_idempotent",
    ),
    (
        "late segment_played reported as invalid_turn",
        DEV,
        [(
            "            if voice.turn_id != turn_id:\n"
            "                # 迟到的上一轮回执是幂等的，不能报错让设备回 IDLE。\n"
            "                if voice.last_turn_id == turn_id:\n"
            "                    return\n",
            "            if voice.turn_id != turn_id:\n",
        )],
        f"{T_VOICE}::test_segment_played_is_idempotent_and_late_reports_are_ignored",
    ),
    (
        "teardown skips closing the voice session",
        DEV,
        [("            await self.close_voice()\n", "            pass\n")],
        f"{T_VOICE}::test_voice_session_closed_on_disconnect",
    ),
    (
        "v1 voice messages silently dropped",
        DEV,
        [(
            "        if session._voice_protocol_error_sent:\n"
            "            return\n"
            "        session._voice_protocol_error_sent = True\n"
            "        await self._send_voice_error(\n"
            '            session, "unsupported_protocol", "", fatal=True\n'
            "        )\n",
            "        return\n",
        )],
        f"{T_VOICE}::test_voice_messages_from_v1_device_get_unsupported_protocol",
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
