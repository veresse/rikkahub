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
import threading
import time

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
        await other.login(password=ADMIN_PASSWORD)
        self.extra_apps.append(other)
        return other

    async def connect_app(self, app: SimulatedApp | None = None) -> SimulatedApp:
        """连接 `/app/ws` 并等待服务端真正注册会话。

        `websockets.connect()` 在收到 101 后就返回，但服务端可能还没执行完
        `AppGateway.handle()`。广播只在已注册会话间投递、不补发，因此测试
        必须先确认会话已挂载，否则会漏掉紧接着发生的事件。
        """
        target = app or self.app
        if target.ws is None:
            await target.connect_ws()

        from companion.server.app.routers import ws as ws_module

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            sessions = ws_module.app_gateway._sessions.get(target.token_id, set())
            if any(not session.closed for session in sessions):
                return target
            await asyncio.sleep(0.01)
        raise AssertionError("App WebSocket 会话未在服务端注册")

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
    await asyncio.sleep(0.1)
    assert device.commands_of_type("action.play") == []


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


async def test_volume_last_write_wins_and_status_snapshot(e2e):
    device = await e2e.new_device(name="E2E 音量")
    await e2e.connect_app()

    first = await e2e.app.remote(
        "remote.volume.set", device.device_id, volume=30
    )
    second = await e2e.app.remote(
        "remote.volume.set", device.device_id, volume=65
    )
    assert first["ok"] is True and second["ok"] is True

    await e2e.app.wait_for(
        lambda e: e.get("type") == "device.report"
        and e.get("device_id") == device.device_id
        and e.get("volume") == 65,
        timeout=5.0,
    )
    assert device.volume == 65

    # 每个已受理的 action_id 都有且仅有一条终态。
    for ack in (first, second):
        result = await expect_result(e2e.app, ack["action_id"])
        assert result["status"] in ("done", "failed")
    for ack in (first, second):
        assert len(_results_for(e2e.app, ack["action_id"])) == 1

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
    await asyncio.sleep(0.3)
    assert reconnected.commands_of_type("action.play") == []


# ---------------------------------------------------------------------------
# 撤销断连
# ---------------------------------------------------------------------------


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

    command = device.commands_of_type("action.play")[0]
    assert command["name"] == "nod"
    assert command["intensity"] == 3
    assert command["source"] == "llm"
    assert command["turn_id"]


# ---------------------------------------------------------------------------
# 限速与日志脱敏
# ---------------------------------------------------------------------------


async def test_app_remote_rate_limit(e2e):
    device = await e2e.new_device(name="E2E 限速")
    await e2e.connect_app()

    acks = []
    for _ in range(15):
        acks.append(await e2e.app.remote("remote.get_status", device.device_id))
    assert any(a.get("error") == "rate_limited" for a in acks)
    assert all(a["ok"] is False for a in acks if a.get("error") == "rate_limited")


async def test_logs_do_not_leak_tokens_or_passwords(e2e, caplog):
    import logging

    # 排除客户端库：httpx/httpcore/websockets 在 DEBUG 级别会打印自己发出的
    # 请求头（含 Authorization），那是模拟器本地日志，不是服务端泄漏。
    client_loggers = ("httpx", "httpcore", "websockets")
    caplog.set_level(logging.DEBUG)
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
        if not any(r.name == p or r.name.startswith(p + ".") for p in client_loggers)
    ]
    logged = "\n".join(f"{r.name}: {r.getMessage()}" for r in records)
    assert records, "服务端/数据库日志为空，测试失去意义"
    assert any(r.name.startswith("companion.server") for r in records)
    assert e2e.app.token not in logged
    assert device.token not in logged
    assert ADMIN_PASSWORD not in logged
