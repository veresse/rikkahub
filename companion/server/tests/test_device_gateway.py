"""DeviceGateway（`/device/ws` 核心逻辑）行为测试。

用 FakeChannel 代替 Starlette WebSocket，覆盖握手、降级、会话替换、
action.result 关联、device.report 落库与断开清理。真实 WS 适配层由
`routers/ws.py` 负责，不在本文件测试。
"""

import asyncio

import pytest
from sqlalchemy import delete

from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.device import DeviceModel
from companion.server.app.services.device_gateway import DeviceGateway
from companion.server.app.services.device_registry import (
    device_link_is_online,
    get_device_link,
    reset_device_links,
    unregister_device_link,
)
from companion.server.app.services.events import EventBus
from companion.shared.catalog import get_catalog

DEVICE_ID = "dev-gateway-test"
TOKEN_HASH = "b" * 64


class FakeChannel:
    """最小 WebSocket 抽象：可以注入上行消息，记录下行与关闭码。"""

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
        # 模拟真实 WebSocket：关闭后阻塞中的 receive 立即返回 None。
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


class FlakyChannel(FakeChannel):
    """握手后可以让 `send_json` 失败，模拟传输层写错误。"""

    def __init__(self):
        super().__init__()
        self.fail = False

    async def send_json(self, payload: dict) -> None:
        if self.fail and payload.get("type") != "hello_ack":
            raise ConnectionError("write failed")
        await super().send_json(payload)


async def wait_until(predicate, timeout: float = 2.0):
    async def _poll():
        while True:
            result = predicate()
            if result:
                return result
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout)


async def wait_until_async(predicate, timeout: float = 5.0):
    async def _poll():
        while True:
            if await predicate():
                return True
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout)


class PausingSessionFactory:
    """把「上报读到 active=True」与「真正写库」之间拉开的测试钩子。

    - `get()` 返回后暂停（旧实现的读-改-写窗口）；
    - `execute()` 前暂停（新实现的条件 UPDATE 之前）；
    - `pause_on_get=N` 只在第 N 次 `get()` 返回后暂停，用于卡在
      「`handle()` 预检之后、`_install()` 复核之前」。

    暂停期间真实 `DELETE /api/devices/{id}` 会提交撤销；放行后无论走哪条路径，
    都不允许把 `state` 写回 `speaking`。
    """

    def __init__(self, inner=AsyncSessionLocal, pause_on_get: int | None = None):
        self._inner = inner
        self.pause_enabled = False
        self.pause_on_get = pause_on_get
        self.get_count = 0
        self.paused = False
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()

    def __call__(self):
        return _PausingSessionCtx(self)

    async def pause(self, force: bool = False) -> None:
        if self.paused:
            return
        if not force and not self.pause_enabled:
            return
        self.paused = True
        self.entered.set()
        await self.resume.wait()


class _PausingSessionCtx:
    def __init__(self, factory: PausingSessionFactory):
        self._factory = factory
        self._session = None

    async def __aenter__(self):
        self._session = self._factory._inner()
        await self._session.__aenter__()
        return _PausingSession(self._factory, self._session)

    async def __aexit__(self, *exc_info):
        return await self._session.__aexit__(*exc_info)


class _PausingSession:
    def __init__(self, factory: PausingSessionFactory, session):
        self._factory = factory
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def get(self, *args, **kwargs):
        device = await self._session.get(*args, **kwargs)
        self._factory.get_count += 1
        if self._factory.pause_on_get == self._factory.get_count:
            await self._factory.pause(force=True)
        else:
            await self._factory.pause()
        return device

    async def execute(self, *args, **kwargs):
        await self._factory.pause()
        return await self._session.execute(*args, **kwargs)


async def wait_status(link, key: str, value, timeout: float = 2.0) -> dict:
    async def _poll():
        while True:
            status = await link.get_status()
            if status.get(key) == value:
                return status
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout)


