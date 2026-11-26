"""Phase 3 端到端测试：真实 uvicorn + 真实 WebSocket + 双模拟器。

覆盖（对应 `docs/语音机器人需求文档/v1_v2.md` 3.3 与 Phase 3）：
- 设备 v2 握手 / v1 降级
- 18 个动作的下发 → 执行 → `action.result` 闭环
- 双源仲裁：`volume.set` 后到覆盖、`action.play` FIFO、`action.stop` 抢占
- 服务端执行超时强停（`action.stop(server_cancel)`）
- 设备离线不补发、重连后不执行历史指令
- App Token / 设备 Token 撤销后 4401 断连
- LLM 工具循环：聊天 SSE → robot.action → 设备回执 → 模型续说
- 指令风暴限速、日志不泄露 Token/密码

所有断言都走真实 TCP/WebSocket，不使用 FakeChannel；LLM 用进程内 Mock，
不需要任何云密钥。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn

from companion.server.app.config import settings
from companion.server.app.main import app as fastapi_app
from companion.simulators.app_simulator import SimulatedApp
from companion.simulators.device_simulator import SimulatedDevice
from companion.shared.catalog import get_action_names

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "test-admin-password")
ALL_ACTIONS = get_action_names()


# ---------------------------------------------------------------------------
# 真实 uvicorn（独立线程 + 独立事件循环）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_server():
    """在后台线程启动真实 uvicorn，测试通过 127.0.0.1 的 TCP/WS 访问。"""
    config = uvicorn.Config(
        app=fastapi_app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        log_config=None,
        access_log=False,
        lifespan="on",
        ws_max_size=settings.WS_TRANSPORT_MAX_MESSAGE_BYTES,
        ws_ping_interval=None,
        ws_ping_timeout=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn-phase3", daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("uvicorn 启动线程提前退出")
        time.sleep(0.02)
    assert server.started, "uvicorn 未能在 20 秒内启动"

    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=15)
    assert not thread.is_alive(), "uvicorn 未能在 15 秒内退出"


# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


class ScriptedDeepSeek:
    """按脚本逐轮产出 chunk；记录每轮 tools 是否注入。"""

    def __init__(self, rounds):
        self.rounds = rounds
        self.calls = []

    async def stream_chat(
        self, messages, model=None, temperature=None, top_p=None, tools=None
    ):
        self.calls.append(
            {
                "messages": json.loads(json.dumps(messages, default=str)),
                "tools": tools,
            }
        )
        chunks = (
            self.rounds.pop(0)
            if self.rounds
            else [{"type": "content", "delta": ""}, {"type": "done"}]
        )
        for chunk in chunks:
            yield chunk


def content(text: str) -> dict:
    return {"type": "content", "delta": text}


def tool_call(call_id: str, name: str, arguments: str) -> dict:
    return {
        "type": "tool_call",
        "delta": [
            {
                "index": 0,
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


# ---------------------------------------------------------------------------
# 测试环境
# ---------------------------------------------------------------------------


class E2EContext:
    def __init__(self, app: SimulatedApp):
        self.app = app
        self.devices: list[SimulatedDevice] = []
        self.device_ids: list[str] = []
        self.assistant_ids: list[str] = []
        self.conversation_ids: list[str] = []
        self.extra_apps: list[SimulatedApp] = []

    async def login_other_app(self) -> SimulatedApp:
        other = SimulatedApp(self.app.base_url)
        await other.__aenter__()
        # 先登记再登录：登录失败时 cleanup 也能关闭底层 httpx client。
        self.extra_apps.append(other)
        await other.login(password=ADMIN_PASSWORD)
        return other

    async def connect_app(self, app: SimulatedApp | None = None) -> SimulatedApp:
        """连接 `/app/ws` 并用一次黑盒往返确认服务端已注册会话。

        `websockets.connect()` 收到 101 就返回，服务端可能还没执行完
        `AppGateway.handle()`；广播只投递给已注册会话且不补发。用一个不存在
        的 device_id 发 `remote.get_status` 会得到 `device_offline` ack：既能
        证明会话已挂载，又不依赖进程内私有状态，对远端服务器同样适用。
        """
        target = app or self.app
        if target.ws is None:
            await target.connect_ws()

        ack = await target.remote("remote.get_status", "__readiness_probe__")
        assert ack["type"] == "remote.ack", ack
        assert ack["ok"] is False, ack
        assert ack["error"] == "device_offline", ack
        return target

    async def new_assistant(self, name: str = "E2E 角色") -> str:
        payload = {
            "name": name,
            "system_prompt": "你是端到端测试角色。",
            "enable_web_search": False,
            "enable_action_sync": True,
        }
        resp = await self.app.post("/api/assistants", json=payload)
        assert resp.status_code == 200, resp.text
        assistant_id = resp.json()["id"]
        self.assistant_ids.append(assistant_id)
        return assistant_id

    async def new_conversation(self, assistant_id: str, title: str = "E2E") -> str:
        resp = await self.app.post(
            "/api/conversations", json={"assistant_id": assistant_id, "title": title}
        )
        assert resp.status_code == 200, resp.text
        conversation_id = resp.json()["id"]
        self.conversation_ids.append(conversation_id)
        return conversation_id

    async def new_device(
        self,
        *,
        name: str = "E2E 机器人",
        protocol_version: int = 2,
        capabilities=None,
        time_scale: float = 0.01,
        auto_complete: bool = True,
        assistant_id: str | None = None,
        conversation_id: str | None = None,
        connect: bool = True,
    ) -> SimulatedDevice:
        caps = capabilities
        if caps is None:
            caps = ["action", "volume", "expression"]
        code_resp = await self.app.post(
            "/api/devices/pair-code",
            json={
                "assistant_id": assistant_id,
                "conversation_id": conversation_id,
            },
        )
        assert code_resp.status_code == 200, code_resp.text
        code = code_resp.json()["code"]

        pair_resp = await self.app.post(
            "/api/devices/pair",
            json={
                "code": code,
                "device_name": name,
                "firmware_version": "2.0.0",
                "protocol_version": protocol_version,
                "capabilities": caps,
            },
        )
        assert pair_resp.status_code == 200, pair_resp.text
        paired = pair_resp.json()
        device = SimulatedDevice(
            self.app.base_url,
            device_id=paired["device_id"],
            token=paired["device_token"],
            protocol_version=protocol_version,
            capabilities=caps,
            time_scale=time_scale,
            auto_complete=auto_complete,
        )
        self.devices.append(device)
        self.device_ids.append(paired["device_id"])
        if connect:
            await device.connect()
            assert device.hello_ack is not None, (
                f"设备握手失败：close_code={device.close_code} "
                f"reason={device.close_reason}"
            )
        return device

    async def cleanup(self) -> None:
        for device in self.devices:
            try:
                await device.close()
            except Exception:
                pass
        for app in self.extra_apps:
            try:
                await app.close_ws()
            except Exception:
                pass
            try:
                await app.__aexit__(None, None, None)
            except Exception:
                pass
        for device_id in self.device_ids:
            try:
                await self.app.delete(f"/api/devices/{device_id}")
            except Exception:
                pass
        for conversation_id in self.conversation_ids:
            try:
                await self.app.delete(f"/api/conversations/{conversation_id}")
            except Exception:
                pass
        for assistant_id in self.assistant_ids:
            try:
                await self.app.delete(f"/api/assistants/{assistant_id}")
            except Exception:
                pass
        await self.app.close_ws()
        await self.app.__aexit__(None, None, None)


@pytest.fixture
async def e2e(live_server):
    app = SimulatedApp(live_server)
    await app.__aenter__()
    await app.login(password=ADMIN_PASSWORD)
    ctx = E2EContext(app)
    try:
        yield ctx
    finally:
        await ctx.cleanup()


async def expect_result(app: SimulatedApp, action_id: str, timeout: float = 5.0):
    return await app.wait_for(
        lambda e: e.get("type") == "action.result"
        and e.get("action_id") == action_id,
        timeout=timeout,
    )


def _results_for(app: SimulatedApp, action_id: str) -> list[dict]:
    return [
        e
        for e in app.events
        if e.get("type") == "action.result" and e.get("action_id") == action_id
    ]


# ---------------------------------------------------------------------------
# 握手与降级
# ---------------------------------------------------------------------------


async def test_device_v2_handshake_contract(e2e):
    await e2e.connect_app()
    device = await e2e.new_device()

    ack = device.hello_ack
    assert ack is not None
    assert ack["v"] == 2
    assert ack["protocol_version"] == 2
    assert set(ack["capabilities"]) == {"action", "volume", "expression"}
    assert ack["action_catalog_version"] == "2.0.0"
    assert ack["limits"]["pan"] == [-90.0, 90.0]
    assert ack["limits"]["tilt"] == [-20.0, 45.0]
    assert ack["limits"]["default_max_duration_ms"] == 3000
    assert ack["limits"]["hard_max_duration_ms"] == 5000
    assert ack["limits"]["rate_limit_per_second"] == 10
    assert ack["warnings"] == []
    assert ack["session_id"]

    # 连接成功后设备主动上报一次，App 应看到 device.report。
    report = await e2e.app.wait_for(
        lambda e: e.get("type") == "device.report"
        and e.get("device_id") == device.device_id
        and e.get("volume") == 70,
        timeout=5.0,
    )
    assert report["state"] == "idle"


async def test_v1_device_degrades_and_rejects_remote_commands(e2e):
    device = await e2e.new_device(protocol_version=1, name="E2E v1 设备")

    ack = device.hello_ack
    assert ack["protocol_version"] == 1
    assert ack["capabilities"] == []

    await e2e.connect_app()
    resp = await e2e.app.remote(
        "remote.action.play", device.device_id, name="nod", intensity=2
    )
    assert resp["ok"] is False
    assert resp["error"] == "unsupported_protocol"

    # v1 设备只走音频通道，绝不能收到 v2 控制消息。
    # 用一次优雅关连接作屏障：服务端在关闭帧之前写下的任何帧都会先被接收循环
    # 读入 `commands`，因此关闭后断言不存在时序窗口。
    await device.close()
    assert device.commands_of_type("action.play") == []
    assert device.received == [], device.received


# ---------------------------------------------------------------------------
# 18 个动作闭环
# ---------------------------------------------------------------------------


async def test_all_18_actions_closed_loop(e2e):
    device = await e2e.new_device(name="E2E 全动作")
    await e2e.connect_app()
    assert len(ALL_ACTIONS) == 18

    seen: list[str] = []
    for name in ALL_ACTIONS:
        ack = await e2e.app.remote("remote.action.play", device.device_id, name=name)
        assert ack["ok"] is True, (name, ack)
        action_id = ack["action_id"]
        result = await expect_result(e2e.app, action_id)
        assert result["status"] == "done", (name, result)
        assert result["source"] == "app"
        seen.append(name)
        # 尊重 App 每连接 10 条/秒限速，避免测试自身触发 rate_limited。
        await asyncio.sleep(0.12)

    assert seen == ALL_ACTIONS
    assert [c["name"] for c in device.commands_of_type("action.play")] == ALL_ACTIONS

    # 抽查几个动作的最终姿态（intensity 默认 2 的缩放）。
    assert device.last_pose_for("look_left") == pytest.approx((-45.0, 0.0), abs=1e-6)
    assert device.last_pose_for("look_right") == pytest.approx((45.0, 0.0), abs=1e-6)
    assert device.last_pose_for("look_up") == pytest.approx((0.0, -14.0), abs=1e-6)
    assert device.last_pose_for("look_down") == pytest.approx((0.0, 30.15), abs=1e-6)
    assert device.last_pose_for("sleep") == pytest.approx((0.0, 35.0), abs=1e-6)
    assert device.last_pose_for("home") == pytest.approx((0.0, 0.0), abs=1e-6)


# ---------------------------------------------------------------------------
# 双源仲裁
# ---------------------------------------------------------------------------


async def _send_remote_no_wait(app, message_type: str, device_id: str, **fields) -> str:
    request_id = str(uuid.uuid4())
    await app.send(
        {
            "v": 2,
            "type": message_type,
            "request_id": request_id,
            "device_id": device_id,
            **fields,
        }
    )
    return request_id


async def _wait_ack(app, request_id: str, timeout: float = 5.0) -> dict:
    return await app.wait_for(
        lambda e: e.get("type") == "remote.ack" and e.get("request_id") == request_id,
        timeout=timeout,
    )


async def test_volume_last_write_wins_and_status_snapshot(e2e):
    # 用永不完成的动作占住 worker，才能让两条 volume.set 真正同时排队。
    device = await e2e.new_device(name="E2E 音量", auto_complete=False)
    await e2e.connect_app()

    busy = await e2e.app.remote("remote.action.play", device.device_id, name="nod")
    assert busy["ok"] is True
    await device.wait_for(
        lambda e: e.get("type") == "action.play"
        and e.get("action_id") == busy["action_id"],
        timeout=5.0,
    )

    first_req = await _send_remote_no_wait(
        e2e.app, "remote.volume.set", device.device_id, volume=30
    )
    second_req = await _send_remote_no_wait(
        e2e.app, "remote.volume.set", device.device_id, volume=65
    )
    first = await _wait_ack(e2e.app, first_req)
    second = await _wait_ack(e2e.app, second_req)
    assert first["ok"] is True and second["ok"] is True
    assert first["action_id"] != second["action_id"]

    # 后到的 volume.set 覆盖仍在排队的前一条：前者 superseded，且从未下发。
    first_result = await expect_result(e2e.app, first["action_id"])
    assert (first_result["status"], first_result["detail"]) == ("failed", "superseded")
    assert len(_results_for(e2e.app, first["action_id"])) == 1
    assert device.commands_of_type("volume.set") == []

    device.release()
    second_result = await expect_result(e2e.app, second["action_id"])
    assert (second_result["status"], second_result["detail"]) == ("done", "")
    assert len(_results_for(e2e.app, second["action_id"])) == 1
    assert device.volume == 65

    # 先等设备侧上报把快照推到 65，再查询服务端缓存，避免读到旧音量。
    await e2e.app.wait_for(
        lambda e: e.get("type") == "device.report"
        and e.get("device_id") == device.device_id
        and e.get("source") is None
        and e.get("volume") == 65,
        timeout=5.0,
    )

    # remote.get_status 返回服务端缓存快照，source=server。
    status = await e2e.app.remote("remote.get_status", device.device_id)
    assert status["ok"] is True
    snapshot = await e2e.app.wait_for(
        lambda e: e.get("type") == "device.report"
        and e.get("source") == "server"
        and e.get("volume") == 65,
        timeout=5.0,
    )
    assert snapshot["pan"] == pytest.approx(0.0)


async def test_action_play_is_fifo(e2e):
    device = await e2e.new_device(name="E2E FIFO")
    await e2e.connect_app()

    ack_a = await e2e.app.remote(
        "remote.action.play", device.device_id, name="look_left"
    )
    ack_b = await e2e.app.remote(
        "remote.action.play", device.device_id, name="look_right"
    )
    assert ack_a["ok"] and ack_b["ok"]

    result_a = await expect_result(e2e.app, ack_a["action_id"])
    result_b = await expect_result(e2e.app, ack_b["action_id"])
    assert result_a["status"] == "done"
    assert result_b["status"] == "done"

    assert [c["name"] for c in device.commands_of_type("action.play")] == [
        "look_left",
        "look_right",
    ]
    assert device.pan == pytest.approx(45.0)


async def test_action_stop_preempts_queue_e2e(e2e):
    device = await e2e.new_device(name="E2E 急停", auto_complete=False)
    await e2e.connect_app()

    ack_a = await e2e.app.remote(
        "remote.action.play", device.device_id, name="look_left"
    )
    ack_b = await e2e.app.remote(
        "remote.action.play", device.device_id, name="look_right"
    )
    assert ack_a["ok"] and ack_b["ok"]
    await device.wait_for(
        lambda e: e.get("type") == "action.play"
        and e.get("action_id") == ack_a["action_id"],
        timeout=5.0,
    )

    stop = await e2e.app.remote("remote.action.stop", device.device_id, reason="user_stop")
    assert stop["ok"] is True

    result_a = await expect_result(e2e.app, ack_a["action_id"])
    result_b = await expect_result(e2e.app, ack_b["action_id"])
    assert (result_a["status"], result_a["detail"]) == ("failed", "stopped")
    assert (result_b["status"], result_b["detail"]) == ("failed", "stopped")
    assert len(_results_for(e2e.app, ack_a["action_id"])) == 1
    assert len(_results_for(e2e.app, ack_b["action_id"])) == 1

    stop_frame = await device.wait_for(
        lambda e: e.get("type") == "action.stop", timeout=5.0
    )
    assert stop_frame["reason"] == "user_stop"
    assert device.pan == pytest.approx(0.0)
    assert device.tilt == pytest.approx(0.0)
    device.release()


async def test_execution_timeout_forces_server_stop(e2e, monkeypatch):
    import companion.server.app.services.action_orchestrator as orchestrator_module

    monkeypatch.setattr(
        orchestrator_module, "_command_timeout_ms", lambda command: 250
    )

    device = await e2e.new_device(name="E2E 超时", auto_complete=False)
    await e2e.connect_app()

    ack = await e2e.app.remote(
        "remote.action.play", device.device_id, name="nod"
    )
    assert ack["ok"] is True

    result = await expect_result(e2e.app, ack["action_id"], timeout=5.0)
    assert (result["status"], result["detail"]) == ("failed", "timeout")
    assert len(_results_for(e2e.app, ack["action_id"])) == 1

    stop_frame = await device.wait_for(
        lambda e: e.get("type") == "action.stop", timeout=5.0
    )
    assert stop_frame["reason"] == "server_cancel"
    device.release()


async def test_offline_device_never_replays_commands(e2e):
    device = await e2e.new_device(name="E2E 离线")
    await e2e.connect_app()
    device_id = device.device_id

    await device.close()
    try:
        await e2e.app.wait_for(
            lambda e: e.get("type") == "device.status"
            and e.get("device_id") == device_id
            and e.get("online") is False,
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        raise AssertionError(
            f"未收到离线状态；device.close_code={device.close_code} "
            f"app.close_code={e2e.app.close_code} events={e2e.app.events[-8:]}"
        )

    ack = await e2e.app.remote(
        "remote.action.play", device_id, name="nod"
    )
    assert ack["ok"] is False
    assert ack["error"] == "device_offline"
    assert "action_id" not in ack

    # 重新上线：设备历史为空，服务端绝不补发离线期间的指令。
    reconnected = SimulatedDevice(
        e2e.app.base_url,
        device_id=device_id,
        token=device.token,
        protocol_version=2,
        time_scale=0.01,
    )
    e2e.devices.append(reconnected)
    await reconnected.connect()
    # 用「App 收到新会话上线」作为正向前进屏障：`_install()` 完成才会广播
    # online=True，任何补发都必然发生在此之前；再断言设备历史为空。
    await e2e.app.wait_for(
        lambda e: e.get("type") == "device.status"
        and e.get("device_id") == device_id
        and e.get("online") is True,
        timeout=5.0,
    )
    assert reconnected.commands_of_type("action.play") == []


# ---------------------------------------------------------------------------
# 撤销断连
# ---------------------------------------------------------------------------


async def test_device_wait_closed_without_timeout_blocks_until_close(e2e):
    """CLI 联调依赖 `wait_closed(timeout=None)` 常驻，不能被默认 5s 超时打断。"""
    device = await e2e.new_device(name="E2E 常驻等待")
    waiter = asyncio.create_task(device.wait_closed(timeout=None))
    await asyncio.sleep(0.1)
    assert not waiter.done()

    await device.close()
    await asyncio.wait_for(waiter, timeout=5.0)


async def test_device_simulator_cli_stays_connected(e2e):
    """CLI 主用法必须常驻；旧实现 `wait_closed()` 默认 5s 超时会直接崩掉。"""
    repo_root = Path(__file__).resolve().parents[2]
    device = await e2e.new_device(name="E2E CLI", connect=False)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "companion.simulators.device_simulator",
            "--base-url",
            e2e.app.base_url,
            "--device-id",
            device.device_id,
            "--token",
            device.token,
            "--time-scale",
            "0.01",
            "--report-interval",
            "1",
        ],
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # 超过旧实现的 5s 默认超时；仍在运行说明 CLI 没有自行退出。
        await asyncio.sleep(6.0)
        assert proc.poll() is None, proc.stderr.read() if proc.stderr else ""

        # 不仅活着，还必须真的握上手、能接收指令。
        await e2e.connect_app()
        ack = await e2e.app.remote(
            "remote.volume.set", device.device_id, volume=44
        )
        assert ack["ok"] is True, ack
        await e2e.app.wait_for(
            lambda e: e.get("type") == "device.report"
            and e.get("device_id") == device.device_id
            and e.get("volume") == 44,
            timeout=5.0,
        )
    finally:
        proc.terminate()
        try:
            await asyncio.to_thread(proc.wait, 10)
        except subprocess.TimeoutExpired:
            proc.kill()
            await asyncio.to_thread(proc.wait, 5)


async def test_device_simulator_cli_fails_on_bad_token(e2e):
    """握手失败必须用非零退出码暴露，否则脚本/CI 会把联调失败当成功。"""
    repo_root = Path(__file__).resolve().parents[2]
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "companion.simulators.device_simulator",
            "--base-url",
            e2e.app.base_url,
            "--device-id",
            "cli-bad-token",
            "--token",
            "definitely-not-a-valid-device-token",
            "--report-interval",
            "0",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0, proc.stdout
    assert "handshake failed" in proc.stderr


async def test_app_token_revocation_closes_ws(e2e):
    other = await e2e.login_other_app()
    await e2e.connect_app(other)

    resp = await e2e.app.post(f"/api/auth/tokens/{other.token_id}/revoke")
    assert resp.status_code == 200, resp.text

    await other.wait_closed(timeout=5.0)
    assert other.close_code == 4401
    assert other.close_reason == "token_revoked"


async def test_device_revocation_closes_device_ws(e2e):
    device = await e2e.new_device(name="E2E 撤销设备")
    resp = await e2e.app.delete(f"/api/devices/{device.device_id}")
    assert resp.status_code == 200, resp.text

    await device.wait_closed(timeout=5.0)
    assert device.close_code == 4401
    assert device.close_reason == "device_revoked"

    # 旧 Token 不能再握手成功。
    reconnect = SimulatedDevice(
        e2e.app.base_url,
        device_id=device.device_id,
        token=device.token,
        protocol_version=2,
    )
    await reconnect.connect()
    await reconnect.wait_closed(timeout=5.0)
    assert reconnect.close_code == 4401


# ---------------------------------------------------------------------------
# LLM 工具循环闭环
# ---------------------------------------------------------------------------


async def test_chat_tool_loop_drives_real_device(e2e, monkeypatch):
    import companion.server.app.services.orchestrator as orchestrator_module

    assistant_id = await e2e.new_assistant("E2E 工具角色")
    conversation_id = await e2e.new_conversation(assistant_id)
    device = await e2e.new_device(
        name="E2E 工具设备",
        assistant_id=assistant_id,
        conversation_id=conversation_id,
    )
    assert device.device_id

    fake = ScriptedDeepSeek(
        [
            [
                content("好的，我点点头。"),
                tool_call("call_1", "robot.action", '{"name": "nod", "intensity": 3}'),
            ],
            [content("嗯嗯，收到。"), {"type": "done", "finish_reason": "stop"}],
        ]
    )
    monkeypatch.setattr(orchestrator_module, "DeepSeekClient", lambda: fake)

    events = await e2e.app.chat(conversation_id, "用力点点头")

    assert fake.calls and fake.calls[0]["tools"], "首轮必须注入 robot.* 工具"
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0]["tool"] == "robot.action"
    assert tool_results[0]["result"]["ok"] is True
    assert tool_results[0]["result"]["status"] == "done"

    contents = "".join(e.get("delta", "") for e in events if e.get("type") == "content")
    assert "好的" in contents and "收到" in contents
    assert any(e.get("type") == "done" for e in events)

    # 第二轮必须把 tool 结果回灌给模型，否则模型只是凭空续说。
    assert len(fake.calls) == 2
    tool_messages = [
        m for m in fake.calls[1]["messages"] if m.get("role") == "tool"
    ]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_1"]
    assert json.loads(tool_messages[0]["content"])["ok"] is True

    command = device.commands_of_type("action.play")[0]
    assert command["name"] == "nod"
    assert command["intensity"] == 3
    assert command["source"] == "llm"
    assert command["turn_id"]


# ---------------------------------------------------------------------------
# 限速与日志脱敏
# ---------------------------------------------------------------------------


async def test_app_remote_rate_limit(e2e, monkeypatch):
    # 固定窗口：3 条 / 60 秒，彻底去掉「6 次串行 RTT 必须挤进 1 秒」的时序依赖。
    from companion.server.app.services import app_gateway as app_gateway_module

    monkeypatch.setattr(app_gateway_module, "MAX_REMOTE_PER_SECOND", 3)
    monkeypatch.setattr(app_gateway_module, "RATE_WINDOW_SECONDS", 60.0)

    device = await e2e.new_device(name="E2E 限速")
    # 这里只需要 ack（会话已注册），不需要广播就绪同步，避免探针占用窗口。
    await e2e.app.connect_ws()

    acks = []
    for _ in range(6):
        acks.append(await e2e.app.remote("remote.get_status", device.device_id))
    limited = [a for a in acks if a.get("error") == "rate_limited"]
    assert len(limited) == 3, acks
    assert all(a["ok"] is False for a in limited)
    assert all(a["ok"] is True for a in acks if a not in limited)


async def test_logs_do_not_leak_tokens_or_passwords(e2e, caplog, monkeypatch):
    import logging

    # 第三方库在 DEBUG 会打印自己收发的请求头（含 Authorization），属于
    # 库日志而非本项目日志；用 monkeypatch 改级别（用例结束后自动恢复），
    # 不按 logger 名前缀过滤 record，避免顺带放过本项目的服务端日志。
    for library in ("httpx", "httpcore", "websockets"):
        monkeypatch.setattr(
            logging.getLogger(library), "level", logging.WARNING
        )
    caplog.set_level(logging.DEBUG)

    # 登录必须发生在 call 阶段：caplog 在用例开始时才安装，setup 阶段的
    # 登录日志不会被采集，密码断言否则恒真。
    await e2e.login_other_app()

    device = await e2e.new_device(name="E2E 日志", auto_complete=False)
    await e2e.connect_app()
    ack = await e2e.app.remote(
        "remote.action.play", device.device_id, name="nod"
    )
    # 急停会走调度器 `_terminate` 的 INFO 日志，确保确实有服务端日志被采集。
    stop = await e2e.app.remote(
        "remote.action.stop", device.device_id, reason="user_stop"
    )
    assert stop["ok"] is True
    result = await expect_result(e2e.app, ack["action_id"])
    assert (result["status"], result["detail"]) == ("failed", "stopped")
    device.release()

    records = [
        r
        for r in caplog.records
        if r.name.startswith(("companion.", "uvicorn"))
    ]
    logged = "\n".join(f"{r.name}: {r.getMessage()}" for r in records)
    assert records, "服务端日志为空，测试失去意义"
    assert any(r.name.startswith("companion.server") for r in records)
    assert e2e.app.token not in logged
    assert device.token not in logged
    assert ADMIN_PASSWORD not in logged
