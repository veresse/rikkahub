"""ActionOrchestrator 行为测试：校验、限次、限速、排队与覆盖。"""

import asyncio
import time

import pytest

from companion.server.app.services.action_orchestrator import (
    ActionOrchestrator,
    ActionTurnState,
    _DeviceScheduler,
    reset_action_schedulers,
)
from companion.server.app.services.device_registry import (
    ActionResult,
    register_device_link,
    reset_device_links,
)


class FakeLink:
    def __init__(self, delay: float = 0.0, result: ActionResult | None = None):
        self.delay = delay
        self.result = result or ActionResult(status="done")
        self.commands: list[dict] = []
        self.status_calls = 0

    async def send(self, command: dict) -> ActionResult:
        self.commands.append(command)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result

    async def get_status(self) -> dict:
        self.status_calls += 1
        return {"volume": 60, "state": "idle", "pan": 0.0, "tilt": 0.0}


@pytest.fixture(autouse=True)
def _clean_state():
    reset_device_links()
    reset_action_schedulers()
    yield
    reset_device_links()
    reset_action_schedulers()


def _turn(max_actions: int = 3) -> ActionTurnState:
    return ActionTurnState(turn_id="turn-1", max_actions=max_actions)


async def test_action_play_builds_protocol_command():
    link = FakeLink()
    register_device_link("dev-1", link)
    orch = ActionOrchestrator()

    result = await orch.execute_tool(
        "robot.action", {"name": "nod", "intensity": 3, "speed": 1, "repeat": 2}, "dev-1", _turn()
    )

    assert result["ok"] is True
    assert result["status"] == "done"
    command = link.commands[0]
    assert command["type"] == "action.play"
    assert command["name"] == "nod"
    assert (command["intensity"], command["speed"], command["repeat"]) == (3, 1, 2)
    assert command["source"] == "llm"
    assert command["action_id"] == result["action_id"]


async def test_unknown_action_and_out_of_range_params_are_rejected():
    register_device_link("dev-1", FakeLink())
    orch = ActionOrchestrator()

    assert (await orch.execute_tool("robot.action", {"name": "fly"}, "dev-1", _turn()))["error"] == "unknown_action"
    assert (await orch.execute_tool("robot.action", {"name": "nod", "intensity": 4}, "dev-1", _turn()))["error"] == "invalid_arguments"
    assert (await orch.execute_tool("robot.action", {"name": "nod", "speed": 0}, "dev-1", _turn()))["error"] == "invalid_arguments"
    assert (await orch.execute_tool("robot.action", {"name": "nod", "repeat": 6}, "dev-1", _turn()))["error"] == "invalid_arguments"


async def test_action_limit_is_three_per_turn():
    link = FakeLink()
    register_device_link("dev-1", link)
    orch = ActionOrchestrator()
    turn = _turn()

    for _ in range(3):
        assert (await orch.execute_tool("robot.action", {"name": "nod"}, "dev-1", turn))["ok"] is True

    blocked = await orch.execute_tool("robot.action", {"name": "nod"}, "dev-1", turn)
    assert blocked["ok"] is False
    assert blocked["error"] == "action_limit_exceeded"
    assert len(link.commands) == 3


async def test_get_status_does_not_consume_action_budget():
    link = FakeLink()
    register_device_link("dev-1", link)
    orch = ActionOrchestrator()
    turn = _turn(max_actions=1)

    status = await orch.execute_tool("robot.get_status", {}, "dev-1", turn)
    assert status["ok"] is True
    assert status["status"]["volume"] == 60

    assert (await orch.execute_tool("robot.action", {"name": "nod"}, "dev-1", turn))["ok"] is True
    assert (await orch.execute_tool("robot.action", {"name": "shake"}, "dev-1", turn))["error"] == "action_limit_exceeded"


async def test_move_head_validates_ranges():
    link = FakeLink()
    register_device_link("dev-1", link)
    orch = ActionOrchestrator()

    ok = await orch.execute_tool("robot.move_head", {"pan": -30, "tilt": 10, "speed": 400}, "dev-1", _turn())
    assert ok["ok"] is True
    command = link.commands[-1]
    assert command["type"] == "servo.move"
    assert (command["pan"], command["tilt"], command["speed"]) == (-30.0, 10.0, 400)

    assert (await orch.execute_tool("robot.move_head", {"pan": -91, "tilt": 0}, "dev-1", _turn()))["error"] == "angle_out_of_range"
    assert (await orch.execute_tool("robot.move_head", {"pan": 0, "tilt": 46}, "dev-1", _turn()))["error"] == "angle_out_of_range"
    assert (await orch.execute_tool("robot.move_head", {"pan": 0, "tilt": 0, "speed": 1001}, "dev-1", _turn()))["error"] == "invalid_arguments"


