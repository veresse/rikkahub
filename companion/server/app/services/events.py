"""进程内事件总线：设备网关与 App 网关之间的解耦通道。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict], Awaitable[None]]

# 后台广播任务的强引用：避免 create_task 的结果被 GC 提前回收。
_background_tasks: set[asyncio.Task] = set()


class EventBus:
    """极简单播/广播总线；单个订阅者异常不会影响其他订阅者。"""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    async def publish(self, event: dict) -> None:
        for handler in list(self._handlers):
            try:
                await handler(dict(event))
            except Exception:
                logger.exception("event handler failed for %s", event.get("type"))

    def clear(self) -> None:
        """清空订阅者（测试隔离 / 进程关闭用）。"""
        self._handlers.clear()


_event_bus = EventBus()


def get_event_bus() -> EventBus:
    return _event_bus


def publish_async(bus: EventBus, event: dict) -> asyncio.Task | None:
    """在后台广播事件，避免慢订阅者阻塞设备读循环。

    返回的任务由模块级集合持有强引用；调用方无需等待，也无需关心异常
    （`EventBus.publish` 内部已逐个吞掉订阅者异常）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(bus.publish(event))
    _background_tasks.add(task)

    def _done(finished: asyncio.Task) -> None:
        _background_tasks.discard(finished)
        if finished.cancelled():
            return
        error = finished.exception()
        if error is not None:
            logger.error("background event publish failed", exc_info=error)

    task.add_done_callback(_done)
    return task
