"""AppGateway（`/app/ws`）行为测试。

覆盖遥控指令转发、离线立即失败、参数校验、事件广播、急停与 Token 撤销，
用 FakeChannel 代替 Starlette WebSocket。
"""

import asyncio

import pytest

from companion.server.app.services.app_gateway import AppGateway
from companion.server.app.services.device_registry import (
    ActionResult,
    register_device_link,
    reset_device_links,
)
from companion.server.app.services.action_orchestrator import reset_action_schedulers
from companion.server.app.services.events import get_event_bus

TOKEN_ID = "tok-app-gateway"
DEVICE_ID = "dev-app-gateway"


class FakeChannel:
    def __init__(self):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self.sent_event = asyncio.Event()

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
        self.sent_event.set()

    async def receive(self):
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed is None:
            self.closed = (code, reason)
        self.incoming.put_nowait(None)

    def push(self, message) -> None:
        self.incoming.put_nowait(message)

    async def wait_sent(self, predicate, timeout: float = 2.0) -> dict:
        async def _poll():
            while True:
                for message in self.sent:
                    if predicate(message):
                        return message
                self.sent_event.clear()
                await self.sent_event.wait()

        return await asyncio.wait_for(_poll(), timeout)


class FakeLink:
    def __init__(self, delay: float = 0.0, result: ActionResult | None = None):
        self.delay = delay
        self.result = result or ActionResult(status="done")
        self.commands: list[dict] = []

    async def send(self, command: dict) -> ActionResult:
        self.commands.append(command)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result

    async def get_status(self) -> dict:
        return {"volume": 60, "pan": 0.0, "tilt": 0.0, "state": "idle", "online": True}


class HangingLink:
    def __init__(self):
        self.commands: list[dict] = []

    async def send(self, command: dict) -> ActionResult:
        self.commands.append(command)
        if command.get("type") == "action.stop":
            return ActionResult(status="done")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def get_status(self) -> dict:
        return {}


class Harness:
    def __init__(self):
        # 与生产一致：AppGateway 与调度器终态都走同一个全局事件总线。
        self.bus = get_event_bus()
        self.events: list[dict] = []
        self.bus.subscribe(self._collect)
        self.gateway = AppGateway(self.bus)

    async def _collect(self, event: dict) -> None:
        self.events.append(event)

    async def connect(self) -> tuple[asyncio.Task, FakeChannel]:
        channel = FakeChannel()
        task = asyncio.create_task(self.gateway.handle(TOKEN_ID, channel))
        await asyncio.sleep(0)
        return task, channel

    async def remote(self, channel: FakeChannel, payload: dict) -> dict:
        request_id = payload.setdefault("request_id", f"req-{len(channel.sent)}")
        payload.setdefault("device_id", DEVICE_ID)
        channel.push(payload)
        return await channel.wait_sent(
            lambda m: m.get("type") == "remote.ack" and m.get("request_id") == request_id
        )


@pytest.fixture(autouse=True)
def _clean_state():
    reset_device_links()
    reset_action_schedulers()
    get_event_bus().clear()
    yield
    reset_device_links()
    reset_action_schedulers()
    get_event_bus().clear()


async def _stop(task: asyncio.Task, channel: FakeChannel) -> None:
    channel.push(None)
    await asyncio.wait_for(task, timeout=2.0)


async def wait_until(predicate, timeout: float = 2.0):
    async def _poll():
        while True:
            result = predicate()
            if result:
                return result
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout)


async def test_remote_action_play_forwards_with_app_source():
    link = FakeLink()
    register_device_link(DEVICE_ID, link)
    harness = Harness()
    task, channel = await harness.connect()

    ack = await harness.remote(
        channel,
        {
            "type": "remote.action.play",
            "name": "nod",
            "intensity": 2,
            "speed": 3,
            "repeat": 2,
        },
    )
    assert ack["ok"] is True

    command = await wait_until(
        lambda: next((c for c in link.commands if c.get("type") == "action.play"), None)
    )
    assert command["name"] == "nod"
    assert command["intensity"] == 2
    assert command["speed"] == 3
    assert command["repeat"] == 2
    assert command["source"] == "app"
    assert command["action_id"]

    await _stop(task, channel)


async def test_offline_device_fails_immediately_without_commands():
    harness = Harness()
    task, channel = await harness.connect()

    ack = await harness.remote(
        channel, {"type": "remote.action.play", "name": "nod"}
    )
    assert ack == {
        "v": 2,
        "type": "remote.ack",
        "request_id": ack["request_id"],
        "ok": False,
        "error": "device_offline",
    }

    await _stop(task, channel)


