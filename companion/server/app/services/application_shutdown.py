"""应用层停机顺序：设备下线 → 等广播送出 → 关闭 App 通道。

`CompanionServer` 在 uvicorn 对连接发 1012 之前调用一次，lifespan 再兜底调用一次。
**两层都必须有界**：uvicorn 的 lifespan shutdown 没有超时，若兜底无界，第一次超时
后剩下的设备会在这里把进程拖到 systemd 的 SIGKILL。本模块提供顺序、单项上限
（`APP_CLOSE_TIMEOUT_SECONDS`）与有界入口（`bounded_shutdown_application()`）。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# `CompanionServer` 等待整个应用层关闭的总时限。
APP_SHUTDOWN_TIMEOUT_SECONDS = 10.0
# lifespan 兜底的总时限：第一次已清空集合时没有实际工作；只有第一次超时/被
# `force_exit` 跳过时才会剩少量设备，用远小于 APP 预算的值再兜一次。
LIFESPAN_SHUTDOWN_TIMEOUT_SECONDS = 2.0
# 关闭 App 通道的独立预算：`shutdown_application()` 的 `finally` 在取消后仍会
# 执行，这里必须有界，否则一个卡住的 App `close()` 就能让“已超时”的停机重新无界。
APP_CLOSE_TIMEOUT_SECONDS = 2.0
# 发出 1012 之后，uvicorn 等待连接任务收尾的上限（`Config.timeout_graceful_shutdown`）。
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 20.0


async def shutdown_application() -> None:
    """设备下线 → 等广播 → 关 App。调用方负责总时限。"""
    # 延迟导入：`routers.ws` 会构建网关单例，放在模块顶部易与 main 形成环。
    from companion.server.app.routers import ws
    from companion.server.app.services.events import drain_background_tasks

    try:
        await ws.device_gateway.shutdown()
        # 设备下线可能异步广播 failed/device_offline 终态，先等它送达 App，
        # 再关闭 App 通道。`drain_background_tasks()` 自身有 1s 上限。
        await drain_background_tasks()
    finally:
        # 被上层取消时这里仍会执行：App 通道必须关闭，但不能再无界等待。
        await ws.app_gateway.shutdown(timeout=APP_CLOSE_TIMEOUT_SECONDS)


async def bounded_shutdown_application(timeout: float, *, source: str) -> bool:
    """至多等 `timeout` 秒；超时取消并返回 False。

    取消后仍会等 `shutdown_application()` 的 `finally` 跑完（上限为
    `APP_CLOSE_TIMEOUT_SECONDS`），所以实际耗时是 `timeout + 少量收尾`。
    """
    task = asyncio.ensure_future(shutdown_application())
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if task in done:
            if not task.cancelled():
                error = task.exception()
                if error is not None:
                    logger.error("%s failed", source, exc_info=error)
            return True
        logger.warning(
            "%s exceeded %.1fs, falling back to uvicorn connection cleanup",
            source,
            timeout,
        )
        return False
    finally:
        # 调用方被取消时也要把应用层任务收干净，避免留下 pending task。
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
