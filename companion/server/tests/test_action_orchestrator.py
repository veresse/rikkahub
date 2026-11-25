"""ActionOrchestrator 行为测试：校验、限次、限速、排队与覆盖。"""

import asyncio
import time

import pytest

from companion.server.app.services.action_orchestrator import (
    ActionOrchestrator,
    ActionTurnState,
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
