"""DashScope 适配器测试共用的本地假服务器与 SSE 构造器（不含用例）。"""

from __future__ import annotations

import asyncio
import base64
import json

import websockets


def event(name: str, **payload) -> dict:
    return {"header": {"event": name, "task_id": "task-1"}, "payload": payload}


def sentence(text: str, *, end: bool = False) -> dict:
    return {
        "header": {"event": "result-generated", "task_id": "task-1"},
        "payload": {
            "output": {"sentence": {"text": text, "sentence_end": end}}
        },
    }


def audio_chunk(data: bytes) -> dict:
    return {
        "output": {
            "type": "sentence-synthesis",
            "audio": {"data": base64.b64encode(data).decode()},
        }
    }


def sse_bytes(*events: dict) -> bytes:
    lines = [
        f"data: {json.dumps(item, ensure_ascii=False)}\n\n" for item in events
    ]
    return "".join(lines).encode()


class FakeAsrServer:
    """按脚本回应 run-task / finish-task 的假服务端。"""

    def __init__(
        self,
        *,
        results: tuple[dict, ...] = (),
        finish_results: tuple[dict, ...] = (),
        fail_on_run: bool = False,
        fail_on_finish: bool = False,
        hang: bool = False,
        finish_with_task_finished: bool = True,
        start_delay: float = 0.0,
        drop_on_audio: bool = False,
    ) -> None:
        self.results = list(results)
        self.finish_results = list(finish_results)
        self.fail_on_run = fail_on_run
        self.fail_on_finish = fail_on_finish
        self.hang = hang
        self.finish_with_task_finished = finish_with_task_finished
        self.start_delay = start_delay
        self.drop_on_audio = drop_on_audio
        self.run_task: dict = {}
        self.finish_task: dict = {}
        self.auth = ""
        self.audio: list[bytes] = []
        self.finish_count = 0
        self.connections = 0
        self.closed = asyncio.Event()
        self.release = asyncio.Event()
        self._server = None
        self.url = ""

    async def handler(self, ws) -> None:
        self.connections += 1
        try:
            raw = await ws.recv()
            self.run_task = json.loads(raw)
            self.auth = ws.request.headers.get("authorization", "")
            if self.hang:
                await self.release.wait()
                return
            if self.fail_on_run:
                await ws.send(json.dumps(event("task-failed")))
                return
            if self.start_delay:
                await asyncio.sleep(self.start_delay)
            await ws.send(json.dumps(event("task-started")))
            for item in self.results:
                await ws.send(json.dumps(item))
            async for message in ws:
                if isinstance(message, bytes):
                    self.audio.append(message)
                    if self.drop_on_audio:
                        await ws.close()
                        break
                    continue
                data = json.loads(message)
                if data["header"]["action"] != "finish-task":
                    continue
                self.finish_count += 1
                self.finish_task = data
                for item in self.finish_results:
                    await ws.send(json.dumps(item))
                if self.fail_on_finish:
                    await ws.send(json.dumps(event("task-failed")))
                elif self.finish_with_task_finished:
                    await ws.send(json.dumps(event("task-finished")))
                break
        except websockets.ConnectionClosed:
            pass
        finally:
            self.closed.set()

    async def __aenter__(self) -> "FakeAsrServer":
        self._server = await websockets.serve(self.handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/api-ws/v1/inference"
        return self

    async def __aexit__(self, *exc) -> None:
        self.release.set()
        self._server.close()
        await self._server.wait_closed()