def hello(**overrides) -> dict:
    payload = {
        "v": 2,
        "type": "hello",
        "device_id": DEVICE_ID,
        "firmware_version": "2.0.0",
        "action_catalog_version": get_catalog()["version"],
        "capabilities": ["action", "volume", "expression"],
        "audio": {
            "uplink": {"codec": "opus", "sample_rate": 16000, "channels": 1, "frame_ms": 60},
            "downlink": {"codec": "opus", "sample_rate": 24000, "channels": 1, "frame_ms": 60},
        },
        "ts": 1764000000000,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
async def seeded_device():
    async with AsyncSessionLocal() as session:
        session.add(
            DeviceModel(
                id=DEVICE_ID,
                name="网关测试机",
                token_hash=TOKEN_HASH,
                protocol_version=2,
                capabilities_json=["action"],
                volume=65,
                idle_enabled=True,
                pan_offset=1.0,
                tilt_offset=-1.0,
            )
        )
        await session.commit()
    yield DEVICE_ID
    async with AsyncSessionLocal() as session:
        await session.execute(delete(DeviceModel).where(DeviceModel.id == DEVICE_ID))
        await session.commit()


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_device_links()
    yield
    reset_device_links()


class Harness:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.bus = EventBus()
        self.events: list[dict] = []

        async def collect(event: dict) -> None:
            self.events.append(event)

        self.bus.subscribe(collect)
        self.gateway = DeviceGateway(self.bus, session_factory=session_factory)

    async def start(self, channel: FakeChannel) -> asyncio.Task:
        return asyncio.create_task(self.gateway.handle(DEVICE_ID, channel))

    async def connected(self, **overrides) -> tuple[asyncio.Task, FakeChannel, dict]:
        channel = FakeChannel()
        task = await self.start(channel)
        channel.push(hello(**overrides))
        ack = await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
        return task, channel, ack


async def _stop(task: asyncio.Task, channel: FakeChannel) -> None:
    channel.push(None)
    await asyncio.wait_for(task, timeout=2.0)


def test_device_link_is_online_requires_ready_and_not_closing():
    """LLM/事件/App 共用同一个在线判定：握手完成且未进入收尾。"""

    class Link:
        pass

    link = Link()
    assert device_link_is_online(None) is False
    assert device_link_is_online(link) is True
    link.ready = False
    assert device_link_is_online(link) is False
    link.ready = True
    link.closing = True
    assert device_link_is_online(link) is False
    link.closing = False
    link.closed = True
    assert device_link_is_online(link) is False


async def test_hello_v2_registers_link_and_ack_contains_server_state(seeded_device):
    harness = Harness()
    task, channel, ack = await harness.connected()

    assert ack["v"] == 2
    assert ack["protocol_version"] == 2
    assert ack["capabilities"] == ["action", "volume", "expression"]
    assert ack["action_catalog_version"] == get_catalog()["version"]
    assert ack["warnings"] == []
    assert ack["state"] == {
        "volume": 65,
        "idle_enabled": True,
        "pan_offset": 1.0,
        "tilt_offset": -1.0,
    }
    assert ack["limits"]["pan"] == [-90.0, 90.0]
    assert ack["limits"]["tilt"] == [-20.0, 45.0]
    assert ack["limits"]["rate_limit_per_second"] == 10
    assert ack["audio"]["uplink"]["sample_rate"] == 16000
    assert ack["session_id"]
    assert get_device_link(DEVICE_ID) is not None

    async with AsyncSessionLocal() as session:
        device = await session.get(DeviceModel, DEVICE_ID)
        assert device.firmware_version == "2.0.0"
        assert device.protocol_version == 2
        assert device.capabilities_json == ["action", "volume", "expression"]
        assert device.last_seen_at is not None

    await _stop(task, channel)
    assert get_device_link(DEVICE_ID) is None


async def test_hello_timeout_closes_4408(seeded_device, monkeypatch):
    monkeypatch.setattr(settings, "DEVICE_HELLO_TIMEOUT_SECONDS", 0.05)
    harness = Harness()
    channel = FakeChannel()
    task = await harness.start(channel)

    await asyncio.wait_for(task, timeout=2.0)
    assert channel.closed == (4408, "hello_timeout")
    assert harness.events[-1]["type"] == "device.status"
    assert harness.events[-1]["online"] is False


async def test_v1_hello_degrades_without_capabilities(seeded_device):
    harness = Harness()
    task, channel, ack = await harness.connected(
        v=1, capabilities=None, action_catalog_version=None
    )

    assert ack["protocol_version"] == 1
    assert ack["capabilities"] == []
    assert "action_catalog_mismatch" not in ack["warnings"]

    async with AsyncSessionLocal() as session:
        device = await session.get(DeviceModel, DEVICE_ID)
        assert device.protocol_version == 1
        assert device.capabilities_json == []

    await _stop(task, channel)


async def test_catalog_version_mismatch_produces_warning(seeded_device):
    harness = Harness()
    task, channel, ack = await harness.connected(action_catalog_version="1.0.0")
    assert ack["warnings"] == ["action_catalog_mismatch"]
    await _stop(task, channel)


async def test_hello_device_id_mismatch_closes_4403(seeded_device):
    harness = Harness()
    channel = FakeChannel()
    task = await harness.start(channel)
    channel.push(hello(device_id="another-device"))

    await asyncio.wait_for(task, timeout=2.0)
    assert channel.closed == (4403, "device_mismatch")
    assert get_device_link(DEVICE_ID) is None


async def test_first_message_not_hello_closes_4408(seeded_device):
    harness = Harness()
    channel = FakeChannel()
    task = await harness.start(channel)
    channel.push({"type": "device.report", "volume": 10})

    await asyncio.wait_for(task, timeout=2.0)
    assert channel.closed == (4408, "invalid_hello")


class SlowAckChannel(FakeChannel):
    """hello_ack 的发送被阻塞，制造「链路已注册、ack 尚未发出」的窗口。"""

    def __init__(self):
        super().__init__()
        self.ack_started = asyncio.Event()
        self.ack_release = asyncio.Event()

    async def send_json(self, payload: dict) -> None:
        if payload.get("type") == "hello_ack":
            self.ack_started.set()
            await self.ack_release.wait()
        await super().send_json(payload)


async def test_link_is_not_controllable_before_hello_ack(seeded_device):
    """ack 之前链路虽已注册，但既不能下发也不能对外显示在线。

    协议 §2 的 session_id 在 hello_ack 里，设备（含自带模拟器）会把 ack 之前的
    控制帧当成握手失败；因此注册必须早于 ack（撤销路由才查得到链路），但
    `ready=False` 期间一律拒绝下发，上线事件也只在 ack 之后广播。
    """
    harness = Harness()
    channel = SlowAckChannel()
    task = await harness.start(channel)
    channel.push(hello())
    await asyncio.wait_for(channel.ack_started.wait(), timeout=3.0)

    link = get_device_link(DEVICE_ID)
    assert link is not None
    assert link.ready is False
    assert (await link.get_status())["online"] is False
    rejected = await link.send({"type": "idle.set", "enabled": False})
    assert (rejected.status, rejected.detail) == ("failed", "device_offline")
    assert not [m for m in channel.sent if m.get("type") == "idle.set"]
    assert not [e for e in harness.events if e.get("type") == "device.status"]

    channel.ack_release.set()
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    await wait_until(lambda: getattr(link, "ready", False))
    assert (await link.get_status())["online"] is True
    await wait_until(
        lambda: any(
            e.get("type") == "device.status" and e.get("online") is True
            for e in harness.events
        )
    )
    await _stop(task, channel)


async def test_revoke_while_ack_blocked_does_not_broadcast_online(
    seeded_device, client, auth_headers
):
    """ack 发送期间撤销：ack 写成功后也不得再广播 online=true。

    `_mark_ready()` 只检查「链路仍是 current」是不够的：撤销路由会关闭链路但
    不在注册表里删除它，因此必须在锁内复核 closed/closing 与 is_active。
    """
    harness = Harness()
    channel = SlowAckChannel()
    task = await harness.start(channel)
    channel.push(hello())
    await asyncio.wait_for(channel.ack_started.wait(), timeout=3.0)

    try:
        resp = await asyncio.wait_for(
            client.delete(f"/api/devices/{DEVICE_ID}", headers=auth_headers),
            timeout=5.0,
        )
    finally:
        channel.ack_release.set()
    assert resp.status_code == 200, resp.text

    await asyncio.wait_for(task, timeout=3.0)
    online = [
        e
        for e in harness.events
        if e.get("type") == "device.status" and e.get("online") is True
    ]
    assert online == [], harness.events
    assert get_device_link(DEVICE_ID) is None


async def test_action_result_resolves_pending_command(seeded_device):
    harness = Harness()
    task, channel, ack = await harness.connected()
    link = get_device_link(DEVICE_ID)

    send_task = asyncio.create_task(
        link.send(
            {
                "type": "action.play",
                "action_id": "act-1",
                "name": "nod",
                "intensity": 2,
                "speed": 2,
                "repeat": 1,
                "source": "llm",
            }
        )
    )
    command = await channel.wait_sent(lambda m: m.get("type") == "action.play")
    assert command["v"] == 2
    assert command["session_id"] == ack["session_id"]
    assert command["ts"] > 0

    channel.push(
        {"v": 2, "type": "action.result", "action_id": "act-1", "status": "done", "detail": ""}
    )
    result = await asyncio.wait_for(send_task, timeout=2.0)
    assert result.status == "done"

    event = await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "action.result"), None)
    )
    assert event["action_id"] == "act-1"
    assert event["source"] == "llm"

    await _stop(task, channel)


