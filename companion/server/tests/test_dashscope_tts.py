"""DashScope TTS 适配器：`httpx.MockTransport` 假 SSE，不联网。"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from companion.server.app.audio.dashscope import DashScopeTtsProvider
from companion.server.app.audio.providers import ProviderError
from companion.server.tests.dashscope_fakes import (
    audio_chunk as _chunk,
    sse_bytes as _sse,
)

API_KEY = "sk-test-secret-do-not-log"
MODEL = "qwen-audio-3.0-tts-flash"
VOICE = "cherry"
TEXT = "机密合成文本不要进日志"


def _provider(handler, **kwargs) -> DashScopeTtsProvider:
    transport = httpx.MockTransport(handler)
    return DashScopeTtsProvider(
        API_KEY,
        model=MODEL,
        voice=VOICE,
        url="http://127.0.0.1:9002/tts",
        client_factory=lambda: httpx.AsyncClient(transport=transport),
        **kwargs,
    )


async def test_streams_base64_chunks_and_sends_expected_request():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                _chunk(b"ogg-1"),
                _chunk(b"ogg-2"),
                {"output": {"finish_reason": "stop"}},
            ),
        )

    provider = _provider(handler)
    chunks = [
        chunk async for chunk in provider.synthesize(
            TEXT, turn_id="turn-1", segment_seq=0
        )
    ]

    assert chunks == [b"ogg-1", b"ogg-2"]
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:9002/tts"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["x-dashscope-sse"] == "enable"
    assert json.loads(request.content) == {
        "model": MODEL,
        "input": {
            "text": TEXT,
            "voice": VOICE,
            "format": "opus",
            "sample_rate": 24000,
            "bit_rate": 32,
        },
    }


async def test_done_sentinel_finishes_stream():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_chunk(b"ogg-1")) + b"data: [DONE]\n\n",
        )

    chunks = [
        chunk
        async for chunk in _provider(handler).synthesize(
            TEXT, turn_id="turn-1", segment_seq=1
        )
    ]
    assert chunks == [b"ogg-1"]


async def test_audio_url_is_rejected_without_downloading():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {
                    "output": {
                        "type": "sentence-synthesis",
                        "audio": {"url": "https://example.invalid/a.ogg"},
                    }
                }
            ),
        )

    with pytest.raises(ProviderError) as exc:
        [
            chunk
            async for chunk in _provider(handler).synthesize(
                TEXT, turn_id="turn-1", segment_seq=0
            )
        ]
    assert exc.value.code == "tts_unavailable"


@pytest.mark.parametrize("status", [401, 500])
async def test_http_error_maps_to_tts_unavailable(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"denied")

    with pytest.raises(ProviderError) as exc:
        [
            chunk
            async for chunk in _provider(handler).synthesize(
                TEXT, turn_id="turn-1", segment_seq=0
            )
        ]
    assert exc.value.code == "tts_unavailable"


async def test_bad_json_maps_to_tts_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: {not json}\n\n",
        )

    with pytest.raises(ProviderError) as exc:
        [
            chunk
            async for chunk in _provider(handler).synthesize(
                TEXT, turn_id="turn-1", segment_seq=0
            )
        ]
    assert exc.value.code == "tts_unavailable"


async def test_bad_base64_maps_to_tts_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse({"output": {"audio": {"data": "!!!not-base64!!!"}}}),
        )

    with pytest.raises(ProviderError) as exc:
        [
            chunk
            async for chunk in _provider(handler).synthesize(
                TEXT, turn_id="turn-1", segment_seq=0
            )
        ]
    assert exc.value.code == "tts_unavailable"


async def test_connect_error_maps_to_tts_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ProviderError) as exc:
        [
            chunk
            async for chunk in _provider(handler).synthesize(
                TEXT, turn_id="turn-1", segment_seq=0
            )
        ]
    assert exc.value.code == "tts_unavailable"


async def test_missing_api_key_fails_without_any_request():
    called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["count"] += 1
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    provider = DashScopeTtsProvider(
        "",
        url="http://127.0.0.1:9002/tts",
        client_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    with pytest.raises(ProviderError) as exc:
        [
            chunk
            async for chunk in provider.synthesize(
                TEXT, turn_id="turn-1", segment_seq=0
            )
        ]
    assert exc.value.code == "tts_unavailable"
    assert called["count"] == 0


class GatedSseStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self):
        yield _sse(_chunk(b"ogg-1"))
        await self.release.wait()

    async def aclose(self) -> None:
        self.closed.set()


async def test_aclose_mid_stream_is_bounded_and_closes_transport():
    stream = GatedSseStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    generator = _provider(handler).synthesize(
        TEXT, turn_id="turn-1", segment_seq=0
    )
    assert await generator.__anext__() == b"ogg-1"
    await asyncio.wait_for(generator.aclose(), 2.0)
    assert stream.closed.is_set()


async def test_logs_contain_no_text_or_key(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_chunk(b"ogg-1"), {"output": {"finish_reason": "stop"}}),
        )

    with caplog.at_level(logging.DEBUG):
        [chunk async for chunk in _provider(handler).synthesize(
            TEXT, turn_id="turn-1", segment_seq=0
        )]

    ours = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("companion.server.app.audio")
    )
    assert TEXT not in ours
    assert API_KEY not in ours
