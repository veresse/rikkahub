"""ChatOrchestrator 工具循环集成测试（离线 Mock LLM，不依赖云密钥）。

覆盖：
- 在线 v2 设备 + 动作联动 → 注入 5 个 robot.* 工具、执行动作、结果回传模型续说
- 未绑定/离线设备 → 不注入工具、注入轻量状态提示
- 角色关闭动作联动 → 不注入工具、也不加提示
- 单轮 3 次动作上限、非法 JSON 参数、工具回合上限
"""

import json

import pytest
from sqlalchemy import delete

from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.device import DeviceModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.services import orchestrator as orchestrator_module
from companion.server.app.services.action_orchestrator import reset_action_schedulers
from companion.server.app.services.device_registry import (
    ActionResult,
    register_device_link,
    reset_device_links,
)
from companion.server.app.services.orchestrator import ChatOrchestrator

ASSISTANT_ID = "asst-action-loop"
CONVERSATION_ID = "conv-action-loop"
DEVICE_ID = "dev-action-loop"


class FakeLink:
    def __init__(self, result: ActionResult | None = None):
        self.result = result or ActionResult(status="done")
        self.commands: list[dict] = []
        self.status_calls = 0

    async def send(self, command: dict) -> ActionResult:
        self.commands.append(command)
        return self.result

    async def get_status(self) -> dict:
        self.status_calls += 1
        return {"volume": 70, "state": "idle", "pan": 0.0, "tilt": 0.0}


class ScriptedDeepSeek:
    """按脚本逐轮产出 chunk，并记录每轮实际收到的 payload。"""

    def __init__(self, rounds: list[list[dict]]):
        self.rounds = rounds
        self.calls: list[dict] = []

    async def stream_chat(self, messages, model=None, temperature=None, top_p=None, tools=None):
        self.calls.append(
            {"messages": json.loads(json.dumps(messages, default=str)), "tools": tools}
        )
        chunks = self.rounds.pop(0) if self.rounds else [
            {"type": "content", "delta": ""},
            {"type": "done", "finish_reason": "stop"},
        ]
        for chunk in chunks:
            yield chunk


