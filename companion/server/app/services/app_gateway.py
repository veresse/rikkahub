"""App WebSocket 网关（`/app/ws`）：遥控转发与设备事件广播。

职责边界：
- `routers/ws.py`：Starlette 适配 + App Token 鉴权；
- 本模块：解析 `remote.*` 指令、校验参数、复用设备调度器下发、回 `remote.ack`，
  并把事件总线上的设备事件广播给所有在线 App；
- `ActionOrchestrator` 仍然只服务 LLM 工具调用，两者共用同一设备队列。

协议契约见 `companion/docs/protocol_v2.md` 第 8 节。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

from companion.server.app.config import settings
from companion.server.app.services.action_orchestrator import (
    DEFAULT_COMMAND_TIMEOUT_MS,
    get_device_scheduler,
)
from companion.server.app.services.clock import now_ms
from companion.server.app.services.device_registry import ActionResult, get_device_link
from companion.server.app.services.events import EventBus, get_event_bus
from companion.shared.catalog import CatalogError, get_action, resolve_params

logger = logging.getLogger(__name__)

CLOSE_TOKEN_REVOKED = 4401
OFFSET_LIMIT = 30.0
STOP_REASONS = {"user_stop", "barge_in", "server_cancel"}

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
    """占位协议，具体实现由 `routers/ws.py` 的 `StarletteChannel` 提供。"""

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def receive(self) -> dict[str, Any] | bytes | None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


class AppSession:
    """一条 App 连接。"""

    def __init__(self, token_id: str, channel: WSChannel, bus: EventBus) -> None:
        self.token_id = token_id
        self.channel = channel
        self.bus = bus
        self.closed = False

    async def send_json(self, payload: dict[str, Any]) -> bool:
        if self.closed:
            return False
        try:
            await self.channel.send_json(payload)
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


def _ack(request_id: str, ok: bool, error: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "v": 2,
        "type": "remote.ack",
        "request_id": request_id,
        "ok": ok,
    }
    if error:
        payload["error"] = error
    return payload


class AppGateway:
    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or get_event_bus()
        self._sessions: dict[str, set[AppSession]] = {}
        self._subscribed = False

    # ---- 连接生命周期 ----

    async def handle(self, token_id: str, channel: WSChannel) -> None:
        session = AppSession(token_id, channel, self.bus)
        self._sessions.setdefault(token_id, set()).add(session)
        self._ensure_subscribed()
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
        if not self._subscribed:
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
        for session in list(self._sessions.get(token_id, ())):
            await session.close(CLOSE_TOKEN_REVOKED, "token_revoked")

    async def shutdown(self) -> None:
        for sessions in list(self._sessions.values()):
            for session in list(sessions):
                await session.close(1011, "server_shutdown")

    # ---- 事件广播 ----

    async def _broadcast(self, event: dict[str, Any]) -> None:
        dead: list[AppSession] = []
        for sessions in list(self._sessions.values()):
            for session in list(sessions):
                if not await session.send_json(event):
                    dead.append(session)
        for session in dead:
            self._detach(session)

    # ---- 上行指令 ----

    async def _on_message(self, session: AppSession, message: Any) -> None:
        if isinstance(message, (bytes, bytearray)):
            return
        if not isinstance(message, dict):
            return

        message_type = message.get("type")
        if not isinstance(message_type, str) or not message_type.startswith("remote."):
            return

        request_id = message.get("request_id")
        device_id = message.get("device_id")
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

        ok, error = await self._dispatch(device_id, link, message_type, message)
        await session.send_json(_ack(request_id, ok, error))

    async def _dispatch(
        self, device_id: str, link: Any, message_type: str, message: dict[str, Any]
    ) -> tuple[bool, str]:
        if message_type not in REMOTE_TYPES:
            return False, "unknown_command"

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
        if message_type == "remote.get_status":
            return await self._get_status(device_id, link)

        return False, "unknown_command"

    async def _get_status(self, device_id: str, link: Any) -> tuple[bool, str]:
        try:
            status = await link.get_status()
        except Exception:
            return False, "internal_error"
        report = {
            "v": 2,
            "type": "device.report",
            "device_id": device_id,
            "ts": now_ms(),
        }
        report.update({k: v for k, v in status.items() if k != "device_id"})
        await self.bus.publish(report)
        return True, ""

    async def _play(self, scheduler: Any, message: dict[str, Any]) -> tuple[bool, str]:
        name = message.get("name")
        if not isinstance(name, str) or not name:
            return False, "invalid_arguments"
        try:
            action = get_action(name)
            params = resolve_params(action, message)
        except KeyError:
            return False, "unknown_action"
        except (CatalogError, TypeError, ValueError):
            return False, "invalid_arguments"

        command = {
            "v": 2,
            "type": "action.play",
            "action_id": str(uuid.uuid4()),
            "name": action["name"],
            "intensity": params["intensity"],
            "speed": params["speed"],
            "repeat": params["repeat"],
            "source": "app",
            "ts": now_ms(),
        }
        timeout_ms = int(action["max_duration_ms"]) + settings.ACTION_RESULT_TIMEOUT_EXTRA_MS
        return self._from_result(await scheduler.enqueue(command, timeout_ms=timeout_ms))

    async def _move_head(self, scheduler: Any, message: dict[str, Any]) -> tuple[bool, str]:
        pan = message.get("pan")
        tilt = message.get("tilt")
        speed = message.get("speed", 500)
        if not _is_number(pan) or not _is_number(tilt):
            return False, "invalid_arguments"
        if not (-90.0 <= float(pan) <= 90.0) or not (-20.0 <= float(tilt) <= 45.0):
            return False, "angle_out_of_range"
        if isinstance(speed, bool) or not isinstance(speed, int) or not (0 <= speed <= 1000):
            return False, "invalid_arguments"

        command = {
            "v": 2,
            "type": "servo.move",
            "action_id": str(uuid.uuid4()),
            "pan": float(pan),
            "tilt": float(tilt),
            "speed": speed,
            "source": "app",
            "ts": now_ms(),
        }
        timeout_ms = max(DEFAULT_COMMAND_TIMEOUT_MS, speed + 1000)
        return self._from_result(
            await scheduler.enqueue(command, timeout_ms=timeout_ms, override=True)
        )

    async def _set_volume(self, scheduler: Any, message: dict[str, Any]) -> tuple[bool, str]:
        volume = message.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or not (0 <= volume <= 100):
            return False, "invalid_arguments"

        command = {
            "v": 2,
            "type": "volume.set",
            "action_id": str(uuid.uuid4()),
            "volume": volume,
            "source": "app",
            "ts": now_ms(),
        }
        return self._from_result(
            await scheduler.enqueue(
                command, timeout_ms=DEFAULT_COMMAND_TIMEOUT_MS, override=True
            )
        )

    async def _set_idle(self, scheduler: Any, message: dict[str, Any]) -> tuple[bool, str]:
        enabled = message.get("enabled")
        if not isinstance(enabled, bool):
            return False, "invalid_arguments"

        command = {
            "v": 2,
            "type": "idle.set",
            "action_id": str(uuid.uuid4()),
            "enabled": enabled,
            "source": "app",
            "ts": now_ms(),
        }
        return self._from_result(
            await scheduler.enqueue(command, timeout_ms=DEFAULT_COMMAND_TIMEOUT_MS)
        )

    async def _stop(self, scheduler: Any, message: dict[str, Any]) -> tuple[bool, str]:
        reason = message.get("reason", "user_stop")
        if reason not in STOP_REASONS:
            return False, "invalid_arguments"
        return self._from_result(await scheduler.stop(reason))

    async def _calibrate_preview(self, link: Any, message: dict[str, Any]) -> tuple[bool, str]:
        offsets = self._offsets(message)
        if offsets is None:
            return False, "invalid_arguments"
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
            return False, "internal_error"
        return self._from_result(result)

    async def _calibrate_save(self, scheduler: Any, message: dict[str, Any]) -> tuple[bool, str]:
        offsets = self._offsets(message)
        if offsets is None:
            return False, "invalid_arguments"
        pan_offset, tilt_offset = offsets
        command = {
            "v": 2,
            "type": "servo.calibrate_save",
            "action_id": str(uuid.uuid4()),
            "pan_offset": pan_offset,
            "tilt_offset": tilt_offset,
            "source": "app",
            "ts": now_ms(),
        }
        return self._from_result(
            await scheduler.enqueue(command, timeout_ms=DEFAULT_COMMAND_TIMEOUT_MS)
        )

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
    def _from_result(result: ActionResult) -> tuple[bool, str]:
        if result.status in ("queued", "done"):
            return True, ""
        return False, result.detail or result.status
