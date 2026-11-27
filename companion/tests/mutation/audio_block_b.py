"""Audio Block B mutation checks: segmentation, downlink, pipeline, persistence.

Each case rewrites one source file in place, runs the single test that should
catch it, and restores the file in `finally`. Run from the repository root with
the project venv:

    python companion/tests/mutation/audio_block_b.py

The string matches are intentionally strict: when the code changes, this script
fails loudly instead of silently mutating the wrong place.
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
SENTENCES = ROOT / "companion/server/app/audio/sentences.py"
DOWNLINK = ROOT / "companion/server/app/audio/downlink.py"
PIPELINE = ROOT / "companion/server/app/audio/pipeline.py"
ORCHESTRATOR = ROOT / "companion/server/app/services/orchestrator.py"
ACTION = ROOT / "companion/server/app/services/action_orchestrator.py"
BINDING = ROOT / "companion/server/app/audio/binding.py"
DEV = ROOT / "companion/server/app/services/device_gateway.py"

T_SENT = "companion/server/tests/test_audio_sentences.py"
T_DOWN = "companion/server/tests/test_audio_downlink.py"
T_PIPE = "companion/server/tests/test_audio_pipeline.py"
T_CANCEL = "companion/server/tests/test_chat_cancel.py"
T_ACTION = "companion/server/tests/test_action_orchestrator.py"
T_BIND = "companion/server/tests/test_audio_binding.py"
T_TRANSPORT = "companion/server/tests/test_voice_transport.py"

CASES = [
    (
        "flush drops the remaining sentence",
        SENTENCES,
        [(
            "        piece = self._buffer.strip()\n"
            '        self._buffer = ""\n'
            "        if self._is_meaningful(piece):\n"
            "            return [piece]\n"
            "        return []\n",
            "        piece = self._buffer.strip()\n"
            '        self._buffer = ""\n'
            "        if False:\n"
            "            return [piece]\n"
            "        return []\n",
        )],
        f"{T_SENT}::test_flush_returns_remaining_text_only_once",
    ),
    (
        "max_chars never hard cuts",
        SENTENCES,
        [(
            "        while len(self._buffer) > self.max_chars:",
            "        while False:",
        )],
        f"{T_SENT}::test_max_chars_hard_cut_without_separator",
    ),
    (
        "FLAG_LAST never set on the last packet",
        DOWNLINK,
        [(
            "        flags = FLAG_LAST if item.last_in_turn else 0",
            "        flags = 0",
        )],
        f"{T_DOWN}::test_segment_precedes_its_audio_and_turn_seq_is_continuous",
    ),
    (
        "turn_seq never advances",
        DOWNLINK,
        [(
            "        self._turn_seq = (self._turn_seq + 1) & 0xFFFFFFFF",
            "        pass",
        )],
        f"{T_DOWN}::test_segment_precedes_its_audio_and_turn_seq_is_continuous",
    ),
    (
        "segment metadata is sent after its audio",
        DOWNLINK,
        [(
            "        if segment is None:\n"
            "            ok = await self._sink.send_audio(\n"
            "                item.payload, turn_seq=self._turn_seq, flags=flags\n"
            "            )\n"
            "        else:\n"
            "            ok = await self._sink.send_segment(\n"
            "                segment.to_payload(),\n"
            "                item.payload,\n"
            "                turn_seq=self._turn_seq,\n"
            "                flags=flags,\n"
            "            )\n"
            "            if ok:\n"
            "                self.last_sent_segment = segment.segment_seq\n",
            "        ok = await self._sink.send_audio(\n"
            "            item.payload, turn_seq=self._turn_seq, flags=flags\n"
            "        )\n"
            "        if segment is not None and ok:\n"
            "            ok = await self._sink.send_control(segment.to_payload())\n"
            "            if ok:\n"
            "                self.last_sent_segment = segment.segment_seq\n",
        )],
        f"{T_DOWN}::test_segment_precedes_its_audio_and_turn_seq_is_continuous",
    ),
    (
        "cancel does not bump the generation",
        PIPELINE,
        [(
            "        gen = self.generation\n"
            "        turn_id = self.turn_id\n"
            "        self.generation += 1\n"
            "        self.state = IDLE\n"
            "        self.last_turn_id = turn_id\n"
            "        self.turn_id = None\n"
            '        self.last_finish_reason = "interrupted"\n',
            "        gen = self.generation\n"
            "        turn_id = self.turn_id\n"
            "        self.state = IDLE\n"
            "        self.last_turn_id = turn_id\n"
            "        self.turn_id = None\n"
            '        self.last_finish_reason = "interrupted"\n',
        )],
        f"{T_PIPE}::test_cancel_turn_marks_interrupted_and_drops_late_segments",
    ),
    (
        "playback watchdog is never started",
        PIPELINE,
        [(
            "        self._spawn_timer(self._playback_watchdog(gen, turn_id))",
            "        pass",
        )],
        f"{T_PIPE}::test_missing_playback_report_times_out",
    ),
    (
        "last segment report never completes the turn",
        PIPELINE,
        [(
            "        if self.last_segment_seq is not None and segment_seq >= self.last_segment_seq:\n"
            '            await self._complete_turn(gen, turn_id, "stop")\n',
            "        if self.last_segment_seq is not None and segment_seq > self.last_segment_seq:\n"
            '            await self._complete_turn(gen, turn_id, "stop")\n',
        )],
        f"{T_PIPE}::test_normal_turn_streams_two_segments_and_completes_on_last_played",
    ),
    (
        "cancelled turn is persisted without interrupted",
        ORCHESTRATOR,
        [(
            "                **kwargs,\n"
            "                interrupted=True,\n"
            '                finish_reason="interrupted",\n',
            "                **kwargs,\n"
            "                interrupted=False,\n"
            '                finish_reason="interrupted",\n',
        )],
        f"{T_CANCEL}::test_cancel_persists_partial_turn_as_interrupted",
    ),
    (
        "action.stop always uses server_cancel",
        ACTION,
        [(
            '        timeout_ms = int(action["max_duration_ms"]) + settings.ACTION_RESULT_TIMEOUT_EXTRA_MS\n'
            "        return await self._submit(\n"
            "            device_id,\n"
            "            command,\n"
            "            timeout_ms=timeout_ms,\n"
            "            cancel_reason=turn.cancel_reason,\n"
            "        )\n",
            '        timeout_ms = int(action["max_duration_ms"]) + settings.ACTION_RESULT_TIMEOUT_EXTRA_MS\n'
            "        return await self._submit(\n"
            "            device_id,\n"
            "            command,\n"
            "            timeout_ms=timeout_ms,\n"
            '            cancel_reason="server_cancel",\n'
            "        )\n",
        )],
        f"{T_ACTION}::test_cancel_reason_is_forwarded_to_action_stop",
    ),
    (
        "assistant fallback picks the oldest conversation",
        BINDING,
        [(
            "        .order_by(ConversationModel.updated_at.desc())",
            "        .order_by(ConversationModel.updated_at.asc())",
        )],
        f"{T_BIND}::test_assistant_falls_back_to_most_recent_conversation",
    ),
    (
        "bound conversation is ignored",
        BINDING,
        [(
            "        if conversation is not None:\n"
            "            return conversation.id\n",
            "        if False:\n"
            "            return conversation.id\n",
        )],
        f"{T_BIND}::test_bound_conversation_wins_over_assistant",
    ),
    (
        "voice.cancel is not acked",
        DEV,
        [(
            "            if session.voice_runtime is not None:\n"
            "                # 保留设备上报的原因（barge_in/user_stop）；Block B 的\n"
            "                # action.stop(barge_in) 由被取消的聊天生成器发出。\n"
            "                voice.cancel_turn(reason)\n"
            "            else:\n"
            "                voice.cancel()\n"
            "            await self._send_cancel_ack(session, turn_id, reason)\n"
            "            return\n",
            "            if session.voice_runtime is not None:\n"
            "                voice.cancel_turn(reason)\n"
            "            else:\n"
            "                voice.cancel()\n"
            "            return\n",
        )],
        f"{T_TRANSPORT}::test_voice_cancel_returns_ack_and_returns_idle",
    ),
    (
        "late voice.cancel is not acked again",
        DEV,
        [(
            "                if voice.last_turn_id == turn_id:\n"
            "                    # 设备没收到 ack 后重试：幂等，再发一次即可。\n"
            "                    await self._send_cancel_ack(session, turn_id, reason)\n"
            "                    return\n",
            "                if voice.last_turn_id == turn_id:\n"
            "                    return\n",
        )],
        f"{T_TRANSPORT}::test_late_cancel_for_finished_turn_is_acked_again",
    ),
    (
        "old turn cleanup cancels the next turn speaker",
        PIPELINE,
        [(
            "            if not speaker_task.done():\n"
            "                speaker_task.cancel()\n"
            "                await asyncio.gather(speaker_task, return_exceptions=True)\n"
            "            if self._speaker_task is speaker_task:\n"
            "                self._speaker_task = None\n",
            "            if self._speaker_task is not None and not self._speaker_task.done():\n"
            "                self._speaker_task.cancel()\n"
            "                await asyncio.gather(self._speaker_task, return_exceptions=True)\n"
            "            self._speaker_task = None\n",
        )],
        f"{T_PIPE}::test_cancel_in_thinking_does_not_cancel_the_next_turn_speaker",
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

    print(f"CASES: {len(CASES)}")
    print("MUTATIONS THAT STAYED GREEN:", failures or "none")
    print("MUTATIONS THAT HUNG:", hangs or "none")
    return 1 if failures or hangs else 0


if __name__ == "__main__":
    raise SystemExit(main())