async def test_set_volume_validates_and_is_last_write_wins_while_queued():
    link = FakeLink(delay=0.05)
    register_device_link("dev-1", link)
    orch = ActionOrchestrator()

    first = asyncio.create_task(orch.execute_tool("robot.set_volume", {"volume": 10}, "dev-1", _turn()))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(orch.execute_tool("robot.set_volume", {"volume": 20}, "dev-1", _turn()))
    third = asyncio.create_task(orch.execute_tool("robot.set_volume", {"volume": 30}, "dev-1", _turn()))

    results = await asyncio.gather(first, second, third)
    volumes = [c["volume"] for c in link.commands if c["type"] == "volume.set"]
    assert volumes[0] == 10          # 已在执行，无法撤回
    assert volumes[-1] == 30         # 排队中的 20 被 30 覆盖
    assert 20 not in volumes
    assert results[1]["error"] == "superseded"

    assert (await orch.execute_tool("robot.set_volume", {"volume": 101}, "dev-1", _turn()))["error"] == "invalid_arguments"


async def test_offline_device_returns_structured_error():
    orch = ActionOrchestrator()
    result = await orch.execute_tool("robot.action", {"name": "nod"}, "missing", _turn())
    assert result == {"ok": False, "error": "device_offline"}


async def test_rate_limit_is_ten_commands_per_second():
    link = FakeLink()
    register_device_link("dev-1", link)
    orch = ActionOrchestrator()

    for _ in range(10):
        assert (await orch.execute_tool("robot.set_volume", {"volume": 50}, "dev-1", _turn()))["ok"] is True

    blocked = await orch.execute_tool("robot.set_volume", {"volume": 50}, "dev-1", _turn())
    assert blocked["error"] == "rate_limited"


async def test_queue_limit_is_eight_pending():
    link = FakeLink(delay=0.05)
    register_device_link("dev-1", link)
    orch = ActionOrchestrator()

    in_flight = asyncio.create_task(orch.execute_tool("robot.action", {"name": "dance"}, "dev-1", _turn()))
    await asyncio.sleep(0.01)
    queued = [
        asyncio.create_task(orch.execute_tool("robot.action", {"name": "nod"}, "dev-1", _turn()))
        for _ in range(9)
    ]
    results = await asyncio.gather(*queued)
    errors = [r.get("error") for r in results if not r.get("ok")]
    assert errors == ["queue_full"]
    assert sum(1 for r in results if r.get("ok")) == 8
    assert (await in_flight)["ok"] is True


async def test_stop_clears_queue_and_preempts():
    link = FakeLink(delay=0.2)
    register_device_link("dev-1", link)
    orch = ActionOrchestrator()

    in_flight = asyncio.create_task(orch.execute_tool("robot.action", {"name": "dance"}, "dev-1", _turn()))
    await asyncio.sleep(0.02)
    queued = asyncio.create_task(orch.execute_tool("robot.action", {"name": "nod"}, "dev-1", _turn()))
    await asyncio.sleep(0.02)

    await orch.stop("dev-1", reason="user_stop")

    assert (await in_flight)["error"] == "stopped"
    assert (await queued)["error"] == "stopped"
    assert any(c["type"] == "action.stop" for c in link.commands)


async def test_set_idle_requires_boolean():
    register_device_link("dev-1", FakeLink())
    orch = ActionOrchestrator()
    ok = await orch.execute_tool("robot.set_idle", {"enabled": False}, "dev-1", _turn())
    assert ok["ok"] is True
    bad = await orch.execute_tool("robot.set_idle", {"enabled": "yes"}, "dev-1", _turn())
    assert bad["error"] == "invalid_arguments"


class HangingLink:
    """动作永不回执，用于验证调度器超时强停。"""

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


async def test_scheduler_timeout_sends_action_stop():
    link = HangingLink()
    scheduler = _DeviceScheduler("dev-timeout", link)
    try:
        result = await scheduler.submit(
            {"v": 2, "type": "action.play", "action_id": "a1", "source": "llm"},
            timeout_ms=30,
        )

        assert result.status == "timeout"
        stops = [c for c in link.commands if c.get("type") == "action.stop"]
        assert len(stops) == 1
        assert stops[0]["reason"] == "server_cancel"
    finally:
        scheduler._stopped = True
        if scheduler.worker and not scheduler.worker.done():
            scheduler.worker.cancel()


async def _collect_until(predicate, timeout: float = 2.0):
    async def _poll():
        while True:
            value = predicate()
            if value:
                return value
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout)