async def test_invalid_parameters_are_rejected():
    link = FakeLink()
    register_device_link(DEVICE_ID, link)
    harness = Harness()
    task, channel = await harness.connect()

    assert (await harness.remote(channel, {"type": "remote.action.play", "name": "nope"}))[
        "error"
    ] == "unknown_action"
    assert (
        await harness.remote(channel, {"type": "remote.volume.set", "volume": 101})
    )["error"] == "invalid_arguments"
    assert (
        await harness.remote(channel, {"type": "remote.servo.move", "pan": 100, "tilt": 0})
    )["error"] == "angle_out_of_range"
    assert (
        await harness.remote(channel, {"type": "remote.idle.set", "enabled": "yes"})
    )["error"] == "invalid_arguments"
    assert link.commands == []

    await _stop(task, channel)


async def test_control_commands_forward_expected_types():
    link = FakeLink()
    register_device_link(DEVICE_ID, link)
    harness = Harness()
    task, channel = await harness.connect()

    for payload in (
        {"type": "remote.volume.set", "volume": 42},
        {"type": "remote.servo.move", "pan": 12.5, "tilt": -3.0, "speed": 400},
        {"type": "remote.idle.set", "enabled": False},
        {"type": "remote.calibrate.preview", "pan_offset": 1.5, "tilt_offset": -2.0},
    ):
        ack = await harness.remote(channel, payload)
        assert ack["ok"] is True, payload

    types = [c["type"] for c in link.commands]
    assert types == ["volume.set", "servo.move", "idle.set", "servo.calibrate_preview"]
    assert all(c["source"] == "app" for c in link.commands)

    await _stop(task, channel)


async def test_remote_stop_interrupts_queued_action():
    link = HangingLink()
    register_device_link(DEVICE_ID, link)
    harness = Harness()
    task, channel = await harness.connect()

    ack = await harness.remote(
        channel, {"type": "remote.action.play", "name": "dance"}
    )
    assert ack["ok"] is True
    await wait_until(
        lambda: next((c for c in link.commands if c.get("type") == "action.play"), None)
    )

    stop_ack = await harness.remote(
        channel, {"type": "remote.action.stop", "reason": "user_stop"}
    )
    assert stop_ack["ok"] is True

    stop = await wait_until(
        lambda: next((c for c in link.commands if c.get("type") == "action.stop"), None)
    )
    assert stop["reason"] == "user_stop"

    await _stop(task, channel)


async def test_get_status_replies_with_snapshot():
    register_device_link(DEVICE_ID, FakeLink())
    harness = Harness()
    task, channel = await harness.connect()

    ack = await harness.remote(channel, {"type": "remote.get_status"})
    assert ack["ok"] is True

    report = await channel.wait_sent(lambda m: m.get("type") == "device.report")
    assert report["device_id"] == DEVICE_ID
    assert report["volume"] == 60

    await _stop(task, channel)


async def test_events_broadcast_to_every_app_connection():
    harness = Harness()
    task_a, channel_a = await harness.connect()
    task_b, channel_b = await harness.connect()

    await harness.bus.publish(
        {"v": 2, "type": "device.status", "device_id": DEVICE_ID, "online": False}
    )

    for channel in (channel_a, channel_b):
        event = await channel.wait_sent(lambda m: m.get("type") == "device.status")
        assert event["device_id"] == DEVICE_ID

    await _stop(task_a, channel_a)
    await _stop(task_b, channel_b)


async def test_malformed_message_reports_error_and_keeps_connection():
    register_device_link(DEVICE_ID, FakeLink())
    harness = Harness()
    task, channel = await harness.connect()

    channel.push({"type": "remote.action.play", "name": "nod"})
    error = await channel.wait_sent(lambda m: m.get("type") == "error")
    assert error["code"] == "invalid_request"

    channel.push({"type": "remote.get_status", "request_id": "req-ok", "device_id": DEVICE_ID})
    ack = await channel.wait_sent(
        lambda m: m.get("type") == "remote.ack" and m.get("request_id") == "req-ok"
    )
    assert ack["ok"] is True
    assert channel.closed is None

    await _stop(task, channel)


async def test_revoke_token_closes_its_connections_only():
    harness = Harness()
    task_a, channel_a = await harness.connect()

    channel_b = FakeChannel()
    task_b = asyncio.create_task(harness.gateway.handle("tok-other", channel_b))
    await asyncio.sleep(0)

    await harness.gateway.revoke(TOKEN_ID)

    await asyncio.wait_for(task_a, timeout=2.0)
    assert channel_a.closed == (4401, "token_revoked")
    assert channel_b.closed is None

    await _stop(task_b, channel_b)


async def test_binary_and_unknown_messages_are_ignored():
    register_device_link(DEVICE_ID, FakeLink())
    harness = Harness()
    task, channel = await harness.connect()

    channel.push(b"\xa1\x01")
    channel.push({"type": "totally.unknown", "request_id": "x"})
    channel.push({"type": "remote.get_status", "request_id": "req-1", "device_id": DEVICE_ID})

    ack = await channel.wait_sent(
        lambda m: m.get("type") == "remote.ack" and m.get("request_id") == "req-1"
    )
    assert ack["ok"] is True

    await _stop(task, channel)


