"""DashScope ASR 适配器：本地假 WebSocket 服务器，不联网、不使用真实 Key。"""

from __future__ import annotations

import asyncio
import logging

import pytest
import websockets

from companion.server.app.audio.dashscope import DashScopeAsrProvider
from companion.server.app.audio.providers import (
    ASR_ERROR,
    ASR_FINAL,
    ASR_PARTIAL,
    ProviderError,
)
from companion.server.tests.dashscope_fakes import (
    FakeAsrServer,
    sentence as _sentence,
)

API_KEY = "sk-test-secret-do-not-log"
SECRET_TEXT = "机密口令不要进日志"



def _provider(fake: FakeAsrServer, **kwargs) -> DashScopeAsrProvider:
    return DashScopeAsrProvider(API_KEY, url=fake.url, **kwargs)


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_handshake_payload_headers_and_audio_forwarding():
    async with FakeAsrServer() as fake:
        stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
        await stream.send(b"\x01\x02\x03")
        await _wait_for(lambda: bool(fake.audio))
        assert fake.audio == [b"\x01\x02\x03"]
        assert fake.auth == f"bearer {API_KEY}"

        header = fake.run_task["header"]
        payload = fake.run_task["payload"]
        assert header["action"] == "run-task"
        assert header["streaming"] == "duplex"
        assert header["task_id"] == "turn-1"
        assert payload["task_group"] == "audio"
        assert payload["task"] == "asr"
        assert payload["function"] == "recognition"
        assert payload["model"] == "fun-asr-realtime"
        assert payload["parameters"] == {
            "format": "opus",
            "sample_rate": 16000,
            "max_sentence_silence": 800,
            "language_hints": ["zh"],
        }
        await stream.close()


async def test_partial_then_single_final_with_accumulated_text():
    async with FakeAsrServer(
        results=(_sentence(SECRET_TEXT),),
        finish_results=(
            _sentence(SECRET_TEXT, end=True),
            _sentence("第二句", end=True),
        ),
    ) as fake:
        stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
        await stream.send(b"\x01")
        await stream.finish()
        events = [event async for event in stream.events()]
        await stream.close()

    partials = [event for event in events if event.kind == ASR_PARTIAL]
    finals = [event for event in events if event.kind == ASR_FINAL]
    assert partials and partials[-1].text == "第二句"
    assert len(finals) == 1
    assert finals[0].text == SECRET_TEXT + "第二句"
    assert [event.kind for event in events if event.kind == ASR_ERROR] == []


async def test_finish_sends_finish_task_once():
    async with FakeAsrServer() as fake:
        stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
        await stream.finish()
        await stream.finish()
        assert fake.finish_count == 1
        assert fake.finish_task["header"]["action"] == "finish-task"
        assert fake.finish_task["header"]["task_id"] == "turn-1"
        await stream.close()


async def test_no_speech_yields_one_empty_final():
    async with FakeAsrServer() as fake:
        stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
        await stream.finish()
        events = [event async for event in stream.events()]
        await stream.close()

    finals = [event for event in events if event.kind == ASR_FINAL]
    assert len(finals) == 1
    assert finals[0].text == ""


async def test_finish_timeout_falls_back_to_accumulated_text():
    async with FakeAsrServer(
        results=(_sentence(SECRET_TEXT),),
        finish_with_task_finished=False,
    ) as fake:
        stream = _provider(fake, finish_timeout=0.2).open(
            device_id="dev-1", turn_id="turn-1"
        )
        await stream.send(b"\x01")
        await stream.finish()
        events = [event async for event in stream.events()]
        await stream.close()

    finals = [event for event in events if event.kind == ASR_FINAL]
    assert len(finals) == 1
    assert finals[0].text == SECRET_TEXT


async def test_task_failed_on_handshake_reports_asr_unavailable():
    async with FakeAsrServer(fail_on_run=True) as fake:
        stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
        events = [event async for event in stream.events()]
        await stream.close()

    assert [event.kind for event in events] == [ASR_ERROR]
    assert events[0].code == "asr_unavailable"