async def test_inflight_command_timeout_emits_terminal_and_stop(monkeypatch):
    import companion.server.app.services.action_orchestrator as mod
    from companion.server.app.services.events import get_event_bus

    monkeypatch.setattr(mod, "DEFAULT_COMMAND_TIMEOUT_MS", 50)
    link = HangingLink()
    scheduler = _DeviceScheduler("dev-force", link)
    mod._SCHEDULERS["dev-force"] = scheduler
    bus = get_event_bus()
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    bus.subscribe(collect)
    try:
        result = await scheduler.enqueue(
            {"v": 2, "type": "idle.set", "action_id": "term-1", "source": "app"}
        )
        assert result.status == "queued"

        event = await _collect_until(
            lambda: next((e for e in events if e.get("action_id") == "term-1"), None)
        )
        assert event["status"] == "failed"
        assert event["detail"] == "timeout"
        assert event["source"] == "app"
        assert any(c.get("type") == "action.stop" for c in link.commands)
        # 终态必须恰好一次（worker 结算与急停不得各发一条）。
        assert len([e for e in events if e.get("action_id") == "term-1"]) == 1
    finally:
        bus.unsubscribe(collect)
        mod.invalidate_device_scheduler("dev-force")


class _DeviceReportingLink:
    """模拟设备已回报结果、但 worker 还没被调度恢复的窗口。"""

    def __init__(self, device_id: str, action_id: str):
        self.device_id = device_id
        self.action_id = action_id
        self.commands: list[dict] = []
        self.reported = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, command: dict) -> ActionResult:
        self.commands.append(command)
        if command.get("type") == "action.stop":
            return ActionResult(status="done")
        # 与 DeviceGateway 收到 action.result 后的处理顺序一致：
        # 先同步标记已回报，再由 worker 恢复。
        import companion.server.app.services.action_orchestrator as mod

        mod.mark_action_reported(self.device_id, self.action_id)
        self.reported.set()
        await self.release.wait()
        return ActionResult(status="done", action_id=self.action_id)

    async def get_status(self) -> dict:
        return {}


async def test_device_reported_result_suppresses_late_stop_terminal():
    import companion.server.app.services.action_orchestrator as mod
    from companion.server.app.services.events import get_event_bus

    bus = get_event_bus()
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    bus.subscribe(collect)
    link = _DeviceReportingLink("dev-race", "race-1")
    scheduler = _DeviceScheduler("dev-race", link)
    mod._SCHEDULERS["dev-race"] = scheduler
    try:
        await scheduler.enqueue(
            {
                "v": 2,
                "type": "action.play",
                "action_id": "race-1",
                "name": "nod",
                "source": "app",
            }
        )
        await asyncio.wait_for(link.reported.wait(), timeout=1.0)

        # 网关在设备回报时已经广播过一次 done；此时用户急停。
        mod.publish_async(
            bus,
            {
                "v": 2,
                "type": "action.result",
                "device_id": "dev-race",
                "action_id": "race-1",
                "status": "done",
                "detail": "",
                "source": "app",
                "ts": 1,
            },
        )
        await scheduler.stop("user_stop")
        link.release.set()

        await _collect_until(
            lambda: [e for e in events if e.get("action_id") == "race-1"]
        )
        await asyncio.sleep(0.05)
        results = [
            e
            for e in events
            if e.get("type") == "action.result" and e.get("action_id") == "race-1"
        ]
        assert [r["status"] for r in results] == ["done"]
    finally:
        bus.unsubscribe(collect)
        mod.invalidate_device_scheduler("dev-race")


async def test_expired_queued_command_is_dropped_with_terminal_event(monkeypatch):
    import companion.server.app.services.action_orchestrator as mod
    from companion.server.app.services.events import get_event_bus

    link = FakeLink(delay=0.15)
    scheduler = _DeviceScheduler("dev-queue", link)
    mod._SCHEDULERS["dev-queue"] = scheduler
    bus = get_event_bus()
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    bus.subscribe(collect)
    try:
        first = await scheduler.enqueue(
            {"v": 2, "type": "action.play", "action_id": "first", "name": "nod", "source": "app"}
        )
        assert first.status == "queued"
        await _collect_until(
            lambda: next((c for c in link.commands if c.get("action_id") == "first"), None)
        )

        monkeypatch.setattr(mod, "QUEUE_MAX_WAIT_MS", 20)
        second = await scheduler.enqueue(
            {"v": 2, "type": "idle.set", "action_id": "late", "source": "app"}
        )
        assert second.status == "queued"

        event = await _collect_until(
            lambda: next((e for e in events if e.get("action_id") == "late"), None)
        )
        assert event["status"] == "failed"
        assert event["detail"] == "timeout"
        assert not any(c.get("action_id") == "late" for c in link.commands)
        # 纯排队超时不得急停：设备当前执行的是另一条命令。
        assert not any(c.get("type") == "action.stop" for c in link.commands)
    finally:
        bus.unsubscribe(collect)
        mod.invalidate_device_scheduler("dev-queue")


