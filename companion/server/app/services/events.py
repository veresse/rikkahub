"""进程内事件总线：设备网关与 App 网关之间的解耦通道。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict], Awaitable[None]]


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


_event_bus = EventBus()


def get_event_bus() -> EventBus:
    return _event_bus