def tool_call(index: int, call_id: str, name: str, arguments: str) -> dict:
    return {
        "type": "tool_call",
        "delta": [
            {
                "index": index,
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


def content(text: str) -> dict:
    return {"type": "content", "delta": text}


async def seed(
    session,
    *,
    enable_action_sync: bool = True,
    with_device: bool = True,
    protocol_version: int = 2,
    capabilities: tuple[str, ...] = ("action",),
) -> None:
    session.add(
        AssistantModel(
            id=ASSISTANT_ID,
            name="测试角色",
            system_prompt="你是测试角色。",
            enable_web_search=False,
            enable_action_sync=enable_action_sync,
        )
    )
    session.add(ConversationModel(id=CONVERSATION_ID, assistant_id=ASSISTANT_ID, title="测试"))
    await session.flush()
    if with_device:
        session.add(
            DeviceModel(
                id=DEVICE_ID,
                name="测试机器人",
                token_hash="a" * 64,
                protocol_version=protocol_version,
                capabilities_json=list(capabilities),
                bound_assistant_id=ASSISTANT_ID,
                bound_conversation_id=CONVERSATION_ID,
                last_seen_at=1,
            )
        )
    await session.commit()


async def cleanup(session) -> None:
    await session.rollback()
    await session.execute(
        delete(MessageNodeModel).where(MessageNodeModel.conversation_id == CONVERSATION_ID)
    )
    await session.execute(delete(DeviceModel).where(DeviceModel.id == DEVICE_ID))
    await session.execute(delete(ConversationModel).where(ConversationModel.id == CONVERSATION_ID))
    await session.execute(delete(AssistantModel).where(AssistantModel.id == ASSISTANT_ID))
    await session.commit()


@pytest.fixture
async def session():
    async with AsyncSessionLocal() as s:
        yield s
        await cleanup(s)


@pytest.fixture(autouse=True)
def _clean_links():
    reset_device_links()
    reset_action_schedulers()
    yield
    reset_device_links()
    reset_action_schedulers()


def _install(monkeypatch, fake: ScriptedDeepSeek) -> None:
    monkeypatch.setattr(orchestrator_module, "DeepSeekClient", lambda: fake)


def _events(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


async def test_online_device_executes_action_and_model_continues(session, monkeypatch):
    await seed(session)
    link = FakeLink()
    register_device_link(DEVICE_ID, link)

    fake = ScriptedDeepSeek(
        [
            [
                content("好的，我点点头。"),
                tool_call(0, "call_1", "robot.action", '{"name": "nod", "intensity": 3}'),
            ],
            [content("嗯嗯，收到。"), {"type": "done", "finish_reason": "stop"}],
        ]
    )
    _install(monkeypatch, fake)

    events = [
        e
        async for e in ChatOrchestrator.stream_chat(session, CONVERSATION_ID, "用力点点头")
    ]

    # 1. 首轮注入了全部 robot.* 工具
    first_tools = {t["function"]["name"] for t in fake.calls[0]["tools"]}
    assert first_tools == {
        "robot.action",
        "robot.move_head",
        "robot.set_volume",
        "robot.get_status",
        "robot.set_idle",
    }

    # 2. 动作按协议下发且参数来自模型
    action_commands = [c for c in link.commands if c["type"] == "action.play"]
    assert len(action_commands) == 1
    command = action_commands[0]
    assert command["name"] == "nod"
    assert command["intensity"] == 3
    assert command["speed"] == 2
    assert command["repeat"] == 1
    assert command["source"] == "llm"

    # 3. 工具结果作为 tool 消息回传模型
    tool_messages = [
        m for m in fake.calls[1]["messages"] if m.get("role") == "tool"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert json.loads(tool_messages[0]["content"])["ok"] is True

    # 4. 流里既能看到动作执行结果，也能看到模型续说
    tool_results = _events(events, "tool_result")
    assert len(tool_results) == 1
    assert tool_results[0]["result"]["status"] == "done"
    assert "嗯嗯，收到。" in "".join(
        e["delta"] for e in _events(events, "content")
    )

    # 5. 落库：用户节点 + 助手节点，助手消息含两轮文本
    nodes = list(
        (
            await session.execute(
                MessageNodeModel.__table__.select()
                .where(MessageNodeModel.conversation_id == CONVERSATION_ID)
                .order_by(MessageNodeModel.node_index)
            )
        ).mappings()
    )
    assert len(nodes) == 2
    assistant_parts = nodes[1]["messages"][0]["parts"]
    text = "".join(p.get("text", "") for p in assistant_parts)
    assert text == "好的，我点点头。嗯嗯，收到。"


async def test_offline_device_gets_note_and_no_tools(session, monkeypatch):
    await seed(session)  # 绑定设备但不注册链路 → 离线
    fake = ScriptedDeepSeek([[content("我现在动不了。")]])
    _install(monkeypatch, fake)

    events = [
        e
        async for e in ChatOrchestrator.stream_chat(session, CONVERSATION_ID, "点点头")
    ]

    assert fake.calls[0]["tools"] is None
    system_contents = [
        m["content"] for m in fake.calls[0]["messages"] if m.get("role") == "system"
    ]
    assert any("离线" in text for text in system_contents)
    assert _events(events, "tool_result") == []


async def test_unbound_device_gets_unbound_note(session, monkeypatch):
    await seed(session, with_device=False)
    fake = ScriptedDeepSeek([[content("还没有绑定机器人哦。")]])
    _install(monkeypatch, fake)

    _ = [
        e
        async for e in ChatOrchestrator.stream_chat(session, CONVERSATION_ID, "点点头")
    ]

    assert fake.calls[0]["tools"] is None
    system_contents = [
        m["content"] for m in fake.calls[0]["messages"] if m.get("role") == "system"
    ]
    assert any("没有绑定" in text for text in system_contents)


async def test_action_sync_disabled_gets_no_tools_and_no_note(session, monkeypatch):
    await seed(session, enable_action_sync=False)
    register_device_link(DEVICE_ID, FakeLink())
    fake = ScriptedDeepSeek([[content("好呀。")]])
    _install(monkeypatch, fake)

    _ = [
        e
        async for e in ChatOrchestrator.stream_chat(session, CONVERSATION_ID, "点点头")
    ]

    assert fake.calls[0]["tools"] is None
    system_contents = [
        m["content"] for m in fake.calls[0]["messages"] if m.get("role") == "system"
    ]
    assert all("实体机器人" not in text for text in system_contents)


async def test_v1_device_gets_no_tools(session, monkeypatch):
    await seed(session, protocol_version=1)
    register_device_link(DEVICE_ID, FakeLink())
    fake = ScriptedDeepSeek([[content("好呀。")]])
    _install(monkeypatch, fake)

    _ = [
        e
        async for e in ChatOrchestrator.stream_chat(session, CONVERSATION_ID, "点点头")
    ]

    assert fake.calls[0]["tools"] is None
    system_contents = [
        m["content"] for m in fake.calls[0]["messages"] if m.get("role") == "system"
    ]
    assert any("离线" in text for text in system_contents)


async def test_fourth_action_call_is_blocked(session, monkeypatch):
    await seed(session)
    link = FakeLink()
    register_device_link(DEVICE_ID, link)
    fake = ScriptedDeepSeek(
        [
            [
                tool_call(0, "c1", "robot.action", '{"name": "nod"}'),
                tool_call(1, "c2", "robot.action", '{"name": "shake"}'),
                tool_call(2, "c3", "robot.action", '{"name": "bow"}'),
                tool_call(3, "c4", "robot.action", '{"name": "nod"}'),
            ],
            [content("做完了。")],
        ]
    )
    _install(monkeypatch, fake)

    events = [
        e
        async for e in ChatOrchestrator.stream_chat(session, CONVERSATION_ID, "连续做四个动作")
    ]

    results = [e["result"] for e in _events(events, "tool_result")]
    assert len(results) == 4
    assert [r["ok"] for r in results] == [True, True, True, False]
    assert results[-1]["error"] == "action_limit_exceeded"
    assert len([c for c in link.commands if c["type"] == "action.play"]) == 3


async def test_invalid_json_arguments_are_rejected_without_device_command(session, monkeypatch):
    await seed(session)
    link = FakeLink()
    register_device_link(DEVICE_ID, link)
    fake = ScriptedDeepSeek(
        [
            [tool_call(0, "c1", "robot.action", "{not-json")],
            [content("参数不对。")],
        ]
    )
    _install(monkeypatch, fake)

    events = [
        e
        async for e in ChatOrchestrator.stream_chat(session, CONVERSATION_ID, "点点头")
    ]

    results = [e["result"] for e in _events(events, "tool_result")]
    assert results == [{"ok": False, "error": "invalid_arguments"}]
    assert link.commands == []


async def test_tool_round_limit_terminates_runaway_loop(session, monkeypatch):
    await seed(session)
    link = FakeLink()
    register_device_link(DEVICE_ID, link)

    runaway = [[tool_call(0, f"c{i}", "robot.action", '{"name": "nod"}')] for i in range(10)]
    fake = ScriptedDeepSeek(runaway)
    _install(monkeypatch, fake)

    events = [
        e
        async for e in ChatOrchestrator.stream_chat(session, CONVERSATION_ID, "一直点头")
    ]

    results = [e["result"] for e in _events(events, "tool_result")]
    errors = [r.get("error") for r in results]
    assert "round_limit_exceeded" in errors
    # 前 3 轮正常执行，之后不再下发设备
    assert len([c for c in link.commands if c["type"] == "action.play"]) == 3
    assert len(fake.calls) <= 5