async def test_task_failed_after_finish_reports_asr_unavailable():
    async with FakeAsrServer(fail_on_finish=True) as fake:
        stream = _provider(fake, finish_timeout=0.5).open(
            device_id="dev-1", turn_id="turn-1"
        )
        await stream.finish()
        events = [event async for event in stream.events()]
        await stream.close()

    errors = [event for event in events if event.kind == ASR_ERROR]
    assert len(errors) == 1
    assert errors[0].code == "asr_unavailable"


async def test_handshake_timeout_is_bounded():
    async with FakeAsrServer(hang=True) as fake:
        stream = _provider(fake, handshake_timeout=0.2).open(
            device_id="dev-1", turn_id="turn-1"
        )
        with pytest.raises(ProviderError) as exc:
            await asyncio.wait_for(stream.send(b"\x01"), 2.0)
        assert exc.value.code == "asr_unavailable"
        await stream.close()


async def test_connection_refused_is_bounded():
    server = await websockets.serve(lambda ws: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    provider = DashScopeAsrProvider(
        API_KEY,
        url=f"ws://127.0.0.1:{port}/api-ws/v1/inference",
        handshake_timeout=0.5,
    )
    stream = provider.open(device_id="dev-1", turn_id="turn-1")
    with pytest.raises(ProviderError) as exc:
        await asyncio.wait_for(stream.send(b"\x01"), 2.0)
    assert exc.value.code == "asr_unavailable"
    await stream.close()


async def test_close_is_idempotent_and_stops_the_server_session():
    async with FakeAsrServer() as fake:
        stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
        await stream.send(b"\x01")
        await stream.close()
        await stream.close()
        await asyncio.wait_for(fake.closed.wait(), 2.0)

        # 关闭后 events() 立即结束，不抛出。
        assert [event async for event in stream.events()] == []


async def test_close_during_connect_closes_the_socket():
    async with FakeAsrServer(start_delay=0.2) as fake:
        stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
        sending = asyncio.create_task(stream.send(b"\x01"))
        await asyncio.sleep(0.05)
        await stream.close()
        with pytest.raises(ProviderError) as exc:
            await asyncio.wait_for(sending, 2.0)
        assert exc.value.code == "asr_unavailable"
        await asyncio.wait_for(fake.closed.wait(), 2.0)


async def test_close_during_handshake_only_connects_once():
    async with FakeAsrServer(start_delay=0.2) as fake:
        stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
        first = asyncio.create_task(stream.send(b"\x01"))
        second = asyncio.create_task(stream.send(b"\x02"))
        await asyncio.sleep(0.05)
        await stream.close()
        for task in (first, second):
            with pytest.raises(ProviderError) as exc:
                await asyncio.wait_for(task, 2.0)
            assert exc.value.code == "asr_unavailable"
        await asyncio.wait_for(fake.closed.wait(), 2.0)
    # 第二个 send 在 close 后拿到锁，不应再发起一次握手连接。
    assert fake.connections == 1


async def test_server_disconnect_mid_stream_reports_asr_unavailable():
    async with FakeAsrServer(drop_on_audio=True) as fake:
        stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
        await stream.send(b"\x01")
        events = [event async for event in stream.events()]
        await stream.close()

    assert [event.kind for event in events] == [ASR_ERROR]
    assert events[0].code == "asr_unavailable"


async def test_logs_contain_no_text_or_key(caplog):
    with caplog.at_level(logging.DEBUG):
        async with FakeAsrServer(
            results=(_sentence(SECRET_TEXT),),
            finish_results=(_sentence(SECRET_TEXT, end=True),),
        ) as fake:
            stream = _provider(fake).open(device_id="dev-1", turn_id="turn-1")
            await stream.send(b"\x01")
            await stream.finish()
            [event async for event in stream.events()]
            await stream.close()

    ours = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("companion.server.app.audio")
    )
    assert SECRET_TEXT not in ours
    assert API_KEY not in ours
