"""App 侧模拟器：REST + `/app/ws`。

既可在 pytest 中作为客户端桩，也可命令行独立运行：

```bash
python -m companion.simulators.app_simulator \
    --base-url http://127.0.0.1:8000 --password "$ADMIN_PASSWORD"
```

安全：Token 只保存在内存，不写日志、不落盘。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from typing import Any, Callable

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

EventPredicate = Callable[[dict[str, Any]], bool]


def _ws_url(base_url: str, path: str) -> str:
    if base_url.startswith("https://"):
        root = "wss://" + base_url[len("https://") :]
    elif base_url.startswith("http://"):
        root = "ws://" + base_url[len("http://") :]
    else:
        root = base_url
    return root.rstrip("/") + path


class SimulatedApp:
    """一条 App 客户端：持有 Bearer Token、可选 `/app/ws` 连接与事件历史。"""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self.token: str = ""
        self.token_id: str = ""
        self.username: str = ""

        self.ws: Any = None
        self.events: list[dict[str, Any]] = []
        self.close_code: int | None = None
        self.close_reason: str = ""

        self._receiver: asyncio.Task | None = None
        self._closed = asyncio.Event()
        self._listeners: list[tuple[EventPredicate, asyncio.Future]] = []

    # ---- 生命周期 ----

    async def __aenter__(self) -> "SimulatedApp":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close_ws()
        await self.http.aclose()

    async def login(
        self,
        *,
        username: str = "admin",
        password: str,
        device_name: str = "simulated-app",
    ) -> dict[str, Any]:
        resp = await self.http.post(
            "/api/auth/login",
            json={"username": username, "password": password, "device_name": device_name},
        )
        resp.raise_for_status()
        payload = resp.json()
        self.token = payload["token"]
        self.token_id = payload["token_id"]
        self.username = payload.get("username", username)
        self.http.headers["Authorization"] = f"Bearer {self.token}"
        return payload

    async def connect_ws(self) -> None:
        if self.ws is not None:
            return
        self.ws = await websockets.connect(
            _ws_url(self.base_url, "/app/ws"),
            additional_headers={"Authorization": f"Bearer {self.token}"},
            max_size=None,
            ping_interval=None,
        )
        self._closed.clear()
        self.close_code = None
        self.close_reason = ""
        self._receiver = asyncio.create_task(self._receive_loop())

    async def close_ws(self) -> None:
        ws = self.ws
        self.ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if self._receiver is not None:
            try:
                await asyncio.wait_for(self._receiver, timeout=2.0)
            except Exception:
                self._receiver.cancel()
                await asyncio.gather(self._receiver, return_exceptions=True)
            self._receiver = None
        # 取消并清空等待者，避免重连后旧 listener 被新连接的事件唤醒。
        for _, future in list(self._listeners):
            if not future.done():
                future.set_exception(ConnectionError("websocket closed"))
        self._listeners.clear()
        self._closed.set()

    async def wait_closed(self, timeout: float = 5.0) -> "SimulatedApp":
        await asyncio.wait_for(self._closed.wait(), timeout=timeout)
        return self

    # ---- REST ----

    async def get(self, path: str, **kwargs) -> httpx.Response:
        return await self.http.get(path, **kwargs)

    async def post(self, path: str, **kwargs) -> httpx.Response:
        return await self.http.post(path, **kwargs)

    async def put(self, path: str, **kwargs) -> httpx.Response:
        return await self.http.put(path, **kwargs)

    async def delete(self, path: str, **kwargs) -> httpx.Response:
        return await self.http.delete(path, **kwargs)

    # ---- WebSocket ----

    async def send(self, payload: dict[str, Any]) -> None:
        if self.ws is None:
            raise RuntimeError("app ws 未连接")
        await self.ws.send(json.dumps(payload))

    async def remote(
        self,
        message_type: str,
        device_id: str,
        *,
        timeout: float = 5.0,
        **fields: Any,
    ) -> dict[str, Any]:
        """发送一条 `remote.*` 并等待同 `request_id` 的 `remote.ack`。"""
        request_id = str(uuid.uuid4())
        await self.send(
            {
                "v": 2,
                "type": message_type,
                "request_id": request_id,
                "device_id": device_id,
                **fields,
            }
        )
        return await self.wait_for(
            lambda e: e.get("type") == "remote.ack"
            and e.get("request_id") == request_id,
            timeout=timeout,
        )

    async def wait_for(
        self, predicate: EventPredicate, *, timeout: float = 5.0
    ) -> dict[str, Any]:
        # 先扫描历史，避免 ack/result 在等待前已经到达。
        for event in self.events:
            if predicate(event):
                return event

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        entry = (predicate, future)
        self._listeners.append(entry)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            try:
                self._listeners.remove(entry)
            except ValueError:
                pass

    async def chat(
        self, conversation_id: str, message: str, *, timeout: float = 20.0
    ) -> list[dict[str, Any]]:
        """调用 `/api/chat/completions` 并收集全部 SSE 事件。"""
        events: list[dict[str, Any]] = []
        async with self.http.stream(
            "POST",
            "/api/chat/completions",
            json={"conversation_id": conversation_id, "message": message},
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    events.append(json.loads(line[len("data: ") :]))
                except json.JSONDecodeError:
                    continue
        return events

    # ---- 内部 ----

    async def _receive_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                self.events.append(event)
                for predicate, future in list(self._listeners):
                    if future.done():
                        continue
                    try:
                        matched = predicate(event)
                    except Exception:
                        matched = False
                    if matched:
                        future.set_result(event)
        except ConnectionClosed as exc:
            if exc.rcvd is not None:
                self.close_code = exc.rcvd.code
                self.close_reason = exc.rcvd.reason
        finally:
            self._closed.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="App 侧协议模拟器")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD", ""))
    parser.add_argument("--device-id", default="")
    parser.add_argument("--show-events", action="store_true")
    args = parser.parse_args()

    if not args.password:
        parser.error("必须提供 --password 或 ADMIN_PASSWORD 环境变量")

    async def run() -> None:
        async with SimulatedApp(args.base_url) as app:
            await app.login(username=args.username, password=args.password)
            print(f"logged in as {app.username} token_id={app.token_id}")
            await app.connect_ws()
            print("app ws connected")
            if args.device_id:
                ack = await app.remote("remote.get_status", args.device_id)
                print("ack:", json.dumps(ack, ensure_ascii=False))
                if args.show_events:
                    print("events:", json.dumps(app.events, ensure_ascii=False, indent=2))
            else:
                while True:
                    await asyncio.sleep(3600)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
