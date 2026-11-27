"""语音运行时的热替换：已连接会话下一轮必须用新 Key/模型。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from companion.server.app.services.device_gateway import DeviceGateway


class _FakeSession:
    def __init__(self) -> None:
        self.voice = SimpleNamespace(state="idle")
        self.voice_runtime = "old-runtime"
        self._voice_begin_task = None
        self.closed: list[bool] = []

    async def close_voice(self) -> None:
        self.voice = None
        self.closed.append(True)


def _session() -> _FakeSession:
    return _FakeSession()


async def test_swapping_runtime_closes_idle_voice_session():
    gateway = DeviceGateway()
    session = _session()
    gateway._sessions.add(session)

    gateway.set_voice_runtime("new-runtime")
    # close_voice 是异步的；让事件循环跑一次。
    await asyncio.sleep(0)

    assert session.voice is None
    assert session.closed == [True]
    assert session.voice_runtime == "new-runtime"


async def test_same_runtime_does_not_abort_current_session():
    runtime = object()
    gateway = DeviceGateway(voice_runtime=runtime)
    session = _session()
    gateway._sessions.add(session)

    gateway.set_voice_runtime(runtime)
    await asyncio.sleep(0)

    assert session.voice is not None
    assert session.closed == []


async def test_swapping_to_none_closes_session():
    gateway = DeviceGateway(voice_runtime="old-runtime")
    session = _session()
    gateway._sessions.add(session)

    gateway.set_voice_runtime(None)
    await asyncio.sleep(0)

    assert session.voice is None
    assert session.closed == [True]
    assert session.voice_runtime is None
