"""`CompanionServer` 停机入口的单元测试（不启动真实 uvicorn）。

真实 uvicorn 下的停机顺序由 `companion/tests/test_phase3_shutdown_e2e.py` 覆盖；
这里只锁定分支选择与「应用层关闭失败/超重时仍然收尾」的行为。
"""

import asyncio
import time

import pytest
import uvicorn

import companion.server.app.server as server_module


def _config(**kwargs) -> uvicorn.Config:
    return uvicorn.Config("companion.server.app.main:app", **kwargs)


def test_run_uses_change_reload_when_config_asks(monkeypatch):
    """`DEBUG=true` 时 `run()` 必须走 ChangeReload，而不是静默单进程。"""
    calls: list[str] = []

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self, sockets=None):
            calls.append("server")

    class FakeChangeReload:
        def __init__(self, config, target, sockets):
            self.target = target
            self.sockets = sockets

        def run(self):
            calls.append("reload")

    monkeypatch.setattr(server_module, "CompanionServer", FakeServer)
    monkeypatch.setattr(server_module, "ChangeReload", FakeChangeReload)
    config = _config(reload=True)
    monkeypatch.setattr(config, "bind_socket", lambda: None)

    server_module.run(config)

    assert calls == ["reload"]


def test_run_starts_companion_server_without_reload(monkeypatch):
    calls: list[str] = []

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self, sockets=None):
            calls.append("server")

    class FakeChangeReload:
        def __init__(self, config, target, sockets):
            calls.append("reload")

        def run(self):
            calls.append("reload-run")

    monkeypatch.setattr(server_module, "CompanionServer", FakeServer)
    monkeypatch.setattr(server_module, "ChangeReload", FakeChangeReload)

    server_module.run(_config())

    assert calls == ["server"]


def test_run_rejects_multiple_workers():
    """多 worker 下停机顺序保证不成立，必须显式拒绝而不是静默降级。"""
    with pytest.raises(SystemExit):
        server_module.run(_config(workers=2))


def test_run_rejects_reload_with_object_app(monkeypatch):
    """reload 需要子进程重新导入应用；对象 app 会静默失去 reload。"""

    class BoomServer:
        def __init__(self, config):
            self.config = config

        def run(self, *args, **kwargs):
            raise AssertionError("必须拒绝，而不是真的启动服务")

    monkeypatch.setattr(server_module, "CompanionServer", BoomServer)
    with pytest.raises(SystemExit):
        server_module.run(uvicorn.Config(object(), reload=True))


def test_build_config_sets_graceful_shutdown_timeout():
    """1012 之后的连接 drain 也必须有界，否则整个停机仍可能无界。"""
    from companion.server.app.main import build_config
    from companion.server.app.services.application_shutdown import (
        GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    )

    config = build_config()

    assert config.timeout_graceful_shutdown == GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS


