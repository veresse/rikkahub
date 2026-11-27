"""DashScope 真实适配器（ASR WebSocket / TTS HTTP SSE）。

所有行为用本地假服务器 / `httpx.MockTransport` 验证；真实云与真机验收
标记为待办。本模块不得记录识别/合成文本、音频 payload 或 API Key。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import websockets

from companion.server.app.audio.providers import (
    ASR_ERROR,
    ASR_FINAL,
    ASR_PARTIAL,
    AsrEvent,
    ProviderError,
)

logger = logging.getLogger(__name__)
# websockets 在 DEBUG 级别会原样打印收发帧（可能是识别文本/音频），
# 这里给连接单独一个 INFO 级别的 logger，避免全局打开 DEBUG 时泄露内容。
_WS_LOGGER = logging.getLogger(f"{__name__}.ws")
_WS_LOGGER.setLevel(logging.INFO)

DEFAULT_ASR_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DEFAULT_TTS_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
)

ASR_UNAVAILABLE = "asr_unavailable"
TTS_UNAVAILABLE = "tts_unavailable"


def dashscope_asr_url(
    workspace_id: str, override: str = "", region: str = "cn-beijing"
) -> str:
    """按设计 §7.3 构造 ASR WebSocket URL；测试可传 override 指向本地假服务器。"""
    if override:
        return override
    workspace = (workspace_id or "").strip()
    if workspace:
        zone = (region or "").strip() or "cn-beijing"
        return (
            f"wss://{workspace}.{zone}.maas.aliyuncs.com"
            "/api-ws/v1/inference"
        )
    return DEFAULT_ASR_URL


def dashscope_tts_url(override: str = "") -> str:
    """TTS SSE 地址；测试可传 override 指向本地假服务器。"""
    return override or DEFAULT_TTS_URL


def _json(message: dict[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=False)


def _event_name(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    header = message.get("header")
    if not isinstance(header, dict):
        return ""
    return str(header.get("event") or "")


class DashScopeAsrProvider:
    """`fun-asr-realtime` WebSocket 适配器；懒连接，测试用本地假服务器。"""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "fun-asr-realtime",
        url: str = "",
        workspace_id: str = "",
        region: str = "cn-beijing",
        handshake_timeout: float = 5.0,
        finish_timeout: float = 2.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._url = dashscope_asr_url(workspace_id, url, region)
        self._handshake_timeout = handshake_timeout
        self._finish_timeout = finish_timeout

    def open(
        self, *, device_id: str, turn_id: str, sample_rate: int = 16000
    ) -> "DashScopeAsrStream":
        return DashScopeAsrStream(
            api_key=self._api_key,
            url=self._url,
            model=self._model,
            device_id=device_id,
            turn_id=turn_id,
            sample_rate=sample_rate,
            handshake_timeout=self._handshake_timeout,
            finish_timeout=self._finish_timeout,
        )


class DashScopeAsrStream:
    def __init__(
        self,
        *,
        api_key: str,
        url: str,
        model: str,
        device_id: str,
        turn_id: str,
        sample_rate: int,
        handshake_timeout: float,
        finish_timeout: float,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._model = model
        self._device_id = device_id
        self._turn_id = turn_id
        self._sample_rate = sample_rate
        self._handshake_timeout = handshake_timeout
        self._finish_timeout = finish_timeout

        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._queue: asyncio.Queue[AsrEvent | None] = asyncio.Queue()
        self._terminal = asyncio.Event()
        self._sentences: list[str] = []
        self._current = ""
        self._final_sent = False
        self._error_sent = False
        self._events_done = False
        self._finished = False
        self._closed = False

    # -- public API -----------------------------------------------------

    async def send(self, ogg_bytes: bytes) -> None:
        if self._closed or self._finished or self._error_sent:
            return
        if not ogg_bytes:
            return
        await self._ensure_connected()
        ws = self._ws
        if ws is None:
            raise ProviderError(ASR_UNAVAILABLE)
        try:
            await ws.send(ogg_bytes)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("dashscope asr send failed: %s", self._device_id)
            raise ProviderError(ASR_UNAVAILABLE)

    async def finish(self) -> None:
        if self._closed or self._finished:
            return
        self._finished = True
        try:
            await self._ensure_connected()
            ws = self._ws
            if ws is not None:
                await ws.send(
                    _json(
                        {
                            "header": {
                                "action": "finish-task",
                                "task_id": self._turn_id,
                                "streaming": "duplex",
                            },
                            "payload": {"input": {}},
                        }
                    )
                )
        except asyncio.CancelledError:
            raise
        except ProviderError:
            raise
        except Exception:
            logger.debug("dashscope asr finish failed: %s", self._device_id)
            raise ProviderError(ASR_UNAVAILABLE)

        try:
            await asyncio.wait_for(
                self._terminal.wait(), self._finish_timeout
            )
        except asyncio.TimeoutError:
            # 云端偶尔不发 task-finished：用已累计文本补发 final，保证轮次结束。
            logger.debug("dashscope asr finish timeout: %s", self._device_id)
        if not self._error_sent:
            self._emit_final()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        ws = self._ws
        self._ws = None
        reader = self._reader_task
        self._reader_task = None
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), 1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        if reader is not None and not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        self._queue.put_nowait(None)

    async def events(self) -> AsyncIterator[AsrEvent]:
        if self._events_done:
            return
        if self._closed and self._queue.empty():
            return
        if self._ws is None and not self._closed:
            try:
                await self._ensure_connected()
            except ProviderError:
                self._emit_error()
        while True:
            item = await self._queue.get()
            if item is None:
                self._events_done = True
                return
            yield item
            if item.kind in (ASR_FINAL, ASR_ERROR):
                self._events_done = True
                return

    # -- connection -----------------------------------------------------

    async def _ensure_connected(self) -> None:
        if self._ws is not None:
            return
        if self._closed:
            raise ProviderError(ASR_UNAVAILABLE)
        async with self._connect_lock:
            if self._ws is not None:
                return
            if self._closed:
                # 排队等锁期间可能已被 close()，不能再发起新的连接。
                raise ProviderError(ASR_UNAVAILABLE)
            await self._connect()

    async def _connect(self) -> None:
        if self._closed or not self._api_key:
            raise ProviderError(ASR_UNAVAILABLE)
        headers = {"Authorization": f"bearer {self._api_key}"}
        kwargs: dict[str, Any] = {
            "open_timeout": self._handshake_timeout,
            "ping_interval": 20,
            "ping_timeout": 20,
            "logger": _WS_LOGGER,
        }
        try:
            try:
                ws = await websockets.connect(
                    self._url, additional_headers=headers, **kwargs
                )
            except TypeError:
                # websockets < 14 的头部参数名不同。
                ws = await websockets.connect(
                    self._url, extra_headers=headers, **kwargs
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("dashscope asr connect failed: %s", self._device_id)
            raise ProviderError(ASR_UNAVAILABLE)

        try:
            await ws.send(
                _json(
                    {
                        "header": {
                            "action": "run-task",
                            "task_id": self._turn_id,
                            "streaming": "duplex",
                        },
                        "payload": {
                            "task_group": "audio",
                            "task": "asr",
                            "function": "recognition",
                            "model": self._model,
                            "parameters": {
                                "format": "opus",
                                "sample_rate": self._sample_rate,
                                "max_sentence_silence": 800,
                                "language_hints": ["zh"],
                            },
                            "input": {},
                        },
                    }
                )
            )
            await asyncio.wait_for(
                self._wait_task_started(ws), self._handshake_timeout
            )
        except asyncio.CancelledError:
            await self._close_socket(ws)
            raise
        except Exception:
            logger.debug(
                "dashscope asr handshake failed: %s", self._device_id
            )
            await self._close_socket(ws)
            raise ProviderError(ASR_UNAVAILABLE)

        if self._closed:
            # close() 可能发生在握手期间：此时不能再把连接装进实例，否则
            # 已建立的 WS 与读任务都会泄漏。
            await self._close_socket(ws)
            raise ProviderError(ASR_UNAVAILABLE)
        self._ws = ws
        self._reader_task = asyncio.create_task(self._reader_loop(ws))

    async def _wait_task_started(self, ws: Any) -> None:
        while True:
            raw = await ws.recv()
            if isinstance(raw, bytes):
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            event = _event_name(message)
            if event == "task-started":
                return
            if event == "task-failed":
                raise ProviderError(ASR_UNAVAILABLE)

    async def _close_socket(self, ws: Any) -> None:
        try:
            await asyncio.wait_for(ws.close(), 1.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    # -- inbound --------------------------------------------------------

    async def _reader_loop(self, ws: Any) -> None:
        try:
            async for raw in ws:
                if self._closed:
                    break
                if isinstance(raw, bytes):
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    logger.debug(
                        "dashscope asr bad json: %s", self._device_id
                    )
                    continue
                event = _event_name(message)
                if event == "result-generated":
                    self._handle_result(message)
                elif event == "task-finished":
                    self._emit_final()
                    return
                elif event == "task-failed":
                    logger.debug(
                        "dashscope asr task failed: %s", self._device_id
                    )
                    self._emit_error()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("dashscope asr socket closed: %s", self._device_id)
        finally:
            if self._closed or self._final_sent or self._error_sent:
                return
            if self._finished:
                # 已发 finish-task，对端直接关连接也算正常收尾：用累计文本补 final。
                self._emit_final()
            else:
                self._emit_error()

    def _handle_result(self, message: dict[str, Any]) -> None:
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return
        output = payload.get("output")
        if not isinstance(output, dict):
            return
        sentence = output.get("sentence")
        if not isinstance(sentence, dict):
            return
        text = str(sentence.get("text") or "")
        if not text:
            return
        self._current = text
        if sentence.get("sentence_end"):
            self._sentences.append(text)
            self._current = ""
        logger.debug(
            "dashscope asr result: %s len=%d", self._device_id, len(text)
        )
        self._queue.put_nowait(AsrEvent(ASR_PARTIAL, text=text))

    def _final_text(self) -> str:
        return "".join(self._sentences) + self._current

    def _emit_final(self) -> None:
        if self._final_sent or self._error_sent:
            return
        self._final_sent = True
        logger.debug(
            "dashscope asr final: %s len=%d",
            self._device_id,
            len(self._final_text()),
        )
        self._queue.put_nowait(AsrEvent(ASR_FINAL, text=self._final_text()))
        self._queue.put_nowait(None)
        self._terminal.set()

    def _emit_error(self) -> None:
        if self._error_sent or self._final_sent:
            return
        self._error_sent = True
        self._queue.put_nowait(AsrEvent(ASR_ERROR, code=ASR_UNAVAILABLE))
        self._queue.put_nowait(None)
        self._terminal.set()


class DashScopeTtsProvider:
    """`qwen-audio-3.0-tts-flash` HTTP SSE 适配器。

    只接受流式 base64 音频；`output.audio.url`（非流式）按错误处理，
    不做二次下载。每次 `synthesize()` 使用独立的 httpx 客户端，取消时
    由 `async with` 关闭连接。
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "qwen-audio-3.0-tts-flash",
        voice: str = "cherry",
        url: str = "",
        sample_rate: int = 24000,
        bit_rate: int = 32,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._url = dashscope_tts_url(url)
        self._sample_rate = sample_rate
        self._bit_rate = bit_rate
        self._client_factory = client_factory
        self._timeout = timeout

    def synthesize(
        self, text: str, *, turn_id: str, segment_seq: int
    ) -> AsyncIterator[bytes]:
        return self._stream(text, turn_id=turn_id, segment_seq=segment_seq)

    async def _stream(
        self, text: str, *, turn_id: str, segment_seq: int
    ) -> AsyncIterator[bytes]:
        if not self._api_key:
            raise ProviderError(TTS_UNAVAILABLE)
        body = {
            "model": self._model,
            "input": {
                "text": text,
                "voice": self._voice,
                "format": "opus",
                "sample_rate": self._sample_rate,
                "bit_rate": self._bit_rate,
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-DashScope-SSE": "enable",
        }
        factory = self._client_factory or (
            lambda: httpx.AsyncClient(timeout=self._timeout)
        )
        try:
            async with factory() as client:
                async with client.stream(
                    "POST", self._url, headers=headers, json=body
                ) as response:
                    if response.status_code != 200:
                        logger.debug(
                            "dashscope tts http status=%s: %s",
                            response.status_code,
                            turn_id,
                        )
                        raise ProviderError(TTS_UNAVAILABLE)
                    async for chunk in self._iter_sse(response, turn_id):
                        yield chunk
        except ProviderError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("dashscope tts stream failed: %s", turn_id)
            raise ProviderError(TTS_UNAVAILABLE)

    async def _iter_sse(
        self, response: httpx.Response, turn_id: str
    ) -> AsyncIterator[bytes]:
        async for line in response.aiter_lines():
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            if payload == "[DONE]":
                return
            try:
                message = json.loads(payload)
            except ValueError:
                logger.debug("dashscope tts bad json: %s", turn_id)
                raise ProviderError(TTS_UNAVAILABLE)
            if not isinstance(message, dict):
                continue
            output = message.get("output")
            if not isinstance(output, dict):
                continue
            audio = output.get("audio")
            if isinstance(audio, dict):
                if audio.get("url"):
                    # 非流式返回：不做二次 HTTP 下载，按设计明确报错。
                    logger.debug("dashscope tts returned audio url: %s", turn_id)
                    raise ProviderError(TTS_UNAVAILABLE)
                encoded = audio.get("data")
                if encoded:
                    try:
                        chunk = base64.b64decode(str(encoded), validate=True)
                    except (binascii.Error, ValueError):
                        logger.debug("dashscope tts bad base64: %s", turn_id)
                        raise ProviderError(TTS_UNAVAILABLE)
                    if chunk:
                        logger.debug(
                            "dashscope tts chunk: %s len=%d",
                            turn_id,
                            len(chunk),
                        )
                        yield chunk
            if (
                output.get("finish_reason") == "stop"
                or output.get("type") == "sentence-end"
            ):
                return