async def test_invalidate_scheduler_does_not_resurrect_worker():
    import companion.server.app.services.action_orchestrator as mod

    link = FakeLink(delay=0.05)
    scheduler = _DeviceScheduler("dev-inv", link)
    mod._SCHEDULERS["dev-inv"] = scheduler

    await scheduler.enqueue(
        {"v": 2, "type": "action.play", "action_id": "inv-1", "name": "nod", "source": "app"}
    )
    await scheduler.enqueue(
        {"v": 2, "type": "idle.set", "action_id": "inv-2", "source": "app"}
    )

    mod.invalidate_device_scheduler("dev-inv")
    await asyncio.sleep(0.2)

    assert not any(c.get("action_id") == "inv-2" for c in link.commands)
    assert scheduler._stopped is True


async def test_scheduler_enqueue_returns_queued_immediately():
    link = FakeLink(delay=0.2)
    scheduler = _DeviceScheduler("dev-enqueue", link)
    try:
        started = time.monotonic()
        result = await scheduler.enqueue(
            {"v": 2, "type": "action.play", "action_id": "a2", "source": "app"}
        )
        elapsed = time.monotonic() - started

        assert result.status == "queued"
        assert elapsed < 0.1
        await asyncio.sleep(0.02)
        assert any(c.get("action_id") == "a2" for c in link.commands)
    finally:
        scheduler._stopped = True
        if scheduler.worker and not scheduler.worker.done():
            scheduler.worker.cancel()


async def test_cancelled_submit_stops_its_own_inflight_action():
    """聊天流取消时只停本轮在途命令，不做全局急停。"""
    link = HangingLink()
    scheduler = _DeviceScheduler("dev-cancel", link)
    try:
        task = asyncio.create_task(
            scheduler.submit(
                {"v": 2, "type": "action.play", "action_id": "c1", "source": "llm"},
                timeout_ms=5_000,
            )
        )
        await _collect_until(
            lambda: next((c for c in link.commands if c.get("action_id") == "c1"), None)
        )

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        stops = [c for c in link.commands if c.get("type") == "action.stop"]
        assert len(stops) == 1
        assert stops[0]["reason"] == "server_cancel"
    finally:
        scheduler._stopped = True
        if scheduler.worker and not scheduler.worker.done():
            scheduler.worker.cancel()


async def test_queue_full_still_allows_override():
    """队列已满时，覆盖语义必须先把同类旧命令替换掉，而不是直接 queue_full。"""
    link = FakeLink(delay=0.2)
    scheduler = _DeviceScheduler("dev-full", link)
    try:
        await scheduler.enqueue(
            {"v": 2, "type": "action.play", "action_id": "busy", "name": "nod", "source": "app"}
        )
        await _collect_until(
            lambda: next((c for c in link.commands if c.get("action_id") == "busy"), None)
        )
        await scheduler.enqueue(
            {"v": 2, "type": "servo.move", "action_id": "m2", "source": "app"}
        )
        for index in range(7):
            await scheduler.enqueue(
                {
                    "v": 2,
                    "type": "action.play",
                    "action_id": f"fill-{index}",
                    "name": "nod",
                    "source": "app",
                }
            )
        assert len(scheduler.queue) == 8

        result = await scheduler.enqueue(
            {"v": 2, "type": "servo.move", "action_id": "m3", "source": "app"},
            override=True,
        )

        assert result.status == "queued", result
        assert [job.command["action_id"] for job in scheduler.queue].count("m3") == 1
        assert not any(job.command["action_id"] == "m2" for job in scheduler.queue)
    finally:
        scheduler._stopped = True
        if scheduler.worker and not scheduler.worker.done():
            scheduler.worker.cancel()


class ExplodingLink:
    async def send(self, command: dict) -> ActionResult:
        if command.get("type") == "action.stop":
            return ActionResult(status="done")
        raise RuntimeError("link broken")

    async def get_status(self) -> dict:
        return {}


async def test_link_failure_emits_terminal_internal_error():
    import companion.server.app.services.action_orchestrator as mod
    from companion.server.app.services.events import get_event_bus

    scheduler = _DeviceScheduler("dev-broken", ExplodingLink())
    mod._SCHEDULERS["dev-broken"] = scheduler
    bus = get_event_bus()
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    bus.subscribe(collect)
    try:
        result = await scheduler.enqueue(
            {"v": 2, "type": "idle.set", "action_id": "boom", "source": "app"}
        )
        assert result.status == "queued"

        event = await _collect_until(
            lambda: next((e for e in events if e.get("action_id") == "boom"), None)
        )
        assert event["status"] == "failed"
        assert event["detail"] == "internal_error"
    finally:
        bus.unsubscribe(collect)
        mod.invalidate_device_scheduler("dev-broken")