async def test_application_shutdown_timeout_still_runs_uvicorn_shutdown(monkeypatch):
    """应用层关闭超时不能把 uvicorn 的连接清理/lifespan 收尾一起吞掉。"""
    calls: list[str] = []

    class Probe(server_module.CompanionServer):
        async def _shutdown_application(self):
            calls.append("app")
            await asyncio.sleep(30)

    async def fake_uvicorn_shutdown(self, sockets=None):
        calls.append("uvicorn")

    monkeypatch.setattr(server_module, "APP_SHUTDOWN_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(uvicorn.Server, "shutdown", fake_uvicorn_shutdown)

    started = time.monotonic()
    await Probe(_config()).shutdown()
    elapsed = time.monotonic() - started

    assert calls == ["app", "uvicorn"]
    assert elapsed < 2.0, f"停机未在总时限内收尾：{elapsed:.2f}s"


async def test_application_shutdown_error_still_runs_uvicorn_shutdown(monkeypatch):
    """应用层关闭抛错时不能把连接清理一起吞掉（也不能把异常抛给 uvicorn）。"""
    calls: list[str] = []

    class Probe(server_module.CompanionServer):
        async def _shutdown_application(self):
            calls.append("app")
            raise RuntimeError("gateway bug")

    async def fake_uvicorn_shutdown(self, sockets=None):
        calls.append("uvicorn")

    monkeypatch.setattr(uvicorn.Server, "shutdown", fake_uvicorn_shutdown)

    await Probe(_config()).shutdown()

    assert calls == ["app", "uvicorn"]


async def test_force_exit_skips_application_shutdown(monkeypatch):
    """第二次 Ctrl+C（停机开始前）：不再等应用层关闭，直接交给 uvicorn。"""
    calls: list[str] = []

    class Probe(server_module.CompanionServer):
        async def _shutdown_application(self):
            calls.append("app")

    async def fake_uvicorn_shutdown(self, sockets=None):
        calls.append("uvicorn")

    monkeypatch.setattr(uvicorn.Server, "shutdown", fake_uvicorn_shutdown)
    server = Probe(_config())
    server.force_exit = True

    await server.shutdown()

    assert calls == ["uvicorn"]


async def test_force_exit_during_application_shutdown_abandons_wait(monkeypatch):
    """第二次 Ctrl+C 落在应用层关闭窗口内时也必须立刻收尾，而不是等满时限。"""
    calls: list[str] = []

    class Probe(server_module.CompanionServer):
        async def _shutdown_application(self):
            calls.append("app")
            await asyncio.sleep(30)

    async def fake_uvicorn_shutdown(self, sockets=None):
        calls.append("uvicorn")

    monkeypatch.setattr(server_module, "APP_SHUTDOWN_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(uvicorn.Server, "shutdown", fake_uvicorn_shutdown)
    server = Probe(_config())

    task = asyncio.create_task(server.shutdown())
    await asyncio.sleep(0.3)  # 超过一个轮询周期，确保已进入等待
    assert calls == ["app"]

    started = time.monotonic()
    server.force_exit = True
    await asyncio.wait_for(task, timeout=3.0)
    elapsed = time.monotonic() - started

    assert calls == ["app", "uvicorn"]
    assert elapsed < 1.0, f"第二次 Ctrl+C 未及时生效：{elapsed:.2f}s"


async def test_application_shutdown_bounds_app_close(monkeypatch):
    """取消后 `finally` 里的 App 关闭必须带独立预算，否则仍可能无界。"""
    from companion.server.app.routers import ws
    from companion.server.app.services import application_shutdown as module

    recorded: list[float | None] = []

    async def device_shutdown():
        return None

    async def app_shutdown(timeout: float | None = None):
        recorded.append(timeout)

    monkeypatch.setattr(ws.device_gateway, "shutdown", device_shutdown)
    monkeypatch.setattr(ws.app_gateway, "shutdown", app_shutdown)

    await module.shutdown_application()

    assert recorded == [module.APP_CLOSE_TIMEOUT_SECONDS]


async def test_lifespan_shutdown_is_bounded(monkeypatch):
    """第一次应用层停机超时后，lifespan 兜底也不能无界重跑设备关闭。"""
    from companion.server.app import main as main_module
    from companion.server.app.routers import ws

    async def never():
        await asyncio.Event().wait()

    monkeypatch.setattr(ws.device_gateway, "shutdown", never)
    monkeypatch.setattr(main_module, "LIFESPAN_SHUTDOWN_TIMEOUT_SECONDS", 0.1)

    started = time.monotonic()

    async def run_lifespan() -> None:
        async with main_module.app.router.lifespan_context(main_module.app):
            pass

    try:
        # 兜底若被删成无界，这里 3s 失败而不是让整个会话挂住。
        await asyncio.wait_for(run_lifespan(), timeout=3.0)
    finally:
        # lifespan 退出会置位两个网关的停机标志；不复位会污染后续用例。
        ws.device_gateway.open_for_connections()
        ws.app_gateway.open_for_connections()
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"lifespan 兜底未在预算内收尾：{elapsed:.2f}s"


async def test_lifespan_resets_shutdown_flags():
    """同进程重新跑 lifespan 后，网关必须重新接受设备/App 连接。"""
    from companion.server.app.main import app as web_app
    from companion.server.app.routers import ws

    ws.device_gateway._shutting_down = True
    ws.app_gateway._shutting_down = True

    try:
        async with web_app.router.lifespan_context(web_app):
            assert ws.device_gateway._shutting_down is False
            assert ws.app_gateway._shutting_down is False
    finally:
        # lifespan 退出会重新置位；不复位会污染后续直接用模块级网关的测试。
        ws.device_gateway.open_for_connections()
        ws.app_gateway.open_for_connections()
