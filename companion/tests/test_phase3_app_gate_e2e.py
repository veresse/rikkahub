"""真实 uvicorn：停机窗口内新连入的 `/app/ws` 必须收到 1011。

`AppGateway` 的单测用 FakeChannel 覆盖了分支；这里走真实 TCP + Starlette 适配，
确认 `handle()` 在进入接收循环前 close(1011) 不会被框架再改写（例如 1012）。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest
import uvicorn

from companion.server.app.config import settings
from companion.server.app.main import app as fastapi_app
from companion.server.app.routers import ws
from companion.server.app.server import CompanionServer
from companion.simulators.app_simulator import SimulatedApp

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "test-admin-password")


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("等待条件超时")


@pytest.fixture()
def running_server():
    """每个用例独立进程内服务，避免上一个用例的停机状态串场。"""
    config = uvicorn.Config(
        app=fastapi_app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        log_config=None,
        access_log=False,
        lifespan="on",
        ws_max_size=settings.WS_TRANSPORT_MAX_MESSAGE_BYTES,
        ws_ping_interval=None,
        ws_ping_timeout=None,
    )
    server = CompanionServer(config)
    thread = threading.Thread(
        target=server.run, name="uvicorn-app-gate-e2e", daemon=True
    )
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("uvicorn 启动线程提前退出")
        time.sleep(0.02)
    assert server.started, "uvicorn 未能在 20 秒内启动"

    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        if thread.is_alive():
            server.should_exit = True
            thread.join(timeout=15)
        assert not thread.is_alive(), "uvicorn 未能在 15 秒内退出"
        # 停机把模块级网关置位了；复位，避免同一 pytest 进程里的后续用例受影响。
        ws.device_gateway.open_for_connections()
        ws.app_gateway.open_for_connections()


async def test_new_app_connection_after_shutdown_started_gets_1011(running_server):
    base_url = running_server
    app = SimulatedApp(base_url)
    await app.__aenter__()
    try:
        await app.login(password=ADMIN_PASSWORD)
        # 等价于 `AppGateway.shutdown()` 已置标志、正在关闭已有连接。
        ws.app_gateway._shutting_down = True
        await app.connect_ws()
        await _wait_until(lambda: app.close_code is not None)
        assert (app.close_code, app.close_reason) == (1011, "server_shutdown")
    finally:
        try:
            await app.close_ws()
        except Exception:
            pass
        await app.__aexit__(None, None, None)
