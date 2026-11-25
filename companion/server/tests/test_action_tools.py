"""机器人工具 Schema 与动态注入条件测试。"""

from types import SimpleNamespace

from companion.shared.catalog import get_action_names

from companion.server.app.services.action_tools import (
    ROBOT_TOOL_NAMES,
    build_robot_tools,
    should_inject_robot_tools,
)


def _tool(name: str) -> dict:
    for tool in build_robot_tools():
        if tool["function"]["name"] == name:
            return tool
    raise AssertionError(f"missing tool {name}")


def test_exposes_exactly_five_robot_tools():
    names = {t["function"]["name"] for t in build_robot_tools()}
    assert names == ROBOT_TOOL_NAMES
    assert names == {
        "robot.action",
        "robot.move_head",
        "robot.set_volume",
        "robot.get_status",
        "robot.set_idle",
    }


def test_action_tool_enum_matches_catalog_and_mentions_levels():
    tool = _tool("robot.action")
    params = tool["function"]["parameters"]
    assert set(params["properties"]["name"]["enum"]) == set(get_action_names())
    assert params["properties"]["intensity"]["enum"] == [1, 2, 3]
    assert params["properties"]["speed"]["enum"] == [1, 2, 3]
    assert params["properties"]["repeat"]["minimum"] == 1
    assert params["properties"]["repeat"]["maximum"] == 5
    assert params["required"] == ["name"]

    description = tool["function"]["description"]
    assert "look_left" in description
    assert "intensity 1/2/3" in description
    assert "单轮最多调用 3 次" in description


def test_move_head_tool_ranges_match_protocol():
    params = _tool("robot.move_head")["function"]["parameters"]
    assert params["properties"]["pan"]["minimum"] == -90
    assert params["properties"]["pan"]["maximum"] == 90
    assert params["properties"]["tilt"]["minimum"] == -20
    assert params["properties"]["tilt"]["maximum"] == 45
    assert params["properties"]["speed"]["minimum"] == 0
    assert params["properties"]["speed"]["maximum"] == 1000
    assert set(params["required"]) == {"pan", "tilt"}


def test_volume_and_idle_tools():
    volume = _tool("robot.set_volume")["function"]["parameters"]
    assert volume["properties"]["volume"]["minimum"] == 0
    assert volume["properties"]["volume"]["maximum"] == 100
    assert volume["required"] == ["volume"]

    idle = _tool("robot.set_idle")["function"]["parameters"]
    assert idle["properties"]["enabled"]["type"] == "boolean"
    assert idle["required"] == ["enabled"]

    status = _tool("robot.get_status")["function"]["parameters"]
    assert status["properties"] == {}


def test_injection_requires_switch_online_v2_and_capability():
    assistant = SimpleNamespace(enable_action_sync=True)
    device = SimpleNamespace(protocol_version=2, capabilities_json=["action"])

    assert should_inject_robot_tools(assistant, device, online=True) is True
    assert should_inject_robot_tools(SimpleNamespace(enable_action_sync=False), device, online=True) is False
    assert should_inject_robot_tools(assistant, device, online=False) is False
    assert should_inject_robot_tools(assistant, None, online=True) is False
    assert should_inject_robot_tools(
        assistant, SimpleNamespace(protocol_version=1, capabilities_json=["action"]), online=True
    ) is False
    assert should_inject_robot_tools(
        assistant, SimpleNamespace(protocol_version=2, capabilities_json=[]), online=True
    ) is False
