"""流式 tool-call 增量聚合测试。"""

import json

from companion.server.app.services.tool_calls import ToolCallAccumulator


def test_single_call_with_fragmented_arguments():
    acc = ToolCallAccumulator()
    acc.add(
        [
            {"index": 0, "id": "call_1", "type": "function", "function": {"name": "robot.action", "arguments": ""}},
        ]
    )
    acc.add([{"index": 0, "function": {"arguments": '{"name":'}}])
    acc.add([{"index": 0, "function": {"arguments": '"nod","intensity":3}'}}])

    calls = acc.build()
    assert len(calls) == 1
    assert calls[0]["id"] == "call_1"
    assert calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "robot.action"
    assert json.loads(calls[0]["function"]["arguments"]) == {"name": "nod", "intensity": 3}


def test_multiple_calls_arrive_out_of_order():
    acc = ToolCallAccumulator()
    acc.add([{"index": 1, "id": "call_b", "function": {"name": "robot.set_volume", "arguments": '{"volume":30}'}}])
    acc.add([{"index": 0, "id": "call_a", "function": {"name": "robot.action", "arguments": '{"name":"nod"}'}}])
    acc.add([{"index": 1, "function": {"arguments": ""}}])

    calls = acc.build()
    assert [c["id"] for c in calls] == ["call_a", "call_b"]
    assert [c["function"]["name"] for c in calls] == ["robot.action", "robot.set_volume"]


def test_empty_accumulator_builds_no_calls():
    assert ToolCallAccumulator().build() == []


def test_missing_id_and_name_are_tolerated():
    acc = ToolCallAccumulator()
    acc.add([{"index": 0, "function": {"arguments": "{}"}}])
    calls = acc.build()
    assert len(calls) == 1
    assert calls[0]["id"] == ""
    assert calls[0]["function"]["name"] == ""


def test_name_fragments_are_joined_but_duplicate_full_frames_ignored():
    fragmented = ToolCallAccumulator()
    fragmented.add([{"index": 0, "id": "c1", "function": {"name": "robot.", "arguments": "{}"}}])
    fragmented.add([{"index": 0, "function": {"name": "action"}}])
    assert fragmented.build()[0]["function"]["name"] == "robot.action"

    duplicated = ToolCallAccumulator()
    duplicated.add(
        [{"index": 0, "id": "c1", "function": {"name": "robot.action", "arguments": "{}"}}]
    )
    duplicated.add([{"index": 0, "function": {"name": "robot.action"}}])
    assert duplicated.build()[0]["function"]["name"] == "robot.action"
