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
    """第二次 Ctrl+C：不再等应用层关闭，直接交给 uvicorn。"""
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


async def test_lifespan_resets_shutdown_flags():
    """同进程重新跑 lifespan 后，网关必须重新接受设备/App 连接。"""
    from companion.server.app.main import app as web_app
    from companion.server.app.routers import ws

    ws.device_gateway._shutting_down = True
    ws.app_gateway._shutting_down = True

    async with web_app.router.lifespan_context(web_app):
        assert ws.device_gateway._shutting_down is False
        assert ws.app_gateway._shutting_down is False
