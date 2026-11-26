"""Round 15 mutation checks: bounded application shutdown (10s + 2s + 20s drain).

Each case rewrites one source file, runs the single test that should catch it, and
restores the file in `finally`. Run from anywhere with the project venv:

    python companion/tests/mutation/round15_shutdown.py

The string matches are intentionally strict: when the code changes, this script
fails loudly instead of silently mutating the wrong place.
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
SV = ROOT / "companion/server/app/server.py"
MAIN = ROOT / "companion/server/app/main.py"
APP = ROOT / "companion/server/app/services/app_gateway.py"
DEV = ROOT / "companion/server/app/services/device_gateway.py"
SVC = ROOT / "companion/server/app/services/application_shutdown.py"
T_SERVER = "companion/server/tests/test_server.py"
T_APP = "companion/server/tests/test_app_gateway.py"
T_DEV = "companion/server/tests/test_device_gateway.py"

CASES = [
    (
        "app close in finally has no budget",
        SVC,
        [("await ws.app_gateway.shutdown(timeout=APP_CLOSE_TIMEOUT_SECONDS)",
          "await ws.app_gateway.shutdown()")],
        f"{T_SERVER}::test_application_shutdown_bounds_app_close",
    ),
    (
        "lifespan helper ignores its timeout",
        SVC,
        [("done, _ = await asyncio.wait({task}, timeout=timeout)",
          "done, _ = await asyncio.wait({task}, timeout=timeout + 5)")],
        f"{T_SERVER}::test_lifespan_shutdown_is_bounded",
    ),
    (
        "force_exit is not polled mid-window",
        SV,
        [("                if self.force_exit:\n"
          '                    logger.warning("force exit: abandoning application shutdown")\n'
          "                    break\n", "")],
        f"{T_SERVER}::test_force_exit_during_application_shutdown_abandons_wait",
    ),
    (
        "application deadline ignores the configured value",
        SV,
        [("deadline = loop.time() + APP_SHUTDOWN_TIMEOUT_SECONDS",
          "deadline = loop.time() + 30.0")],
        f"{T_SERVER}::test_application_shutdown_timeout_still_runs_uvicorn_shutdown",
    ),
    (
        "reload branch removed",
        SV,
        [("        if config.should_reload:", "        if False:")],
        f"{T_SERVER}::test_run_uses_change_reload_when_config_asks",
    ),
    (
        "object app accepted with reload",
        SV,
        [("    if config.reload and not isinstance(config.app, str):", "    if False:")],
        f"{T_SERVER}::test_run_rejects_reload_with_object_app",
    ),
    (
        "graceful drain timeout not configured",
        MAIN,
        [("        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,\n", "")],
        f"{T_SERVER}::test_build_config_sets_graceful_shutdown_timeout",
    ),
    (
        "lifespan does not reset app gateway flag",
        MAIN,
        [("    ws.app_gateway.open_for_connections()\n", "")],
        f"{T_SERVER}::test_lifespan_resets_shutdown_flags",
    ),
    (
        "cancelled device session is not put back",
        DEV,
        [("                self._sessions.add(session)\n"
          "                raise\n",
          "                raise\n")],
        f"{T_DEV}::test_shutdown_cancelled_midway_keeps_session_for_retry",
    ),
    (
        "cancelled shutdown forgets the pending offline reason",
        DEV,
        [("                if was_current and session.pending_offline_reason is None:\n"
          "                    session.pending_offline_reason = (\n"
          '                        session.close_reason or "server_shutdown"\n'
          "                    )\n", "")],
        f"{T_DEV}::test_shutdown_cancelled_after_unregister_still_publishes_offline",
    ),
    (
        "teardown cancellation drops the session",
        DEV,
        [("            if session.pending_offline_reason is not None or not session.closed:\n"
          "                self._sessions.add(session)\n", "")],
        f"{T_DEV}::test_teardown_cancelled_after_unregister_keeps_offline_pending",
    ),
    (
        "pending offline is never cleared",
        DEV,
        [("        session.pending_offline_reason = None\n"
          "        if get_device_link(session.device_id) is not None:\n",
          "        if get_device_link(session.device_id) is not None:\n")],
        f"{T_DEV}::test_pending_offline_not_republished_by_concurrent_teardown",
    ),
    (
        "app gateway deadline ignores its timeout",
        APP,
        [("deadline = None if timeout is None else loop.time() + timeout",
          "deadline = None if timeout is None else loop.time() + timeout * 100")],
        f"{T_APP}::test_shutdown_deadline_does_not_wait_for_hanging_app_close",
    ),
    (
        "app gateway accepts connections during shutdown",
        APP,
        [("        if self._shutting_down:\n"
          "            # 停机已开始：新连接不能进入广播集合（否则会被 uvicorn 以 1012 收尾，\n"
          "            # 而不是协议承诺的 1011）。检查与下面的注册之间没有 await，因此\n"
          "            # 与 `shutdown()` 置标志之间不存在“检查通过后才注册”的窗口。\n"
          "            try:\n"
          '                await channel.close(1011, "server_shutdown")\n'
          "            except Exception:\n"
          '                logger.debug("app close failed during shutdown: %s", token_id, exc_info=True)\n'
          "            return\n", "")],
        f"{T_APP}::test_new_connection_during_shutdown_is_closed_with_1011",
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
                    timeout=180,
                )
            except subprocess.TimeoutExpired:
                # 挂起不是干净的 RED：说明用例失去了快速失败保护，
                # 单独计数并以非零退出，避免掩盖“测试根本没跑完”。
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
