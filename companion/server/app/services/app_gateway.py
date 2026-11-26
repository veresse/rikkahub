"""App WebSocket 网关（`/app/ws`）：遥控转发与设备事件广播。

职责边界：
- `routers/ws.py`：Starlette 适配 + App Token 鉴权；
- 本模块：解析 `remote.*` 指令、校验参数、复用设备调度器下发、回 `remote.ack`，
  并把事件总线上的设备事件广播给所有在线 App；
- `ActionOrchestrator` 仍然只服务 LLM 工具调用，两者共用同一设备队列。

协议契约见 `companion/docs/protocol_v2.md` 第 8 节。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Protocol

from companion.server.app.services.action_orchestrator import get_device_scheduler
from companion.server.app.services.clock import now_ms
from companion.server.app.services.device_registry import ActionResult, get_device_link
from companion.server.app.services.events import EventBus, get_event_bus
from companion.shared.catalog import CatalogError, get_action, resolve_params

logger = logging.getLogger(__name__)

CLOSE_TOKEN_REVOKED = 4401
OFFSET_LIMIT = 30.0
STOP_REASONS = {"user_stop", "barge_in", "server_cancel"}
SEND_TIMEOUT_SECONDS = 2.0
MAX_REMOTE_PER_SECOND = 10
RATE_WINDOW_SECONDS = 1.0
# 撤销与握手并发时，用短期记忆兜住“鉴权通过后才挂上”的连接。
REVOKED_TTL_SECONDS = 3600.0

BROADCAST_TYPES = {
    "device.status",
    "device.report",
    "action.result",
    "voice.event",
    "conversation.turn_state",
}

REMOTE_TYPES = {
    "remote.action.play",
    "remote.servo.move",
    "remote.volume.set",
    "remote.idle.set",
    "remote.action.stop",
    "remote.calibrate.preview",
    "remote.calibrate.save",
    "remote.get_status",
}


class WSChannel(Protocol):
    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def receive(self) -> dict[str, Any] | bytes | None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


class AppSession:
    """一条 App 连接，带独立滑动窗口限速与发送超时。"""

    def __init__(self, token_id: str, channel: WSChannel, bus: EventBus) -> None:
        self.token_id = token_id
        self.channel = channel
        self.bus = bus
        self.closed = False
        self._send_lock = asyncio.Lock()
        self._events: deque[float] = deque()

    def allow_rate(self) -> bool:
        now = time.monotonic()
        while self._events and now - self._events[0] > RATE_WINDOW_SECONDS:
            self._events.popleft()
        if len(self._events) >= MAX_REMOTE_PER_SECOND:
            return False
        self._events.append(now)
        return True

    async def send_json(self, payload: dict[str, Any]) -> bool:
        if self.closed:
            return False
        try:
            async with self._send_lock:
                await asyncio.wait_for(
                    self.channel.send_json(payload), timeout=SEND_TIMEOUT_SECONDS
                )
            return True
        except Exception:
            logger.debug("app send failed: %s", self.token_id, exc_info=True)
            self.closed = True
            return False

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        try:
            await self.channel.close(code, reason)
        except Exception:
            logger.debug("app close failed: %s", self.token_id, exc_info=True)


def _ack(
    request_id: str, ok: bool, error: str = "", action_id: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "v": 2,
        "type": "remote.ack",
        "request_id": request_id,
        "ok": ok,
    }
    if error:
        payload["error"] = error
    if action_id:
        payload["action_id"] = action_id
    return payload


class AppGateway:
    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or get_event_bus()
        self._sessions: dict[str, set[AppSession]] = {}
        self._revoked: dict[str, float] = {}
        self._subscribed = False

    # ---- 连接生命周期 ----

    async def handle(self, token_id: str, channel: WSChannel) -> None:
        self._prune_revoked()
        session = AppSession(token_id, channel, self.bus)
        self._sessions.setdefault(token_id, set()).add(session)
        self._ensure_subscribed()

        # 撤销可能发生在“鉴权通过”与“挂载完成”之间；这里同步复核，
        # 中间没有 await，撤销回调无法插入。
        if token_id in self._revoked:
            await session.close(CLOSE_TOKEN_REVOKED, "token_revoked")
            self._detach(session)
            return

        try:
            while True:
                try:
                    message = await channel.receive()
                except Exception:
                    logger.debug("app receive failed: %s", token_id, exc_info=True)
                    return
                if message is None:
                    return
                await self._on_message(session, message)
        finally:
            self._detach(session)

    def _ensure_subscribed(self) -> None:
        # 不用布尔标记做幂等判断：`EventBus.clear()` 可能已移除订阅，
        # 而 `subscribe` 自身会去重。
        self.bus.subscribe(self._broadcast)
        self._subscribed = True

    def _detach(self, session: AppSession) -> None:
        sessions = self._sessions.get(session.token_id)
        if sessions is not None:
            sessions.discard(session)
            if not sessions:
                self._sessions.pop(session.token_id, None)
        session.closed = True
        if not self._sessions and self._subscribed:
            self.bus.unsubscribe(self._broadcast)
            self._subscribed = False

    async def revoke(self, token_id: str) -> None:
        """Token 被撤销/过期时立即断开其全部 App 连接。"""
        self._revoked[token_id] = time.monotonic()
        for session in list(self._sessions.get(token_id, ())):
            await session.close(CLOSE_TOKEN_REVOKED, "token_revoked")

    def _prune_revoked(self) -> None:
        cutoff = time.monotonic() - REVOKED_TTL_SECONDS
        for token_id, stamp in list(self._revoked.items()):
            if stamp < cutoff:
                self._revoked.pop(token_id, None)

    async def shutdown(self) -> None:
        for sessions in list(self._sessions.values()):
            for session in list(sessions):
                await session.close(1011, "server_shutdown")
        self._sessions.clear()
        if self._subscribed:
            self.bus.unsubscribe(self._broadcast)
            self._subscribed = False

    # ---- 事件广播 ----

    async def _broadcast(self, event: dict[str, Any]) -> None:
        if event.get("type") not in BROADCAST_TYPES:
            return
        sessions = [s for group in list(self._sessions.values()) for s in list(group)]
        if not sessions:
            return
        results = await asyncio.gather(
            *(session.send_json(event) for session in sessions),
            return_exceptions=True,
        )
        for session, result in zip(sessions, results):
            if result is not True:
                self._detach(session)

    # ---- 上行指令 ----

    async def _on_message(self, session: AppSession, message: Any) -> None:
        try:
            await self._handle_message(session, message)
        except Exception:
            logger.exception("app message handling failed: %s", session.token_id)
            request_id = message.get("request_id") if isinstance(message, dict) else None
            if isinstance(request_id, str) and request_id:
                await session.send_json(_ack(request_id, False, "internal_error"))

    async def _handle_message(self, session: AppSession, message: Any) -> None:
        if isinstance(message, (bytes, bytearray)):
            return
        if not isinstance(message, dict):
            return

        message_type = message.get("type")
        if not isinstance(message_type, str) or not message_type.startswith("remote."):
            return

        request_id = message.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            await session.send_json(
                {
                    "v": 2,
                    "type": "error",
                    "code": "invalid_request",
                    "message": "request_id required",
                }
            )
            return

        if message_type not in REMOTE_TYPES:
            await session.send_json(_ack(request_id, False, "unknown_command"))
            return

        # 所有 remote.*（含急停/校准/状态查询）都受每连接限速约束。
        if not session.allow_rate():
            await session.send_json(_ack(request_id, False, "rate_limited"))
            return

        device_id = message.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            await session.send_json(
                {
                    "v": 2,
                    "type": "error",
                    "code": "invalid_request",
                    "message": "device_id required",
                    "request_id": request_id,
                }
            )
            return

        link = get_device_link(device_id)
        if link is None:
            await session.send_json(_ack(request_id, False, "device_offline"))
            return

        if int(getattr(link, "protocol_version", 2)) < 2:
            await session.send_json(_ack(request_id, False, "unsupported_protocol"))
            return

        ok, error, action_id = await self._dispatch(
            session, device_id, link, message_type, message
        )
        await session.send_json(_ack(request_id, ok, error, action_id))

    async def _dispatch(
        self,
        session: AppSession,
        device_id: str,
        link: Any,
        message_type: str,
        message: dict[str, Any],
    ) -> tuple[bool, str, str | None]:
        scheduler = get_device_scheduler(device_id, link)

        if message_type == "remote.action.play":
            return await self._play(scheduler, message)
        if message_type == "remote.servo.move":
            return await self._move_head(scheduler, message)
        if message_type == "remote.volume.set":
            return await self._set_volume(scheduler, message)
        if message_type == "remote.idle.set":
            return await self._set_idle(scheduler, message)
        if message_type == "remote.action.stop":
            return await self._stop(scheduler, message)
        if message_type == "remote.calibrate.preview":
            return await self._calibrate_preview(link, message)
        if message_type == "remote.calibrate.save":
            return await self._calibrate_save(scheduler, message)

        return await self._get_status(session, device_id, link)

    async def _get_status(
        self, session: AppSession, device_id: str, link: Any
    ) -> tuple[bool, str, None]:
        try:
            status = await link.get_status()
        except Exception:
            return False, "internal_error", None
        if not isinstance(status, dict):
            return False, "internal_error", None
        # 优先使用协议的对外形状（idle/ts），避免把内部字段 idle_enabled 泄漏给 App。
        public = getattr(link, "public_report", None)
        fields = public() if callable(public) else status
        report = {
            "v": 2,
            "type": "device.report",
            "device_id": device_id,
            "source": "server",
            "ts": now_ms(),
        }
        report.update({k: v for k, v in fields.items() if k != "device_id"})
        await session.send_json(report)
        return True, "", None

    async def _play(self, scheduler: Any, message: dict[str, Any]) -> tuple[bool, str, str | None]:
        name = message.get("name")
        if not isinstance(name, str) or not name:
            return False, "invalid_arguments", None
        try:
            action = get_action(name)
            params = resolve_params(action, message)
        except KeyError:
            return False, "unknown_action", None
        except (CatalogError, TypeError, ValueError):
            return False, "invalid_arguments", None

        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "action.play",
            "action_id": action_id,
            "name": action["name"],
            "intensity": params["intensity"],
            "speed": params["speed"],
            "repeat": params["repeat"],
            "source": "app",
            "ts": now_ms(),
        }
        ok, error, _ = self._from_result(await scheduler.enqueue(command))
        return ok, error, action_id if ok else None

    async def _move_head(
        self, scheduler: Any, message: dict[str, Any]
    ) -> tuple[bool, str, str | None]:
        pan = message.get("pan")
        tilt = message.get("tilt")
        speed = message.get("speed", 500)
        if not _is_number(pan) or not _is_number(tilt):
            return False, "invalid_arguments", None
        if not (-90.0 <= float(pan) <= 90.0) or not (-20.0 <= float(tilt) <= 45.0):
            return False, "angle_out_of_range", None
        if isinstance(speed, bool) or not isinstance(speed, int) or not (0 <= speed <= 1000):
            return False, "invalid_arguments", None

        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "servo.move",
            "action_id": action_id,
            "pan": float(pan),
            "tilt": float(tilt),
            "speed": speed,
            "source": "app",
            "ts": now_ms(),
        }
        ok, error, _ = self._from_result(
            await scheduler.enqueue(command, override=True)
        )
        return ok, error, action_id if ok else None

    async def _set_volume(
        self, scheduler: Any, message: dict[str, Any]
    ) -> tuple[bool, str, str | None]:
        volume = message.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or not (0 <= volume <= 100):
            return False, "invalid_arguments", None

        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "volume.set",
            "action_id": action_id,
            "volume": volume,
            "source": "app",
            "ts": now_ms(),
        }
        ok, error, _ = self._from_result(
            await scheduler.enqueue(command, override=True)
        )
        return ok, error, action_id if ok else None

    async def _set_idle(
        self, scheduler: Any, message: dict[str, Any]
    ) -> tuple[bool, str, str | None]:
        enabled = message.get("enabled")
        if not isinstance(enabled, bool):
            return False, "invalid_arguments", None

        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "idle.set",
            "action_id": action_id,
            "enabled": enabled,
            "source": "app",
            "ts": now_ms(),
        }
        ok, error, _ = self._from_result(await scheduler.enqueue(command))
        return ok, error, action_id if ok else None

    async def _stop(self, scheduler: Any, message: dict[str, Any]) -> tuple[bool, str, None]:
        reason = message.get("reason", "user_stop")
        if reason not in STOP_REASONS:
            return False, "invalid_arguments", None
        ok, error, _ = self._from_result(await scheduler.stop(reason))
        return ok, error, None

    async def _calibrate_preview(self, link: Any, message: dict[str, Any]) -> tuple[bool, str, None]:
        offsets = self._offsets(message)
        if offsets is None:
            return False, "invalid_arguments", None
        pan_offset, tilt_offset = offsets
        try:
            result = await link.send(
                {
                    "v": 2,
                    "type": "servo.calibrate_preview",
                    "pan_offset": pan_offset,
                    "tilt_offset": tilt_offset,
                    "source": "app",
                    "ts": now_ms(),
                }
            )
        except Exception:
            return False, "internal_error", None
        ok, error, _ = self._from_result(result)
        return ok, error, None

    async def _calibrate_save(
        self, scheduler: Any, message: dict[str, Any]
    ) -> tuple[bool, str, str | None]:
        offsets = self._offsets(message)
        if offsets is None:
            return False, "invalid_arguments", None
        pan_offset, tilt_offset = offsets
        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "servo.calibrate_save",
            "action_id": action_id,
            "pan_offset": pan_offset,
            "tilt_offset": tilt_offset,
            "source": "app",
            "ts": now_ms(),
        }
        ok, error, _ = self._from_result(await scheduler.enqueue(command))
        return ok, error, action_id if ok else None

    @staticmethod
    def _offsets(message: dict[str, Any]) -> tuple[float, float] | None:
        pan = message.get("pan_offset")
        tilt = message.get("tilt_offset")
        if not _is_number(pan) or not _is_number(tilt):
            return None
        if not (-OFFSET_LIMIT <= float(pan) <= OFFSET_LIMIT):
            return None
        if not (-OFFSET_LIMIT <= float(tilt) <= OFFSET_LIMIT):
            return None
        return float(pan), float(tilt)

    @staticmethod
    def _from_result(result: ActionResult) -> tuple[bool, str, None]:
        if result.status in ("queued", "done"):
            return True, "", None
        return False, result.detail or result.status, None
