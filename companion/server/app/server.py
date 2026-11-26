"""uvicorn 停机顺序适配与启动入口。

uvicorn 的 `Server.shutdown()` 会先对所有连接发 1012 并等待连接清空，**最后**才
触发 lifespan shutdown（`uvicorn/server.py:215-245`）。如果只在 lifespan 里关闭
设备网关，此时 App 的 `/app/ws` 已经被 1012 断开，`device.status online=false`
事件没有接收者，`server_shutdown` 原因也就永远到不了 App。

`CompanionServer` 把应用层的关闭顺序（设备下线 → 等待广播 → 关闭 App）提前到
uvicorn 关闭连接之前；lifespan 里的同名调用保留为兜底（此时集合为空，幂等）。

**该保证只在通过本模块的 `run()`（即 `python -m companion.server.app.main`）启动时
成立。** 直接 `uvicorn companion.server.app.main:app` 会绕过这里的覆盖。
"""

from __future__ import annotations

import asyncio
import logging
import socket

import uvicorn
from uvicorn.supervisors import ChangeReload

logger = logging.getLogger(__name__)

# 应用层停机的总时限。设备上报冲刷要写 SQLite（`busy_timeout=15000`），慢盘或
# 设备较多时单台就可能到秒级；超过这个总时限就不再等，交给 uvicorn 的 1012 收尾
# （进程即将退出，继续等只会让 systemd / docker 的停止宽限先到并 SIGKILL）。
APP_SHUTDOWN_TIMEOUT_SECONDS = 10.0


class CompanionServer(uvicorn.Server):
    async def _shutdown_application(self) -> None:
        # 延迟导入：`routers.ws` 会构建网关单例，放在模块顶部易与 main 形成环。
        from companion.server.app.routers import ws
        from companion.server.app.services.events import drain_background_tasks

        try:
            await ws.device_gateway.shutdown()
            # 设备下线可能异步广播 failed/device_offline 终态，先等它送达 App，
            # 再关闭 App 通道。
            await drain_background_tasks()
        finally:
            await ws.app_gateway.shutdown()

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        try:
            if self.force_exit:
                # 第二次 Ctrl+C：放弃应用层关闭，让 uvicorn 立刻清理连接。
                logger.warning("force exit: skipping application shutdown")
            else:
                try:
                    await asyncio.wait_for(
                        self._shutdown_application(), APP_SHUTDOWN_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "application shutdown exceeded %.1fs, "
                        "falling back to uvicorn connection cleanup",
                        APP_SHUTDOWN_TIMEOUT_SECONDS,
                    )
                except Exception:
                    logger.exception("application shutdown failed")
        finally:
            # 即使应用层关闭抛错/超时，也要让 uvicorn 完成连接清理与 lifespan 收尾。
            await super().shutdown(sockets)


def run(config: uvicorn.Config) -> None:
    """与 `uvicorn.run()` 对齐的启动入口，但使用 `CompanionServer`。

    `uvicorn.run()` 内部固定构造 `uvicorn.Server`，无法注入子类；这里照抄它的
    reload 分支，保证 `DEBUG=true`（`reload=True`）仍然生效。

    多 worker 不受支持：`Multiprocess` 的每个 worker 都是普通 `uvicorn.Server`，
    不会执行这里的停机顺序（App 会先收到 1012）。项目按单进程部署，这里显式
    拒绝而不是静默降级。
    """
    if config.workers > 1:
        raise SystemExit(
            "workers > 1 is not supported: graceful device shutdown requires "
            "CompanionServer in a single process"
        )
    server = CompanionServer(config)
    try:
        if config.should_reload:
            sock = config.bind_socket()
            ChangeReload(config, target=server.run, sockets=[sock]).run()
        else:
            server.run()
    except KeyboardInterrupt:
        pass
