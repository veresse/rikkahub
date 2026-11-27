"""Audio Block C mutation checks: DashScope ASR/TTS and runtime wiring.

Each case rewrites one source file in place, runs the single test that should
catch it, and restores the file in `finally`. Run from the repository root with
the project venv:

    python companion/tests/mutation/audio_block_c.py

Do not run concurrently with pytest: it temporarily edits source files.
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
DASHSCOPE = ROOT / "companion/server/app/audio/dashscope.py"
BINDING = ROOT / "companion/server/app/audio/binding.py"
SMOKE = ROOT / "companion/scripts/voice_dashscope_smoke.py"

T_ASR = "companion/server/tests/test_dashscope_asr.py"
T_TTS = "companion/server/tests/test_dashscope_tts.py"
T_BIND = "companion/server/tests/test_audio_binding.py"
T_SMOKE = "companion/server/tests/test_voice_smoke_script.py"

CASES = [
    (
        "asr does not send finish-task",
        DASHSCOPE,
        [(
            '                                "action": "finish-task",\n',
            '                                "action": "finish-task-disabled",\n',
        )],
        f"{T_ASR}::test_finish_sends_finish_task_once",
    ),
    (
        "asr emits final on every sentence_end",
        DASHSCOPE,
        [(
            "        if sentence.get(\"sentence_end\"):\n"
            "            self._sentences.append(text)\n"
            "            self._current = \"\"\n",
            "        if sentence.get(\"sentence_end\"):\n"
            "            self._sentences.append(text)\n"
            "            self._current = \"\"\n"
            "            self._emit_final()\n",
        )],
        f"{T_ASR}::test_partial_then_single_final_with_accumulated_text",
    ),
    (
        "tts does not request SSE",
        DASHSCOPE,
        [(
            '            "X-DashScope-SSE": "enable",\n',
            "",
        )],
        f"{T_TTS}::test_streams_base64_chunks_and_sends_expected_request",
    ),
    (
        "tts accepts non-stream audio url",
        DASHSCOPE,
        [(
            "                if audio.get(\"url\"):\n"
            "                    # 非流式返回：不做二次 HTTP 下载，按设计明确报错。\n"
            "                    logger.debug(\"dashscope tts returned audio url: %s\", turn_id)\n"
            "                    raise ProviderError(TTS_UNAVAILABLE)\n",
            "",
        )],
        f"{T_TTS}::test_audio_url_is_rejected_without_downloading",
    ),
    (
        "binding ignores configured api key",
        BINDING,
        [(
            '        if not (settings.DASHSCOPE_API_KEY or "").strip():\n',
            "        if True:\n",
        )],
        f"{T_BIND}::test_runtime_dashscope_with_key_builds_adapters",
    ),
    (
        "asr does not recheck closed after handshake",
        DASHSCOPE,
        [(
            "        if self._closed:\n"
            "            # close() 可能发生在握手期间：此时不能再把连接装进实例，否则\n",
            "        if False:\n"
            "            # close() 可能发生在握手期间：此时不能再把连接装进实例，否则\n",
        )],
        f"{T_ASR}::test_close_during_connect_closes_the_socket",
    ),
    (
        "smoke script counts pages per chunk",
        SMOKE,
        [(
            "        counter.feed(chunk)\n",
            "        counter.pages += _count_ogg_pages(chunk)\n",
        )],
        f"{T_SMOKE}::test_run_tts_counts_pages_across_chunks",
    ),
    (
        "asr connects again after close while queueing",
        DASHSCOPE,
        [(
            "        async with self._connect_lock:\n"
            "            if self._ws is not None:\n"
            "                return\n"
            "            if self._closed:\n"
            "                # 排队等锁期间可能已被 close()，不能再发起新的连接。\n"
            "                raise ProviderError(ASR_UNAVAILABLE)\n",
            "        async with self._connect_lock:\n"
            "            if self._ws is not None:\n"
            "                return\n",
        ), (
            "        if self._closed or not self._api_key:\n",
            "        if not self._api_key:\n",
        )],
        f"{T_ASR}::test_close_during_handshake_only_connects_once",
    ),
    (
        "smoke script drops configured region",
        SMOKE,
        [(
            "        region=settings.DASHSCOPE_REGION,\n",
            "",
        )],
        f"{T_SMOKE}::test_run_asr_passes_configured_region",
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
            tail = [
                line
                for line in proc.stdout.strip().splitlines()
                if line.strip()
            ]
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
