"""设备 WebSocket 网关的核心逻辑（与 Starlette 解耦）。

`routers/ws.py` 只负责把 Starlette WebSocket 包装成 `WSChannel` 并完成 Token
鉴权；握手、协议解析、指令关联、状态落库与上线/离线广播都在本模块，便于
用 FakeChannel 做离线单元测试。

协议契约见 `companion/docs/protocol_v2.md`。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol

from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.device import DeviceModel
from companion.server.app.services.action_orchestrator import invalidate_device_scheduler
from companion.server.app.services.clock import now_ms
from companion.server.app.services.device_registry import (
    ActionResult,
    get_device_link,
    register_device_link,
    unregister_device_link,
)
from companion.server.app.services.events import EventBus, get_event_bus
from companion.shared.catalog import get_catalog

logger = logging.getLogger(__name__)

SUPPORTED_CAPABILITIES = {"action", "volume", "expression"}
VALID_STATES = {"idle", "listening", "thinking", "speaking", "error"}
OFFSET_LIMIT = 30.0

DEFAULT_AUDIO = {
    "uplink": {"codec": "opus", "sample_rate": 16000, "channels": 1, "frame_ms": 60},
    "downlink": {"codec": "opus", "sample_rate": 24000, "channels": 1, "frame_ms": 60},
}


class WSChannel(Protocol):
    """WebSocket 的最小抽象，测试用 FakeChannel 实现。"""

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def receive(self) -> dict[str, Any] | bytes | None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class DeviceSession:
    """一台在线设备的会话与 `DeviceLink` 实现。"""

    def __init__(
        self,
        device: DeviceModel,
        session_id: str,
        channel: WSChannel,
        bus: EventBus,
        *,
        protocol_version: int,
        capabilities: list[str],
    ) -> None:
        self.device_id = device.id
        self.session_id = session_id
        self.channel = channel
        self.bus = bus
        self.protocol_version = protocol_version
        self.capabilities = capabilities
        self.closed = False

        self._report = self._initial_report(device)
        self._pending: dict[str, asyncio.Future[ActionResult]] = {}
        self._sources: dict[str, str] = {}
        self._send_lock = asyncio.Lock()

    @staticmethod
    def _initial_report(device: DeviceModel) -> dict[str, Any]:
        report: dict[str, Any] = {
            "volume": _clamp(int(device.volume or 0), 0, 100),
            "pan": 0.0,
            "tilt": 0.0,
            "idle_enabled": bool(device.idle_enabled),
            "state": device.state if device.state in VALID_STATES else "idle",
            "pan_offset": _clamp(float(device.pan_offset or 0.0), -OFFSET_LIMIT, OFFSET_LIMIT),
            "tilt_offset": _clamp(float(device.tilt_offset or 0.0), -OFFSET_LIMIT, OFFSET_LIMIT),
            "power": "usb",
        }
        snapshot = device.last_report_json
        if isinstance(snapshot, dict):
            for key in ("pan", "tilt", "power"):
                if key in snapshot:
                    report[key] = snapshot[key]
        return report

    # ---- DeviceLink 接口 ----

    async def send(self, command: dict[str, Any]) -> ActionResult:
        if self.closed:
            return ActionResult(status="failed", detail="device_offline")

        payload = dict(command)
        payload.setdefault("v", 2)
        payload.setdefault("ts", now_ms())
        payload["session_id"] = self.session_id

        action_type = payload.get("type")

        # action.stop 是最高优先级且不带 action_id：不等设备回执，同时把
        # 在途动作的等待者标记为 stopped。
        if action_type == "action.stop":
            sent = await self._safe_send(payload)
            self._resolve_all(ActionResult(status="failed", detail="stopped"))
            if not sent:
                return ActionResult(status="failed", detail="device_offline")
            return ActionResult(status="done")

        action_id = payload.get("action_id")
        if not action_id:
            sent = await self._safe_send(payload)
            return (
                ActionResult(status="done")
                if sent
                else ActionResult(status="failed", detail="device_offline")
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ActionResult] = loop.create_future()
        self._pending[action_id] = future
        self._sources[action_id] = str(payload.get("source") or "")
        try:
            if not await self._safe_send(payload):
                return ActionResult(status="failed", detail="device_offline")
            return await asyncio.shield(future)
        finally:
            self._pending.pop(action_id, None)
            self._sources.pop(action_id, None)

    async def get_status(self) -> dict[str, Any]:
        return {
            **self._report,
            "device_id": self.device_id,
            "online": not self.closed,
        }

    # ---- 网关内部接口 ----

    async def _safe_send(self, payload: dict[str, Any]) -> bool:
        try:
            async with self._send_lock:
                await self.channel.send_json(payload)
            return True
        except Exception:
            logger.debug("device send failed: %s", self.device_id, exc_info=True)
            self.closed = True
            return False

    def resolve_result(self, action_id: str, status: str, detail: str) -> str:
        """把 `action.result` 交给等待中的工具调用，返回指令来源。"""
        source = self._sources.get(action_id, "")
        future = self._pending.get(action_id)
        if future is not None and not future.done():
            future.set_result(
                ActionResult(status=status, detail=detail, action_id=action_id)
            )
        return source

    def _resolve_all(self, result: ActionResult) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(result)

    def apply_report(self, report: dict[str, Any]) -> dict[str, Any]:
        volume = report.get("volume")
        if _is_number(volume):
            self._report["volume"] = int(_clamp(int(volume), 0, 100))

        idle = report.get("idle")
        if isinstance(idle, bool):
            self._report["idle_enabled"] = idle

        state = report.get("state")
        if isinstance(state, str) and state in VALID_STATES:
            self._report["state"] = state

        for axis in ("pan", "tilt"):
            value = report.get(axis)
            if _is_number(value):
                self._report[axis] = float(value)

        for key, limit in (("pan_offset", OFFSET_LIMIT), ("tilt_offset", OFFSET_LIMIT)):
            value = report.get(key)
            if _is_number(value):
                self._report[key] = _clamp(float(value), -limit, limit)

        power = report.get("power")
        if isinstance(power, str):
            self._report["power"] = power
        return dict(self._report)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self._resolve_all(ActionResult(status="failed", detail="device_offline"))
        try:
            await self.channel.close(code, reason)
        except Exception:
            logger.debug("device close failed: %s", self.device_id, exc_info=True)


class DeviceGateway:
    def __init__(
        self,
        bus: EventBus | None = None,
        session_factory=AsyncSessionLocal,
    ) -> None:
        self.bus = bus or get_event_bus()
        self._session_factory = session_factory
        self._sessions: set[DeviceSession] = set()

    async def handle(self, device_id: str, channel: WSChannel) -> None:
        session: DeviceSession | None = None
        device_loaded = False
        try:
            device = await self._load_device(device_id)
            if device is None:
                await self._close(channel, 4403, "unknown_device")
                return
            device_loaded = True

            hello = await self._receive_hello(channel)
            if hello is None:
                return

            if hello.get("device_id") != device_id:
                await self._close(channel, 4403, "device_mismatch")
                return

            protocol_version, capabilities = self._negotiate(hello)
            if protocol_version is None:
                await self._close(channel, 4403, "unsupported_protocol")
                return

            session = DeviceSession(
                device,
                str(uuid.uuid4()),
                channel,
                self.bus,
                protocol_version=protocol_version,
                capabilities=capabilities,
            )
            await self._persist_hello(device_id, hello, protocol_version, capabilities)
            await self._send_hello_ack(session, hello)
            await self._install(session)
            await self._read_loop(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("device gateway error: %s", device_id)
            if session is not None:
                await session.close(1011, "internal_error")
            else:
                await self._close(channel, 1011, "internal_error")
        finally:
            if session is not None:
                await self._teardown(session)
            elif device_loaded:
                await self._publish_status(device_id, online=False, reason="handshake_failed")

    # ---- 握手 ----

    async def _receive_hello(self, channel: WSChannel) -> dict[str, Any] | None:
        try:
            message = await asyncio.wait_for(
                channel.receive(), timeout=settings.DEVICE_HELLO_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            await self._close(channel, 4408, "hello_timeout")
            return None
        if not isinstance(message, dict) or message.get("type") != "hello":
            await self._close(channel, 4408, "invalid_hello")
            return None
        return message

    @staticmethod
    def _negotiate(hello: dict[str, Any]) -> tuple[int | None, list[str]]:
        try:
            version = int(hello.get("v", 1))
        except (TypeError, ValueError):
            version = 1
        capabilities = hello.get("capabilities")
        if version == 1 or not isinstance(capabilities, list):
            return 1, []
        if version == 2:
            return 2, [c for c in capabilities if c in SUPPORTED_CAPABILITIES]
        return None, []

    async def _send_hello_ack(
        self, session: DeviceSession, hello: dict[str, Any]
    ) -> None:
        catalog = get_catalog()
        limits = catalog.get("limits", {})
        pan = limits.get("pan", {})
        tilt = limits.get("tilt", {})
        warnings: list[str] = []
        if (
            session.protocol_version >= 2
            and hello.get("action_catalog_version") != catalog["version"]
        ):
            warnings.append("action_catalog_mismatch")

        audio = hello.get("audio")
        if not isinstance(audio, dict):
            audio = DEFAULT_AUDIO

        await session.channel.send_json(
            {
                "v": 2,
                "type": "hello_ack",
                "session_id": session.session_id,
                "server_time": now_ms(),
                "protocol_version": session.protocol_version,
                "capabilities": session.capabilities,
                "action_catalog_version": catalog["version"],
                "audio": audio,
                "state": {
                    "volume": session._report["volume"],
                    "idle_enabled": session._report["idle_enabled"],
                    "pan_offset": session._report["pan_offset"],
                    "tilt_offset": session._report["tilt_offset"],
                },
                "limits": {
                    "pan": [pan.get("min", -90.0), pan.get("max", 90.0)],
                    "tilt": [tilt.get("min", -20.0), tilt.get("max", 45.0)],
                    "volume": [0, 100],
                    "default_max_duration_ms": limits.get("default_max_duration_ms", 3000),
                    "hard_max_duration_ms": limits.get("hard_max_duration_ms", 5000),
                    "rate_limit_per_second": 10,
                },
                "warnings": warnings,
            }
        )

    # ---- 连接生命周期 ----

    async def _install(self, session: DeviceSession) -> None:
        old = get_device_link(session.device_id)
        register_device_link(session.device_id, session)
        self._sessions.add(session)
        if isinstance(old, DeviceSession) and old is not session:
            await old.close(4409, "replaced")
        await self._publish_status(session.device_id, online=True)

    async def _teardown(self, session: DeviceSession) -> None:
        self._sessions.discard(session)
        if get_device_link(session.device_id) is session:
            unregister_device_link(session.device_id, link=session)
            invalidate_device_scheduler(session.device_id)
            await self._publish_status(session.device_id, online=False)
        if not session.closed:
            await session.close(1000)

    async def shutdown(self) -> None:
        """服务关闭时主动断开所有设备，避免连接悬挂。"""
        for session in list(self._sessions):
            await session.close(1011, "server_shutdown")

    async def _read_loop(self, session: DeviceSession) -> None:
        while True:
            try:
                message = await session.channel.receive()
            except Exception:
                logger.debug("device receive failed: %s", session.device_id, exc_info=True)
                return
            if message is None:
                return
            try:
                await self._on_message(session, message)
            except Exception:
                logger.exception("device message handling failed: %s", session.device_id)

    async def _on_message(self, session: DeviceSession, message: Any) -> None:
        # 二进制音频帧属于后续音频 Block；这里只保证不阻塞控制消息。
        if isinstance(message, (bytes, bytearray)):
            return
        if not isinstance(message, dict):
            return

        message_type = message.get("type")
        if message_type == "action.result":
            action_id = message.get("action_id")
            if not isinstance(action_id, str) or not action_id:
                return
            status = str(message.get("status") or "failed")
            detail = str(message.get("detail") or "")
            source = session.resolve_result(action_id, status, detail)
            await self.bus.publish(
                {
                    "v": 2,
                    "type": "action.result",
                    "device_id": session.device_id,
                    "action_id": action_id,
                    "status": status,
                    "detail": detail,
                    "source": source,
                    "ts": now_ms(),
                }
            )
            return

        if message_type == "device.report":
            report = session.apply_report(message)
            await self._persist_report(session.device_id, report)
            await self.bus.publish(
                {"v": 2, "type": "device.report", "device_id": session.device_id, **report}
            )
            return

        if message_type == "voice.event":
            await self.bus.publish(
                {
                    "v": 2,
                    "type": "voice.event",
                    "device_id": session.device_id,
                    "event": message.get("event"),
                    "turn_id": message.get("turn_id"),
                    "text": message.get("text"),
                    "ts": now_ms(),
                }
            )
            return

        logger.debug("ignore unknown device message: %s", message_type)

    # ---- 持久化 ----

    async def _load_device(self, device_id: str) -> DeviceModel | None:
        async with self._session_factory() as db:
            return await db.get(DeviceModel, device_id)

    async def _persist_hello(
        self,
        device_id: str,
        hello: dict[str, Any],
        protocol_version: int,
        capabilities: list[str],
    ) -> None:
        try:
            async with self._session_factory() as db:
                device = await db.get(DeviceModel, device_id)
                if device is None:
                    return
                firmware = hello.get("firmware_version")
                if isinstance(firmware, str):
                    device.firmware_version = firmware[:32]
                device.protocol_version = protocol_version
                device.capabilities_json = capabilities
                device.last_seen_at = now_ms()
                await db.commit()
        except Exception:
            logger.exception("persist hello failed: %s", device_id)

    async def _persist_report(self, device_id: str, report: dict[str, Any]) -> None:
        try:
            async with self._session_factory() as db:
                device = await db.get(DeviceModel, device_id)
                if device is None:
                    return
                device.volume = int(report["volume"])
                device.idle_enabled = bool(report["idle_enabled"])
                device.state = str(report["state"])
                device.pan_offset = float(report["pan_offset"])
                device.tilt_offset = float(report["tilt_offset"])
                device.last_report_json = report
                device.last_seen_at = now_ms()
                await db.commit()
        except Exception:
            logger.exception("persist report failed: %s", device_id)

    async def _publish_status(self, device_id: str, *, online: bool, reason: str = "") -> None:
        await self.bus.publish(
            {
                "v": 2,
                "type": "device.status",
                "device_id": device_id,
                "online": online,
                "reason": reason,
                "ts": now_ms(),
            }
        )

    @staticmethod
    async def _close(channel: WSChannel, code: int, reason: str) -> None:
        try:
            await channel.close(code, reason)
        except Exception:
            logger.debug("channel close failed", exc_info=True)