async def test_settled_action_result_is_not_rebroadcast(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    link = get_device_link(DEVICE_ID)

    send_task = asyncio.create_task(
        link.send(
            {
                "type": "action.play",
                "action_id": "act-dup",
                "name": "nod",
                "source": "app",
            }
        )
    )
    await channel.wait_sent(lambda m: m.get("action_id") == "act-dup")
    channel.push(
        {"v": 2, "type": "action.result", "action_id": "act-dup", "status": "done"}
    )
    assert (await asyncio.wait_for(send_task, timeout=2.0)).status == "done"
    await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "action.result"), None)
    )

    # 服务端已结算（例如超时后急停），设备迟到的同一 action_id 回执不得二次广播。
    channel.push(
        {
            "v": 2,
            "type": "action.result",
            "action_id": "act-dup",
            "status": "failed",
            "detail": "stopped",
        }
    )
    channel.push({"v": 2, "type": "device.report", "volume": 66})
    await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "device.report"), None)
    )

    results = [e for e in harness.events if e["type"] == "action.result"]
    assert len(results) == 1
    assert results[0]["status"] == "done"

    await _stop(task, channel)


async def test_unknown_action_result_is_not_broadcast(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()

    channel.push(
        {"v": 2, "type": "action.result", "action_id": "never-sent", "status": "done"}
    )
    channel.push({"v": 2, "type": "device.report", "volume": 67})
    await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "device.report"), None)
    )

    assert not [e for e in harness.events if e["type"] == "action.result"]

    await _stop(task, channel)


