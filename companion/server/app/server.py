"""uvicorn 停机顺序适配。

uvicorn 的 `Server.shutdown()` 会先对所有连接发 1012 并等待连接清空，**最后**才
触发 lifespan shutdown（`uvicorn/server.py:215-245`）。如果只在 lifespan 里关闭
设备网关，此时 App 的 `/app/ws` 已经被 1012 断开，`device.status online=false`
事件没有接收者，`server_shutdown` 原因也就永远到不了 App。

`CompanionServer` 把应用层的关闭顺序（设备下线 → 等待广播 → 关闭 App）提前到
uvicorn 关闭连接之前；lifespan 里的同名调用保留为兜底（此时集合为空，幂等）。
"""

from __future__ import annotations

import socket

import uvicorn


class CompanionServer(uvicorn.Server):
    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        # 延迟导入：`routers.ws` 会构建网关单例，放在模块顶部易与 main 形成环。
        from companion.server.app.routers import ws
        from companion.server.app.services.events import drain_background_tasks

        try:
            try:
                await ws.device_gateway.shutdown()
                # 设备下线可能异步广播 failed/device_offline 终态，先等它送达 App，
                # 再关闭 App 通道。
                await drain_background_tasks()
            finally:
                await ws.app_gateway.shutdown()
        finally:
            # 即使应用层关闭抛错，也要让 uvicorn 完成连接清理与 lifespan 收尾。
            await super().shutdown(sockets)