async def test_rate_limit_acks_error_after_ten_per_second():
    register_device_link(DEVICE_ID, FakeLink())
    harness = Harness()
    task, channel = await harness.connect()

    acks = []
    for index in range(11):
        acks.append(
            await harness.remote(
                channel,
                {
                    "type": "remote.volume.set",
                    "volume": index,
                    "request_id": f"req-{index}",
                },
            )
        )

    assert all(ack["ok"] for ack in acks[:10])
    assert acks[10]["ok"] is False
    assert acks[10]["error"] == "rate_limited"

    await _stop(task, channel)


async def test_ack_includes_action_id_for_action_commands():
    link = FakeLink()
    register_device_link(DEVICE_ID, link)
    harness = Harness()
    task, channel = await harness.connect()

    ack = await harness.remote(channel, {"type": "remote.action.play", "name": "nod"})
    assert ack["ok"] is True
    assert ack["action_id"]
    assert ack["action_id"] == link.commands[0]["action_id"]

    await _stop(task, channel)


async def test_superseded_command_receives_terminal_event():
    link = HangingLink()
    register_device_link(DEVICE_ID, link)
    harness = Harness()
    task, channel = await harness.connect()

    first = await harness.remote(
        channel, {"type": "remote.servo.move", "pan": 0, "tilt": 0, "request_id": "r1"}
    )
    assert first["ok"] is True
    await wait_until(
        lambda: next((c for c in link.commands if c.get("type") == "servo.move"), None)
    )

    superseded = await harness.remote(
        channel, {"type": "remote.servo.move", "pan": 10, "tilt": 0, "request_id": "r2"}
    )
    replacement = await harness.remote(
        channel, {"type": "remote.servo.move", "pan": 20, "tilt": 0, "request_id": "r3"}
    )
    assert superseded["ok"] is True and superseded["action_id"]
    assert replacement["ok"] is True and replacement["action_id"]

    # App 的 WebSocket 自身必须收到服务端终态，而不只是事件总线上的记录。
    message = await channel.wait_sent(
        lambda m: m.get("type") == "action.result"
        and m.get("action_id") == superseded["action_id"]
    )
    assert message["status"] == "failed"
    assert message["detail"] == "superseded"
    assert message["source"] == "app"
    assert message["device_id"] == DEVICE_ID

    await _stop(task, channel)


async def test_inflight_timeout_reaches_app_over_ws(monkeypatch):
    import companion.server.app.services.action_orchestrator as mod

    monkeypatch.setattr(mod, "DEFAULT_COMMAND_TIMEOUT_MS", 50)
    link = HangingLink()
    register_device_link(DEVICE_ID, link)
    harness = Harness()
    task, channel = await harness.connect()

    ack = await harness.remote(channel, {"type": "remote.idle.set", "enabled": False})
    assert ack["ok"] is True and ack["action_id"]

    message = await channel.wait_sent(
        lambda m: m.get("type") == "action.result" and m.get("action_id") == ack["action_id"]
    )
    assert message["status"] == "failed"
    assert message["detail"] == "timeout"
    assert any(c.get("type") == "action.stop" for c in link.commands)

    await _stop(task, channel)


async def test_v1_link_rejects_remote_control():
    class V1Link(FakeLink):
        protocol_version = 1

    link = V1Link()
    register_device_link(DEVICE_ID, link)
    harness = Harness()
    task, channel = await harness.connect()

    ack = await harness.remote(channel, {"type": "remote.action.play", "name": "nod"})
    assert ack["ok"] is False
    assert ack["error"] == "unsupported_protocol"
    assert link.commands == []

    await _stop(task, channel)


async def test_connection_attaching_after_revoke_is_closed():
    harness = Harness()
    await harness.gateway.revoke(TOKEN_ID)

    channel = FakeChannel()
    task = asyncio.create_task(harness.gateway.handle(TOKEN_ID, channel))
    await asyncio.wait_for(task, timeout=2.0)
    assert channel.closed == (4401, "token_revoked")


async def test_rate_limit_covers_stop_and_get_status():
    register_device_link(DEVICE_ID, FakeLink())
    harness = Harness()
    task, channel = await harness.connect()

    for index in range(10):
        ack = await harness.remote(
            channel, {"type": "remote.get_status", "request_id": f"q-{index}"}
        )
        assert ack["ok"] is True

    blocked = await harness.remote(
        channel, {"type": "remote.action.stop", "reason": "user_stop", "request_id": "stop"}
    )
    assert blocked["ok"] is False
    assert blocked["error"] == "rate_limited"

    await _stop(task, channel)