async def test_last_report_is_persisted_on_disconnect(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()

    channel.push(
        {"v": 2, "type": "device.report", "volume": 23, "state": "speaking"}
    )
    channel.push(None)
    await asyncio.wait_for(task, timeout=2.0)

    async with AsyncSessionLocal() as session:
        device = await session.get(DeviceModel, DEVICE_ID)
        assert device.volume == 23
        assert device.state == "speaking"
        assert device.last_report_json["volume"] == 23


async def test_stop_writer_flushes_reports_in_order(seeded_device):
    """下线冲刷不得与 writer 并发，否则慢的旧上报会覆盖新上报。"""
    harness = Harness()
    task, channel, _ = await harness.connected()
    session = get_device_link(DEVICE_ID)
    assert session is not None

    order: list[int] = []
    started = asyncio.Event()

    async def slow_handler(_session, report: dict) -> None:
        if report["volume"] == 1:
            started.set()
            await asyncio.sleep(0.05)
        # 提交发生在 sleep 之后：旧实现会先 flush 掉 2，再让 writer 提交 1。
        order.append(report["volume"])

    session._report_handler = slow_handler
    session.queue_report({"volume": 1})
    # 等 writer 真正取走第一条并进入慢 handler，再把第二条放进容量为 1 的队列。
    await asyncio.wait_for(started.wait(), timeout=1.0)
    session.queue_report({"volume": 2})
    await session.stop_writer()

    assert order == [1, 2]
    await _stop(task, channel)


async def test_shutdown_flushes_queued_report(seeded_device):
    """服务关闭也必须冲刷最后一条上报，而不是直接 cancel writer。"""
    harness = Harness()
    task, channel, _ = await harness.connected()
    session = get_device_link(DEVICE_ID)
    assert session is not None

    persisted: list[int] = []

    async def handler(_session, report: dict) -> None:
        persisted.append(report["volume"])

    session._report_handler = handler
    # 让 writer 先退出，模拟「上报已入队、writer 尚未/无法消费」。
    session._writer.cancel()
    await asyncio.gather(session._writer, return_exceptions=True)
    session.queue_report({"volume": 77})

    await harness.gateway.shutdown()
    assert persisted == [77]

    await asyncio.wait_for(task, timeout=2.0)


async def test_stop_writer_retries_inflight_report_after_timeout(
    seeded_device, monkeypatch
):
    """writer 卡在 handler 时被取消，已出队的上报仍必须补写。"""
    import companion.server.app.services.device_gateway as mod

    monkeypatch.setattr(mod, "REPORT_STOP_QUEUE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(mod, "REPORT_STOP_DRAIN_TIMEOUT_SECONDS", 0.05)

    harness = Harness()
    task, channel, _ = await harness.connected()
    session = get_device_link(DEVICE_ID)
    assert session is not None

    attempts = 0
    persisted: list[int] = []
    started = asyncio.Event()

    async def handler(_session, report: dict) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            started.set()
            await asyncio.sleep(5)
        persisted.append(report["volume"])

    session._report_handler = handler
    session.queue_report({"volume": 88})
    await asyncio.wait_for(started.wait(), timeout=1.0)

    await session.stop_writer()

    assert persisted == [88]
    assert attempts == 2
    await _stop(task, channel)


async def test_close_flushes_queued_report(seeded_device):
    """撤销设备等外部 `close()` 路径也必须落库最后一条上报。"""
    harness = Harness()
    task, channel, _ = await harness.connected()
    session = get_device_link(DEVICE_ID)
    assert session is not None

    # 模拟「上报已入队、writer 尚未消费」时连接被关闭（例如设备被撤销）。
    session._writer.cancel()
    await asyncio.gather(session._writer, return_exceptions=True)
    channel.push({"v": 2, "type": "device.report", "volume": 42, "state": "speaking"})
    await wait_until(lambda: not session._report_queue.empty())

    await session.close(4401, "device_revoked")
    await asyncio.wait_for(task, timeout=2.0)

    async with AsyncSessionLocal() as db:
        device = await db.get(DeviceModel, DEVICE_ID)
        assert device.volume == 42
        assert device.state == "speaking"
        assert device.last_report_json["volume"] == 42


async def test_replacement_flushes_queued_report(seeded_device):
    """新连接替换旧连接前，旧连接最后一条上报必须落库，避免快照回退。"""
    harness = Harness()
    task_a, channel_a, _ = await harness.connected()
    session_a = get_device_link(DEVICE_ID)
    assert session_a is not None

    session_a._writer.cancel()
    await asyncio.gather(session_a._writer, return_exceptions=True)
    channel_a.push({"v": 2, "type": "device.report", "volume": 51})
    await wait_until(lambda: not session_a._report_queue.empty())

    task_b, channel_b, _ = await harness.connected()

    async with AsyncSessionLocal() as db:
        device = await db.get(DeviceModel, DEVICE_ID)
        assert device.volume == 51
        assert device.last_report_json["volume"] == 51

    await asyncio.wait_for(task_a, timeout=2.0)
    await _stop(task_b, channel_b)


async def test_concurrent_stop_writer_preserves_fifo(seeded_device, monkeypatch):
    """并发 stop_writer() 不得各取一部分队列，导致慢的旧上报后提交。"""
    import companion.server.app.services.device_gateway as mod

    monkeypatch.setattr(mod, "REPORT_STOP_QUEUE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(mod, "REPORT_STOP_DRAIN_TIMEOUT_SECONDS", 0.02)

    harness = Harness()
    task, channel, _ = await harness.connected()
    session = get_device_link(DEVICE_ID)
    assert session is not None

    order: list[int] = []
    started = asyncio.Event()

    async def slow_handler(_session, report: dict) -> None:
        if report["volume"] == 1:
            started.set()
            await asyncio.sleep(0.1)
        order.append(report["volume"])

    session._report_handler = slow_handler
    session.queue_report({"volume": 1})
    await asyncio.wait_for(started.wait(), timeout=1.0)
    session.queue_report({"volume": 2})

    await asyncio.gather(session.stop_writer(), session.stop_writer())

    assert order == [1, 2]
    await _stop(task, channel)


async def test_close_rejects_commands_while_flushing(seeded_device):
    """冲刷期间会话仍是 current link，但不得再把指令下发到已撤销设备。"""
    harness = Harness()
    task, channel, _ = await harness.connected()
    session = get_device_link(DEVICE_ID)
    assert session is not None

    original = harness.gateway._handle_report
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_handler(sess, report: dict) -> None:
        started.set()
        await release.wait()
        await original(sess, report)

    session._report_handler = slow_handler
    session.queue_report({"volume": 12})
    await asyncio.wait_for(started.wait(), timeout=2.0)

    close_task = asyncio.create_task(session.close(4401, "device_revoked"))
    try:
        # 旧实现没有 closing 标记、close() 可能在冲刷完成前直接返回；用有上界
        # 的等待把回归暴露成失败而不是让 pytest 挂死。
        await wait_until(
            lambda: getattr(session, "closing", False) or close_task.done()
        )
        assert not close_task.done(), "close() 不得在冲刷完成前返回"

        # 冲刷期间会话仍是 current link，但指令必须被拒绝。
        result = await asyncio.wait_for(
            session.send({"type": "idle.set", "enabled": False}), timeout=2.0
        )
        assert result.status == "failed"
        assert result.detail == "device_offline"
        assert not [m for m in channel.sent if m.get("type") == "idle.set"]
        # closing 期间对外一律视为离线，避免「在线但指令全失败」。
        assert (await session.get_status())["online"] is False
    finally:
        release.set()
        await asyncio.wait_for(close_task, timeout=5.0)

    await asyncio.wait_for(task, timeout=2.0)
    assert channel.closed == (4401, "device_revoked")

    # 重复 close（例如替换路径）不得改写关闭码。
    await session.close(4409, "replaced")
    assert channel.closed == (4401, "device_revoked")


async def test_revoke_keeps_revoked_state_and_persists_last_report(
    seeded_device, client, auth_headers
):
    """撤销时冲刷的最后一条上报只能留快照，不能把 state 改回 speaking。"""
    harness = Harness()
    task, channel, _ = await harness.connected()
    session = get_device_link(DEVICE_ID)
    assert session is not None

    # 制造「上报已入队、writer 尚未消费」后走真实撤销路由。
    session._writer.cancel()
    await asyncio.gather(session._writer, return_exceptions=True)
    channel.push({"v": 2, "type": "device.report", "volume": 88, "state": "speaking"})
    await wait_until(lambda: not session._report_queue.empty())

    resp = await client.delete(f"/api/devices/{DEVICE_ID}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "revoked"
    await asyncio.wait_for(task, timeout=2.0)

    async with AsyncSessionLocal() as db:
        device = await db.get(DeviceModel, DEVICE_ID)
        assert device.is_active is False
        assert device.state == "revoked"
        # 快照保留（证明确实冲刷过），运行态字段不回写。
        assert device.last_report_json["volume"] == 88
        assert device.volume == 65


async def test_revoke_while_report_write_in_flight_keeps_revoked_state(
    seeded_device, client, auth_headers
):
    """撤销与上报落库交错时，条件更新不得把 state 写回 speaking。

    旧实现「读 ORM → 改字段 → commit」在读取与提交之间被撤销插入时，会把
    `state="revoked"` 覆盖成上报里的 speaking；`close()` 等待冲刷也救不了，
    因为冲刷本身就在撤销提交之后写库。
    """
    factory = PausingSessionFactory()
    harness = Harness(session_factory=factory)
    task, channel, _ = await harness.connected()
    session = get_device_link(DEVICE_ID)
    assert session is not None

    factory.pause_enabled = True
    channel.push({"v": 2, "type": "device.report", "volume": 77, "state": "speaking"})
    await asyncio.wait_for(factory.entered.wait(), timeout=3.0)

    # 撤销路由会等冲刷完成才返回，所以放后台；这里只等撤销事务真正提交
    # （直接读库），制造「写库晚于撤销提交」的真实交错。
    revoke_task = asyncio.create_task(
        client.delete(f"/api/devices/{DEVICE_ID}", headers=auth_headers)
    )

    async def revoked() -> bool:
        async with AsyncSessionLocal() as db:
            device = await db.get(DeviceModel, DEVICE_ID)
            return device is not None and device.is_active is False

    await wait_until_async(revoked)
    factory.resume.set()

    resp = await asyncio.wait_for(revoke_task, timeout=5.0)
    assert resp.status_code == 200, resp.text
    await asyncio.wait_for(task, timeout=2.0)

    async with AsyncSessionLocal() as db:
        device = await db.get(DeviceModel, DEVICE_ID)
        assert device.is_active is False
        assert device.state == "revoked"
        assert device.volume == 65
        # 上报内容仍以快照形式保留，说明它确实写到了库。
        assert device.last_report_json["volume"] == 77


async def test_revoke_during_hello_wait_blocks_late_registration(
    seeded_device, client, auth_headers
):
    """鉴权通过但还没 hello 时被撤销：连接不得在撤销后重新上线。"""
    harness = Harness()
    channel = FakeChannel()
    # `ws_auth` 已在真实路由里完成，此处只模拟「已鉴权、还没发 hello」。
    task = await harness.start(channel)

    resp = await client.delete(f"/api/devices/{DEVICE_ID}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert get_device_link(DEVICE_ID) is None

    channel.push(hello())
    await wait_until(
        lambda: channel.closed is not None or get_device_link(DEVICE_ID) is not None
    )

    assert get_device_link(DEVICE_ID) is None, "撤销后不得重新注册链路"
    assert channel.closed == (4401, "device_revoked")
    assert not [m for m in channel.sent if m.get("type") == "hello_ack"]
    await asyncio.wait_for(task, timeout=2.0)
    assert any(
        event.get("type") == "device.status"
        and event.get("online") is False
        and event.get("reason") == "device_revoked"
        for event in harness.events
    ), harness.events


async def test_revoke_after_precheck_is_caught_by_install_lock(
    seeded_device, client, auth_headers
):
    """撤销发生在 `handle()` 预检之后：权威判定在 `_install()` 锁内，仍须拒绝。

    `test_revoke_during_hello_wait...` 的时序是撤销先完成、hello 后到，预检
    本身就能拦住，覆盖不到锁内复核。这里用 `pause_on_get=2` 卡在预检的
    `get()` 返回之后（此时设备仍是 active 快照），再提交真实撤销：预检读取的是
    撤销前的旧对象而放行，只有 `_install()` 在生命周期锁内重新查库才能挡住。
    """

    factory = PausingSessionFactory(pause_on_get=2)
    harness = Harness(session_factory=factory)
    channel = FakeChannel()
    task = await harness.start(channel)
    channel.push(hello())

    await asyncio.wait_for(factory.entered.wait(), timeout=3.0)
    # 预检已经取到 active=True 的快照但还没判断；撤销在此提交。
    # 若预检被删，pause 会落进 `_install()` 持锁的 get，DELETE 会等同一把锁；
    # 用 wait_for 让这种死锁以失败而不是挂起结束。
    try:
        resp = await asyncio.wait_for(
            client.delete(f"/api/devices/{DEVICE_ID}", headers=auth_headers),
            timeout=5.0,
        )
    finally:
        factory.resume.set()
    assert resp.status_code == 200, resp.text
    assert get_device_link(DEVICE_ID) is None

    await wait_until(
        lambda: task.done()
        or channel.closed is not None
        or get_device_link(DEVICE_ID) is not None
    )
    assert get_device_link(DEVICE_ID) is None, "锁内复核必须阻止撤销后的注册"
    assert channel.closed == (4401, "device_revoked")
    assert not [m for m in channel.sent if m.get("type") == "hello_ack"]
    await asyncio.wait_for(task, timeout=2.0)
    assert any(
        event.get("type") == "device.status"
        and event.get("online") is False
        and event.get("reason") == "device_revoked"
        for event in harness.events
    ), harness.events


async def test_persist_hello_does_not_refresh_revoked_device(seeded_device):
    """hello 落库与撤销交错时，不得把固件/最后在线时间写到已撤销设备。

    旧实现「读 ORM → 判断 is_active → commit」在读取之后、提交之前被撤销插入时，
    读到的仍是 `is_active=True`，于是已撤销设备的元数据被刷新。改成带
    `is_active` 条件的 UPDATE 后由数据库判定。
    """
    factory = PausingSessionFactory()
    gateway = DeviceGateway(EventBus(), session_factory=factory)
    factory.pause_enabled = True
    task = asyncio.create_task(
        gateway._persist_hello(
            DEVICE_ID,
            {"firmware_version": "9.9.9"},
            protocol_version=2,
            capabilities=["action", "volume"],
        )
    )
    await asyncio.wait_for(factory.entered.wait(), timeout=3.0)

    async with AsyncSessionLocal() as db:
        device = await db.get(DeviceModel, DEVICE_ID)
        device.is_active = False
        device.state = "revoked"
        await db.commit()

    factory.resume.set()
    await asyncio.wait_for(task, timeout=3.0)

    async with AsyncSessionLocal() as db:
        device = await db.get(DeviceModel, DEVICE_ID)
        assert device.firmware_version == ""
        assert device.capabilities_json == ["action"]
        assert device.last_seen_at is None
        assert device.state == "revoked"


async def test_send_failure_keeps_flushing_later_reports(seeded_device):
    """传输层写失败只停止下发，不能让随后 flush 的上报被丢弃。"""
    harness = Harness()
    channel = FlakyChannel()
    task = asyncio.create_task(harness.gateway.handle(DEVICE_ID, channel))
    channel.push(hello())
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    session = get_device_link(DEVICE_ID)
    assert session is not None

    channel.fail = True
    result = await session.send({"type": "idle.set", "enabled": False})
    assert result.status == "failed"
    assert result.detail == "device_offline"
    assert session.closing is True
    assert session.closed is False
    # 写失败后链路不可控：对外视为离线，但仍允许已入队上报冲刷。
    assert (await session.get_status())["online"] is False

    channel.fail = False
    channel.push({"v": 2, "type": "device.report", "volume": 43})

    async def persisted() -> bool:
        async with AsyncSessionLocal() as db:
            device = await db.get(DeviceModel, DEVICE_ID)
            return device is not None and device.volume == 43

    await wait_until_async(persisted)

    await _stop(task, channel)

    async with AsyncSessionLocal() as db:
        device = await db.get(DeviceModel, DEVICE_ID)
        assert device.volume == 43
        assert device.last_report_json["volume"] == 43
    # closing 期间对外按离线处理：上报照常落库，但不再广播运行态，
    # 否则事件流会和 get_status/remote 的 device_offline 矛盾。
    assert not [e for e in harness.events if e.get("type") == "device.report"]


class SlowStopChannel(FakeChannel):
    """让 action.stop 的 send_json 阻塞，制造「stop 已广播、stop 帧未发完」窗口。"""

    def __init__(self):
        super().__init__()
        self.stop_started = asyncio.Event()
        self.stop_release = asyncio.Event()

    async def send_json(self, payload: dict) -> None:
        if payload.get("type") == "action.stop":
            self.stop_started.set()
            await self.stop_release.wait()
        await super().send_json(payload)


async def test_device_result_during_stop_send_is_not_rebroadcast(seeded_device):
    """服务端先急停、设备回执在 action.stop 发送期间到达时，只保留一条终态。"""
    import companion.server.app.services.action_orchestrator as mod
    from companion.server.app.services.events import get_event_bus

    harness = Harness()
    channel = SlowStopChannel()
    task = await harness.start(channel)
    channel.push(hello())
    await channel.wait_sent(lambda m: m.get("type") == "hello_ack")
    link = get_device_link(DEVICE_ID)
    scheduler = mod._DeviceScheduler(DEVICE_ID, link)
    mod._SCHEDULERS[DEVICE_ID] = scheduler

    # 调度器终态走全局总线；设备网关事件走 harness.bus。
    terminal_events: list[dict] = []

    async def collect(event: dict) -> None:
        terminal_events.append(event)

    global_bus = get_event_bus()
    global_bus.subscribe(collect)
    try:
        await scheduler.enqueue(
            {
                "v": 2,
                "type": "action.play",
                "action_id": "stop-race",
                "name": "nod",
                "source": "app",
            }
        )
        await channel.wait_sent(lambda m: m.get("action_id") == "stop-race")

        stop_task = asyncio.create_task(scheduler.stop("user_stop"))
        await asyncio.wait_for(channel.stop_started.wait(), timeout=1.0)

        # stop 的终态已经广播，但 action.stop 帧还没发完；设备此刻回报原动作。
        channel.push(
            {
                "v": 2,
                "type": "action.result",
                "action_id": "stop-race",
                "status": "done",
            }
        )
        channel.push({"v": 2, "type": "device.report", "volume": 41})
        await wait_until(
            lambda: next(
                (e for e in harness.events if e.get("type") == "device.report"), None
            )
        )
        await asyncio.sleep(0.05)

        results = [
            e
            for e in terminal_events + harness.events
            if e.get("type") == "action.result"
            and e.get("action_id") == "stop-race"
        ]
        assert [r["status"] for r in results] == ["failed"]
        assert results[0]["detail"] == "stopped"

        channel.stop_release.set()
        await asyncio.wait_for(stop_task, timeout=2.0)
    finally:
        global_bus.unsubscribe(collect)
        channel.stop_release.set()
        mod.invalidate_device_scheduler(DEVICE_ID)
        await _stop(task, channel)


async def test_device_result_after_scheduler_invalidation_is_not_rebroadcast(
    seeded_device,
):
    """设备断连结算后，迟到的 action.result 不得在调度器被移除后再广播。"""
    import companion.server.app.services.action_orchestrator as mod
    from companion.server.app.services.events import get_event_bus

    harness = Harness()
    task, channel, _ = await harness.connected()
    link = get_device_link(DEVICE_ID)
    scheduler = mod._DeviceScheduler(DEVICE_ID, link)
    mod._SCHEDULERS[DEVICE_ID] = scheduler

    terminal_events: list[dict] = []

    async def collect(event: dict) -> None:
        terminal_events.append(event)

    global_bus = get_event_bus()
    global_bus.subscribe(collect)
    try:
        await scheduler.enqueue(
            {
                "v": 2,
                "type": "action.play",
                "action_id": "inv-race",
                "name": "nod",
                "source": "app",
            }
        )
        await channel.wait_sent(lambda m: m.get("action_id") == "inv-race")

        mod.invalidate_device_scheduler(DEVICE_ID)
        channel.push(
            {
                "v": 2,
                "type": "action.result",
                "action_id": "inv-race",
                "status": "done",
            }
        )
        channel.push({"v": 2, "type": "device.report", "volume": 42})
        await wait_until(
            lambda: next(
                (e for e in harness.events if e.get("type") == "device.report"), None
            )
        )
        await asyncio.sleep(0.05)

        results = [
            e
            for e in terminal_events + harness.events
            if e.get("type") == "action.result"
            and e.get("action_id") == "inv-race"
        ]
        assert [r["status"] for r in results] == ["failed"]
        assert results[0]["detail"] == "device_offline"
    finally:
        global_bus.unsubscribe(collect)
        mod.invalidate_device_scheduler(DEVICE_ID)
        await _stop(task, channel)


async def test_device_report_updates_snapshot_and_publishes_event(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    link = get_device_link(DEVICE_ID)

    channel.push(
        {
            "v": 2,
            "type": "device.report",
            "volume": 52,
            "pan": -12.5,
            "tilt": 3.0,
            "idle": False,
            "state": "speaking",
            "pan_offset": 2.0,
            "tilt_offset": -2.0,
        }
    )

    status = await wait_status(link, "volume", 52)
    assert status["pan"] == -12.5
    assert status["tilt"] == 3.0
    assert status["idle_enabled"] is False
    assert status["state"] == "speaking"

    event = await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "device.report"), None)
    )
    assert event["volume"] == 52
    assert event["device_id"] == DEVICE_ID

    async with AsyncSessionLocal() as session:
        device = await session.get(DeviceModel, DEVICE_ID)
        assert device.volume == 52
        assert device.idle_enabled is False
        assert device.state == "speaking"
        assert device.last_report_json["pan"] == -12.5
        assert device.pan_offset == 2.0

    await _stop(task, channel)


async def test_disconnect_fails_pending_commands_and_unregisters(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    link = get_device_link(DEVICE_ID)

    send_task = asyncio.create_task(
        link.send({"type": "action.play", "action_id": "act-2", "name": "nod"})
    )
    await channel.wait_sent(lambda m: m.get("type") == "action.play")

    channel.push(None)
    result = await asyncio.wait_for(send_task, timeout=2.0)
    assert result.status == "failed"
    assert result.detail == "device_offline"

    await asyncio.wait_for(task, timeout=2.0)
    assert get_device_link(DEVICE_ID) is None
    offline = harness.events[-1]
    assert offline["type"] == "device.status"
    assert offline["online"] is False


async def test_second_connection_replaces_first(seeded_device):
    harness = Harness()
    task_a, channel_a, _ = await harness.connected()
    task_b, channel_b, _ = await harness.connected()

    # hello_ack 在 _install 之前发送；旧连接关闭发生在替换流程内，需等待。
    await wait_until(lambda: channel_a.closed == (4409, "replaced"))
    assert get_device_link(DEVICE_ID) is not None

    await asyncio.wait_for(task_a, timeout=2.0)
    # 旧连接退出不得注销新连接
    assert get_device_link(DEVICE_ID) is not None

    await _stop(task_b, channel_b)
    assert get_device_link(DEVICE_ID) is None


async def test_stop_does_not_wait_for_result_and_resolves_pending(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    link = get_device_link(DEVICE_ID)

    send_task = asyncio.create_task(
        link.send({"type": "action.play", "action_id": "act-3", "name": "dance"})
    )
    await channel.wait_sent(lambda m: m.get("type") == "action.play")

    stop_result = await asyncio.wait_for(
        link.send({"type": "action.stop", "reason": "user_stop"}), timeout=2.0
    )
    assert stop_result.status == "done"
    stop_command = await channel.wait_sent(lambda m: m.get("type") == "action.stop")
    assert stop_command["reason"] == "user_stop"

    result = await asyncio.wait_for(send_task, timeout=2.0)
    assert result.status == "failed"
    assert result.detail == "stopped"

    await _stop(task, channel)


async def test_unknown_and_binary_messages_are_ignored(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()

    channel.push({"v": 2, "type": "totally.unknown", "foo": 1})
    channel.push(b"\xa1\x01\x01\x00\x00\x00\x00\x01\x00\x00\x00\x00payload")
    channel.push(
        {"v": 2, "type": "device.report", "volume": 33, "state": "idle"}
    )

    link = get_device_link(DEVICE_ID)
    await wait_status(link, "volume", 33)
    assert get_device_link(DEVICE_ID) is not None

    await _stop(task, channel)


async def test_unregister_with_other_link_keeps_current(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()
    current = get_device_link(DEVICE_ID)
    unregister_device_link(DEVICE_ID, link=object())
    assert get_device_link(DEVICE_ID) is current
    await _stop(task, channel)


async def test_device_report_event_uses_protocol_idle_field(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected()

    channel.push(
        {
            "v": 2,
            "type": "device.report",
            "volume": 41,
            "pan": 200,
            "tilt": -100,
            "idle": False,
            "state": "idle",
            "ts": 1,
        }
    )

    event = await wait_until(
        lambda: next((e for e in harness.events if e["type"] == "device.report"), None)
    )
    # 对外用协议字段 `idle`，并且边界值必须被夹紧。
    assert event["idle"] is False
    assert "idle_enabled" not in event
    assert event["pan"] == 90.0
    assert event["tilt"] == -20.0
    assert isinstance(event["ts"], int) and event["ts"] > 0

    await _stop(task, channel)


async def test_v1_session_rejects_v2_control_messages(seeded_device):
    harness = Harness()
    task, channel, _ = await harness.connected(v=1, capabilities=None)

    link = get_device_link(DEVICE_ID)
    result = await link.send(
        {"v": 2, "type": "action.play", "action_id": "v1-act", "name": "nod"}
    )
    assert result.status == "failed"
    assert result.detail == "unsupported_protocol"
    assert not any(m.get("type") == "action.play" for m in channel.sent)

    await _stop(task, channel)
