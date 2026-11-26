"""uvicorn 停机顺序适配与启动入口。

uvicorn 的 `Server.shutdown()` 会先对所有连接发 1012 并等待连接清空，**最后**才
触发 lifespan shutdown（`uvicorn/server.py:215-245`）。如果只在 lifespan 里关闭
设备网关，此时 App 的 `/app/ws` 已经被 1012 断开，`device.status online=false`
事件没有接收者，`server_shutdown` 原因也就永远到不了 App。

`CompanionServer` 把应用层的关闭顺序（设备下线 → 等待广播 → 关闭 App）提前到
uvicorn 关闭连接之前；lifespan 里的同名调用保留为有界兜底（正常路径下集合为空，
幂等）。

**该保证只在通过本模块的 `run()`（即 `python -m companion.server.app.main`）启动时
成立。** 直接 `uvicorn companion.server.app.main:app` 会绕过这里的覆盖。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

import uvicorn
from uvicorn.supervisors import ChangeReload

from companion.server.app.services.application_shutdown import (
    APP_SHUTDOWN_TIMEOUT_SECONDS,
    shutdown_application,
)

logger = logging.getLogger(__name__)

# 第二次 Ctrl+C 的生效粒度：等待应用层关闭时按这个间隔检查 `force_exit`。
FORCE_EXIT_POLL_SECONDS = 0.1


class CompanionServer(uvicorn.Server):
    async def _shutdown_application(self) -> None:
        await shutdown_application()

    async def _wait_application_shutdown(self) -> None:
        """至多等 `APP_SHUTDOWN_TIMEOUT_SECONDS`；期间响应第二次 Ctrl+C。

        用“任务 + 轮询”而不是 `wait_for`：取消后还要等 `finally` 关 App 通道，
        `wait_for` 会把这段收尾也算进总时长；这里显式区分“超时/强制退出”与
        “应用层抛错”，避免把内部 `TimeoutError` 误报成超时。
        """
        task = asyncio.ensure_future(self._shutdown_application())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + APP_SHUTDOWN_TIMEOUT_SECONDS
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    logger.warning(
                        "application shutdown exceeded %.1fs, "
                        "falling back to uvicorn connection cleanup",
                        APP_SHUTDOWN_TIMEOUT_SECONDS,
                    )
                    break
                done, _ = await asyncio.wait(
                    {task}, timeout=min(remaining, FORCE_EXIT_POLL_SECONDS)
                )
                if task in done:
                    break
                if self.force_exit:
                    logger.warning("force exit: abandoning application shutdown")
                    break
        finally:
            if not task.done():
                task.cancel()
            results = await asyncio.gather(task, return_exceptions=True)
            error = results[0] if results else None
            if isinstance(error, BaseException) and not isinstance(
                error, asyncio.CancelledError
            ):
                logger.error("application shutdown failed", exc_info=error)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        try:
            if self.force_exit:
                # 第二次 Ctrl+C：放弃应用层关闭，让 uvicorn 立刻清理连接。
                logger.warning("force exit: skipping application shutdown")
            else:
                await self._wait_application_shutdown()
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
    if config.reload and not isinstance(config.app, str):
        # 与 uvicorn.run() 一致：reload 需要子进程重新导入应用，非字符串会静默失效。
        raise SystemExit("reload requires the application to be an import string")
    server = CompanionServer(config)
    try:
        if config.should_reload:
            sock = config.bind_socket()
            ChangeReload(config, target=server.run, sockets=[sock]).run()
        else:
            server.run()
    except KeyboardInterrupt:
        pass
    finally:
        if config.uds and os.path.exists(config.uds):
            os.remove(config.uds)
