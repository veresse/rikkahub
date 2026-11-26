"""真实 uvicorn + `CompanionServer`：优雅停机时 App 必须先收到设备下线。

uvicorn 默认先对连接发 1012、最后才跑 lifespan shutdown；如果只在 lifespan 里
关设备网关，App 的 `/app/ws` 已经被断开，`device.status online=false` 无人接收。
本文件用真实的 `CompanionServer.run()`（独立线程 + 独立事件循环）触发一次
`should_exit`，断言 App 在自身关闭之前收到 `(False, "server_shutdown")`。
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
from companion.server.app.server import CompanionServer
from companion.simulators.app_simulator import SimulatedApp
from companion.simulators.device_simulator import SimulatedDevice

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "test-admin-password")
CAPABILITIES = ["action", "volume", "expression"]


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("等待条件超时")


async def _pair_device(app: SimulatedApp) -> dict:
    code_resp = await app.post("/api/devices/pair-code", json={})
    assert code_resp.status_code == 200, code_resp.text
    pair_resp = await app.post(
        "/api/devices/pair",
        json={
            "code": code_resp.json()["code"],
            "device_name": "停机 E2E 机器人",
            "firmware_version": "2.0.0",
            "protocol_version": 2,
            "capabilities": CAPABILITIES,
        },
    )
    assert pair_resp.status_code == 200, pair_resp.text
    return pair_resp.json()


@pytest.fixture(scope="module")
def shutting_server():
    """启动真实的 `CompanionServer`（与 `python -m ...main` 相同的停机路径）。"""
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
    thread = threading.Thread(target=server.run, name="uvicorn-shutdown-e2e", daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("uvicorn 启动线程提前退出")
        time.sleep(0.02)
    assert server.started, "uvicorn 未能在 20 秒内启动"

    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield server, thread, f"http://127.0.0.1:{port}"
    finally:
        if thread.is_alive():
            server.should_exit = True
            thread.join(timeout=15)
        assert not thread.is_alive(), "uvicorn 未能在 15 秒内退出"


async def test_graceful_shutdown_delivers_offline_before_closing_app(shutting_server):
    server, thread, base_url = shutting_server
    app = SimulatedApp(base_url)
    await app.__aenter__()
    await app.login(password=ADMIN_PASSWORD)
    device: SimulatedDevice | None = None
    try:
        # 先连 App，再连设备：上线事件才有接收者。
        await app.connect_ws()
        paired = await _pair_device(app)
        device = SimulatedDevice(
            base_url,
            device_id=paired["device_id"],
            token=paired["device_token"],
            protocol_version=2,
            capabilities=CAPABILITIES,
            time_scale=0.01,
        )
        await device.connect()
        assert device.hello_ack is not None, (
            f"设备握手失败：close_code={device.close_code} "
            f"reason={device.close_reason}"
        )

        def is_online() -> bool:
            return any(
                event.get("type") == "device.status"
                and event.get("device_id") == paired["device_id"]
                and event.get("online") is True
                for event in app.events
            )

        await _wait_until(is_online)

        def stop() -> None:
            server.should_exit = True
            thread.join(timeout=20)

        # 在另一个线程里等停机，保持测试事件循环运行，让模拟器继续收帧。
        await asyncio.to_thread(stop)
        assert not thread.is_alive(), "优雅停机超过 20 秒"

        await _wait_until(lambda: app.close_code is not None)
        await _wait_until(lambda: device.close_code is not None)

        statuses = [
            event
            for event in app.events
            if event.get("type") == "device.status"
            and event.get("device_id") == paired["device_id"]
        ]
        assert [event["online"] for event in statuses] == [True, False], statuses
        assert statuses[-1]["reason"] == "server_shutdown"
        assert (app.close_code, app.close_reason) == (1011, "server_shutdown")
        assert (device.close_code, device.close_reason) == (
            1011,
            "server_shutdown",
        )
    finally:
        if thread.is_alive():
            server.should_exit = True
        if device is not None:
            try:
                await device.close()
            except Exception:
                pass
        try:
            await app.close_ws()
        except Exception:
            pass
        await app.__aexit__(None, None, None)
