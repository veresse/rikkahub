"""设备 WebSocket 网关的核心逻辑（与 Starlette 解耦）。

`routers/ws.py` 只负责把 Starlette WebSocket 包装成 `WSChannel` 并完成 Token
鉴权；握手、协议解析、指令关联、状态落库与上线/离线广播都在本模块，便于
用 FakeChannel 做离线单元测试。

协议契约见 `companion/docs/protocol_v2.md`。
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import update

from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal
from companion.server.app.models.device import DeviceModel
from companion.server.app.services.action_orchestrator import (
    invalidate_device_scheduler,
    mark_action_reported,
)
from companion.server.app.services.clock import now_ms
from companion.server.app.services.device_registry import (
    ActionResult,
    device_lifecycle_lock,
    get_device_link,
    register_device_link,
    unregister_device_link,
)
from companion.server.app.services.events import EventBus, get_event_bus, publish_async
from companion.shared.catalog import get_catalog

logger = logging.getLogger(__name__)

SUPPORTED_CAPABILITIES = {"action", "volume", "expression"}
VALID_STATES = {"idle", "listening", "thinking", "speaking", "error"}
VALID_POWER = {"usb", "battery", "unknown"}
PAN_LIMIT = 90.0
TILT_MIN = -20.0
TILT_MAX = 45.0
OFFSET_LIMIT = 30.0
VALID_CODECS = {"opus"}
VALID_FRAME_MS = {20, 60}
VALID_SAMPLE_RATES = {"uplink": {16000}, "downlink": {16000, 24000}}

DEFAULT_AUDIO = {
    "uplink": {"codec": "opus", "sample_rate": 16000, "channels": 1, "frame_ms": 60},
    "downlink": {"codec": "opus", "sample_rate": 24000, "channels": 1, "frame_ms": 60},
}

# writer 的停止哨兵
_WRITER_STOP = object()

# writer 停止预算：队列满时最多等 writer 取走当前条目，再等它写完剩余上报。
REPORT_STOP_QUEUE_TIMEOUT_SECONDS = 1.0
REPORT_STOP_DRAIN_TIMEOUT_SECONDS = 1.0


class WSChannel(Protocol):
    """WebSocket 的最小抽象，测试用 FakeChannel 实现。"""

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def receive(self) -> dict[str, Any] | bytes | None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _finite(value: Any) -> float | None:
    if not _is_number(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


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
        # closing 表示「已开始冲刷最后一条上报」（撤销 / 替换 / 下线）：
        # 链路不再接受新指令，但仍允许上报落库；closed 表示冲刷已完成。
        self.closing = False
        # ready 表示 hello_ack 已发出、且 `_mark_ready()` 在生命周期锁内复核过
        # 「仍是当前链路 + 未被撤销」。链路会比 ack 更早进入注册表，但协议 §2
        # 的 session_id 就在 ack 里，设备（含自带模拟器）会把 ack 之前的控制帧
        # 当成握手失败；因此复核通过前一律拒绝下发。
        self.ready = False
        # 关闭原因，供 `_teardown()` 广播 offline 时复用（撤销应显示
        # device_revoked，而不是和其它断线一样的空原因）。
        self.close_reason = ""
        # 已从注册表注销、但 offline 还没成功广播：停机/连接任务被取消时记下
        # 原因，重试的 `was_current` 已是 False，只能靠这个字段补发（否则 App
        # 永远收不到该设备的下线事件）。成功广播后清回 None。
        self.pending_offline_reason: str | None = None

        self._report = self._initial_report(device)
        self._pending: dict[str, asyncio.Future[ActionResult]] = {}
        self._sources: dict[str, str] = {}
        self._send_lock = asyncio.Lock()
        # 上报合并队列：设备高频上报时只保留最新一条，避免阻塞读循环。
        self._report_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
        self._report_handler: (
            Callable[[DeviceSession, dict[str, Any]], Awaitable[None]] | None
        ) = None
        self._writer: asyncio.Task | None = None
        self._writer_stopping = False
        # 串行化 stop_writer()：teardown/shutdown/handle 异常路径可能并发调用，
        # 两个 _flush_reports() 同时取走 in-flight 与队列会导致 FIFO 反转。
        self._writer_stop_lock = asyncio.Lock()
        # 串行化 close()：撤销与替换可能并发，重复 close 只应生效一次。
        self._close_lock = asyncio.Lock()
        # writer 已取出、正在 await handler 的上报；超时取消后用于补写。
        self._inflight: dict[str, Any] | None = None

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
        # closing 期间会话仍是 current link（正在冲刷最后一条上报），但不能再
        # 下发指令：撤销窗口内让命令命中已撤销设备是协议外的行为。
        # ready 之前（hello_ack 未发出）同理：设备还没拿到 session_id。
        if not self.ready or self.closing or self.closed:
            return ActionResult(status="failed", detail="device_offline")
        if self.protocol_version < 2:
            # v1 只走音频通道，不允许任何 v2 控制消息。
            return ActionResult(status="failed", detail="unsupported_protocol")

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
            # closing 表示链路已不可写（撤销/替换/写失败）：对 App 与 LLM 都
            # 按离线处理，避免「显示在线但所有指令都失败」。ready 之前同理。
            "online": self.ready and not (self.closed or self.closing),
        }

    def public_report(self) -> dict[str, Any]:
        """协议 §5.2 形状；对外统一用 `idle` 而不是内部字段名。"""
        report = self._report
        return {
            "volume": report["volume"],
            "pan": report["pan"],
            "tilt": report["tilt"],
            "idle": bool(report["idle_enabled"]),
            "power": report["power"],
            "state": report["state"],
            "pan_offset": report["pan_offset"],
            "tilt_offset": report["tilt_offset"],
            "ts": now_ms(),
        }

    # ---- 上报写出 ----

    def start_writer(
        self, handler: Callable[[DeviceSession, dict[str, Any]], Awaitable[None]]
    ) -> None:
        self._report_handler = handler
        self._writer_stopping = False
        self._writer = asyncio.create_task(self._writer_loop())

    def queue_report(self, report: dict[str, Any]) -> None:
        if self.closed or self._writer_stopping:
            return
        try:
            self._report_queue.put_nowait(report)
        except asyncio.QueueFull:
            try:
                self._report_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._report_queue.put_nowait(report)
            except asyncio.QueueFull:
                pass

    async def _run_report_handler(self, report: dict[str, Any]) -> None:
        if report is _WRITER_STOP or self._report_handler is None:
            return
        try:
            await self._report_handler(self, report)
        except Exception:
            logger.exception("device report handler failed: %s", self.device_id)

    async def _writer_loop(self) -> None:
        while True:
            report = await self._report_queue.get()
            if report is _WRITER_STOP:
                return
            self._inflight = report
            try:
                await self._run_report_handler(report)
            except asyncio.CancelledError:
                # 保留 `_inflight`，由 `_flush_reports()` 在取消后补写。
                raise
            self._inflight = None

    async def _flush_reports(self) -> None:
        """把 in-flight 与合并队列里剩下的上报处理完。

        用于正常下线前保住最后一条状态。被取消的 writer 可能已经取出上报却
        没写完，因此先补处理 `_inflight`；上报是绝对快照，重试幂等。
        """
        report = self._inflight
        self._inflight = None
        if report is not None:
            await self._run_report_handler(report)
        while True:
            try:
                report = self._report_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await self._run_report_handler(report)

    async def stop_writer(self) -> None:
        """优雅停止 writer：按 FIFO 冲刷最后一条上报后退出。

        不能一边让 writer 跑、一边自己 flush：两个 handler 并发写库时，慢的
        旧上报可能晚于新上报提交，把最新状态覆盖回旧值。这里先投递停止哨兵，
        让 writer 以自己的顺序处理完剩余上报；只有 writer 卡死超时才取消它，
        再由当前协程接管队列。

        调用方可能并发（`_teardown` / `shutdown` / `handle()` 异常路径），
        因此整段冲刷必须串行；否则两个 `_flush_reports()` 会分别取走
        in-flight 与队列条目，慢的旧上报仍可能后提交。
        """
        async with self._writer_stop_lock:
            await self._drain_writer()

    async def _drain_writer(self) -> None:
        writer = self._writer
        self._writer_stopping = True
        if writer is None or writer.done():
            await self._flush_reports()
            return

        # 队列容量为 1；等 writer 取走当前条目后再放哨兵。
        try:
            await asyncio.wait_for(
                self._report_queue.put(_WRITER_STOP),
                timeout=REPORT_STOP_QUEUE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            pass

        try:
            await asyncio.wait_for(
                asyncio.shield(writer),
                timeout=REPORT_STOP_DRAIN_TIMEOUT_SECONDS,
            )
            return
        except asyncio.TimeoutError:
            writer.cancel()
        except asyncio.CancelledError:
            if not writer.cancelled():
                raise

        await asyncio.gather(writer, return_exceptions=True)
        await self._flush_reports()

    def _stop_writer(self) -> None:
        if self._writer and not self._writer.done():
            self._writer.cancel()

    # ---- 网关内部接口 ----

    async def _safe_send(self, payload: dict[str, Any]) -> bool:
        try:
            async with self._send_lock:
                await self.channel.send_json(payload)
            return True
        except Exception:
            logger.debug("device send failed: %s", self.device_id, exc_info=True)
            # 只标记「不再下发」，不能置 closed：close()/teardown 的冲刷依赖
            # closed=False 才能把最后一条上报落库。
            self.closing = True
            return False

    def resolve_result(self, action_id: str, status: str, detail: str) -> str | None:
        """把 `action.result` 交给等待中的工具调用。

        只有仍在等待的命令才会返回来源；服务端已结算（超时/急停/断连）或
        从未下发的 `action_id` 一律返回 `None`，由调用方丢弃，避免同一
        `action_id` 被广播两次、或广播出协议枚举之外的 `source=""`。
        """
        future = self._pending.get(action_id)
        if future is None or future.done():
            return None
        source = self._sources.get(action_id, "")
        if not future.done():
            future.set_result(
                ActionResult(status=status, detail=detail, action_id=action_id)
            )
        return source

    def _resolve_all(self, result: ActionResult) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(result)

    def settle_pending(self, action_id: str, status: str, detail: str) -> None:
        """服务端已生成终态时，终止设备侧等待，避免迟到回执再被广播。"""
        future = self._pending.get(action_id)
        if future is not None and not future.done():
            future.set_result(
                ActionResult(status=status, detail=detail, action_id=action_id)
            )

    def apply_report(self, report: dict[str, Any]) -> dict[str, Any]:
        volume = _finite(report.get("volume"))
        if volume is not None:
            self._report["volume"] = int(_clamp(volume, 0, 100))

        idle = report.get("idle")
        if isinstance(idle, bool):
            self._report["idle_enabled"] = idle

        state = report.get("state")
        if isinstance(state, str) and state in VALID_STATES:
            self._report["state"] = state

        pan = _finite(report.get("pan"))
        if pan is not None:
            self._report["pan"] = _clamp(pan, -PAN_LIMIT, PAN_LIMIT)
        tilt = _finite(report.get("tilt"))
        if tilt is not None:
            self._report["tilt"] = _clamp(tilt, TILT_MIN, TILT_MAX)

        for key in ("pan_offset", "tilt_offset"):
            value = _finite(report.get(key))
            if value is not None:
                self._report[key] = _clamp(value, -OFFSET_LIMIT, OFFSET_LIMIT)

        power = report.get("power")
        if isinstance(power, str) and power in VALID_POWER:
            self._report["power"] = power
        return dict(self._report)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        async with self._close_lock:
            if self.closed:
                return
            # 在第一个 await 之前同步置 closing：flush 期间会话仍是 current
            # link，`send()` 必须拒绝新指令，否则撤销窗口内命令仍会下发到
            # 已撤销设备；而 `_handle_report()` 只看 closed，flush 照常落库。
            self.closing = True
            # 必须先冲刷再标记 closed：`_handle_report()` 会丢弃 closed 会话的
            # 上报，撤销设备、会话替换等直接调用 close() 的路径否则会丢最后一条。
            if reason:
                self.close_reason = reason
            await self.stop_writer()
            self.closed = True
            self._stop_writer()
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
        self._shutting_down = False

    def open_for_connections(self) -> None:
        """应用（重新）启动时允许注册新链路。

        `_shutting_down` 只对一次停机有效；同进程重新跑 lifespan（测试/嵌入式
        运行）时需要复位，否则所有握手都会被当作停机拒绝。
        """
        self._shutting_down = False

    async def handle(self, device_id: str, channel: WSChannel) -> None:
        session: DeviceSession | None = None
        device_loaded = False
        # 握手阶段就失败时广播的 offline 原因；撤销不是握手错误，单独区分，
        # 否则 App 会在刚撤销设备后收到 reason=handshake_failed 的误导事件。
        handshake_reason = "handshake_failed"
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

            # 鉴权在 hello 之前完成；撤销可能正好落在「等待 hello」的窗口里，
            # 此时注册表里还没有链路，撤销路由关不掉它。这里先复核一次，
            # `_install()` 会在每设备生命周期锁内再复核（权威判定）。
            if not await self._device_is_active(device_id):
                handshake_reason = "device_revoked"
                await self._close(channel, 4401, "device_revoked")
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
            if not await self._install(session):
                return
            # 注册早于 ack，但 ack 之前链路一律不可下发（见 DeviceSession.ready）。
            # ack 之后还要在锁内复核「未被撤销」才置 ready：撤销的 DB 提交与
            # link.close() 在同一把生命周期锁内完成，复核通过才允许控制。
            await self._send_hello_ack(session, hello)
            await self._mark_ready(session)
            await self._read_loop(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("device gateway error: %s", device_id)
            if session is not None:
                # 与撤销/停机在同一把锁内关闭：异常若与撤销竞争，谁先进锁谁决定
                # 关闭码与 `close_reason`，不会出现「已撤销但报 internal_error」
                # 之外的乱序。`close()` 内部会先冲刷 writer。
                async with device_lifecycle_lock(device_id):
                    await session.close(1011, "internal_error")
            else:
                await self._close(channel, 1011, "internal_error")
        finally:
            if session is not None:
                await self._teardown(session)
            elif device_loaded and get_device_link(device_id) is None:
                # 失败握手不应把仍由旧连接托管的设备标记为离线。
                await self._publish_status(
                    device_id, online=False, reason=handshake_reason
                )

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

        audio, audio_ok = self._sanitize_audio(hello.get("audio"))
        if not audio_ok:
            warnings.append("audio_defaulted")

        volume_limits = limits.get("volume", {})
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
                    "volume": [volume_limits.get("min", 0), volume_limits.get("max", 100)],
                    "default_max_duration_ms": limits.get("default_max_duration_ms", 3000),
                    "hard_max_duration_ms": limits.get("hard_max_duration_ms", 5000),
                    "rate_limit_per_second": limits.get("rate_limit_per_second", 10),
                },
                "warnings": warnings,
            }
        )

    @staticmethod
    def _sanitize_audio(audio: Any) -> tuple[dict[str, Any], bool]:
        """校验 `hello.audio`，非法字段回落默认值并让调用方产生 warning。"""
        if not isinstance(audio, dict):
            return {k: dict(v) for k, v in DEFAULT_AUDIO.items()}, False

        sanitized: dict[str, Any] = {}
        ok = True
        for direction in ("uplink", "downlink"):
            default = DEFAULT_AUDIO[direction]
            provided = audio.get(direction)
            if not isinstance(provided, dict):
                ok = False
                sanitized[direction] = dict(default)
                continue
            codec = provided.get("codec", default["codec"])
            sample_rate = provided.get("sample_rate", default["sample_rate"])
            channels = provided.get("channels", default["channels"])
            frame_ms = provided.get("frame_ms", default["frame_ms"])
            valid = (
                isinstance(codec, str)
                and codec in VALID_CODECS
                and isinstance(sample_rate, int)
                and not isinstance(sample_rate, bool)
                and sample_rate in VALID_SAMPLE_RATES[direction]
                and isinstance(channels, int)
                and not isinstance(channels, bool)
                and channels == 1
                and isinstance(frame_ms, int)
                and not isinstance(frame_ms, bool)
                and frame_ms in VALID_FRAME_MS
            )
            if not valid:
                ok = False
                sanitized[direction] = dict(default)
            else:
                sanitized[direction] = {
                    "codec": codec,
                    "sample_rate": sample_rate,
                    "channels": channels,
                    "frame_ms": frame_ms,
                }
        return sanitized, ok

    # ---- 连接生命周期 ----

    async def _device_is_active(self, device_id: str) -> bool:
        """复核设备未被撤销。

        读取失败时按「仍活跃」处理：此时 `_load_device()` 也已失败，连接会走
        错误路径；这里只做撤销窗口的补充检查，不宜因 DB 抖动拒绝正常设备。
        """
        try:
            async with self._session_factory() as db:
                device = await db.get(DeviceModel, device_id)
        except Exception:
            logger.exception("load device active failed: %s", device_id)
            return True
        return device is not None and bool(device.is_active)

    async def _install(self, session: DeviceSession) -> bool:
        """注册链路；设备已撤销/服务正在停机时拒绝并关闭，返回是否注册成功。"""
        async with device_lifecycle_lock(session.device_id):
            # 撤销路由在同一把锁里「查链路 + close(4401)」。锁内复核 is_active
            # 才能堵住：鉴权通过 → 撤销提交（此时还没有链路）→ hello 到达 →
            # 连接重新注册并可控。
            if not await self._device_is_active(session.device_id):
                await session.close(4401, "device_revoked")
                # 与 `handle()` 预检路径保持一致：撤销导致的握手失败同样广播
                # offline（reason 不是 handshake_failed），失败后不会重复发。
                await self._publish_status(
                    session.device_id, online=False, reason="device_revoked"
                )
                return False
            # DB 读是 await，期间可能已开始停机；此后不再接受新链路，否则该
            # 会话会错过 `shutdown()` 的遍历，还会在停机过程中广播上线。
            if self._shutting_down:
                await session.close(1011, "server_shutdown")
                return False
            old = get_device_link(session.device_id)
            # 替换前先在旧链路仍是 current 时冲刷上报 writer：
            # `_handle_report()` 会丢弃「已不是当前链路」的上报，先注册新链路
            # 会让旧连接最后一条 device.report 永远不落库，重连快照回退。
            if isinstance(old, DeviceSession) and old is not session:
                await old.stop_writer()
            register_device_link(session.device_id, session)
            self._sessions.add(session)
            session.start_writer(self._handle_report)
            if isinstance(old, DeviceSession) and old is not session:
                await old.close(4409, "replaced")
            return True

    async def _mark_ready(self, session: DeviceSession) -> None:
        """hello_ack 发出后，在生命周期锁内复核并置 ready + 广播上线。

        `ready` 只能在复核之后置位：撤销路由在同一把锁内「提交 is_active=False
        → close(4401)」，因此锁内复核通过意味着撤销尚未发生；反之若撤销先拿到
        锁，链路已 `closing/closed`，这里不会再放行任何控制帧。
        """
        async with device_lifecycle_lock(session.device_id):
            if get_device_link(session.device_id) is not session:
                # 期间被更新的连接替换：上线事件由新连接负责，避免认领错链路。
                return
            if not await self._device_is_active(session.device_id):
                # 防御性复核：撤销若来自本进程之外（多进程 / 直接改库），提交与
                # close 不经过这把锁，这里仍要拒绝上线。offline 由 `_teardown()`
                # 统一广播，避免重复。
                await session.close(4401, "device_revoked")
                return
            if session.closed or session.closing:
                # 上一行的 DB 读是 await：期间 `close()`（不经生命周期锁的写失败 /
                # 停机路径）可能已经关闭链路。置 ready 前统一在这里确认，避免
                # 「已关闭但仍置 ready 并广播上线」。
                return
            if self._shutting_down:
                # 停机已开始：不再广播上线（链路会由 `shutdown()` 关闭）。
                return
            session.ready = True
            await self._publish_status(session.device_id, online=True)

    async def _teardown(self, session: DeviceSession) -> None:
        was_current = False
        try:
            async with device_lifecycle_lock(session.device_id):
                self._sessions.discard(session)
                was_current = get_device_link(session.device_id) is session
                if was_current:
                    # 下线前把最后一条上报落库，避免重连后快照回退。
                    await session.stop_writer()
                    unregister_device_link(session.device_id, link=session)
                    invalidate_device_scheduler(session.device_id)
                if not session.closed:
                    await session.close(1000)
                if was_current:
                    session.pending_offline_reason = session.close_reason
        except BaseException:
            # uvicorn 在 drain 超时后会取消连接任务：此时链路可能已注销，若只是
            # 让取消穿过去，它既不在注册表也不在集合里，offline 就永久丢失。
            # 把未完成的关闭/广播放回集合，交给停机兜底重试（幂等）。
            if was_current and session.pending_offline_reason is None:
                session.pending_offline_reason = session.close_reason
            if session.pending_offline_reason is not None or not session.closed:
                self._sessions.add(session)
            raise
        await self._publish_pending_offline(session)

    async def shutdown(self) -> None:
        """服务关闭时主动断开所有设备，避免连接悬挂。

        先冲刷 writer 再注销链路，否则最后一条 `device.report` 会因
        `_handle_report` 的「已不是当前链路」检查而被丢弃。

        置 `_shutting_down` 后不再注册新链路；逐个从 `_sessions` 取出处理（而不是
        先拍快照再 `clear()`），这样「`_install()` 在快照之后才完成」的会话也会被
        关闭。每条设备的 offline 在释放生命周期锁后**立即**广播，已经关闭的设备
        不会因为后面还有慢设备而漏发。

        被取消（上层总时限/`force_exit`）时把当前会话放回集合，并把待发原因记在
        会话上：取消若落在 unregister 之后，重试的 `was_current` 已为 False，只有
        `pending_offline_reason` 能让 lifespan 兜底补发这条 offline。取消发生在
        广播本身时同样写回原因重试，因此 offline 是 at-least-once（可能重复，
        不会丢失）。
        """
        self._shutting_down = True
        while self._sessions:
            session = self._sessions.pop()
            was_current = False
            try:
                # 与 `_teardown` 串行，避免一方在另一方 flush 期间 unregister 导致
                # 最后一条上报被 `_handle_report` 的链路检查丢弃。
                async with device_lifecycle_lock(session.device_id):
                    await session.stop_writer()
                    was_current = get_device_link(session.device_id) is session
                    if was_current:
                        unregister_device_link(session.device_id, link=session)
                        invalidate_device_scheduler(session.device_id)
                    await session.close(1011, "server_shutdown")
                    if was_current:
                        # 链路若已被撤销/异常路径关闭，`close()` 是空操作，
                        # `close_reason` 保留先到的原因；否则就是 server_shutdown。
                        session.pending_offline_reason = (
                            session.close_reason or "server_shutdown"
                        )
            except BaseException:
                if was_current and session.pending_offline_reason is None:
                    session.pending_offline_reason = (
                        session.close_reason or "server_shutdown"
                    )
                self._sessions.add(session)
                raise
            await self._publish_pending_offline(session)

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
        # 已被替换/关闭的连接不再处理上报，避免旧连接覆盖新会话状态。
        if session.closed:
            return
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
            if source is None:
                # 服务端已广播过终态（超时/急停/断连）或该 action_id 不是本
                # 会话下发的；迟到回执只更新设备内部状态，不再打扰 App。
                return
            # 同步标记，堵住「设备先回报、服务端随后急停/断连」的重复终态窗口。
            # `False` 表示服务端已广播过终态（例如 stop 正在发送 action.stop
            # 时设备回执才到），此时必须丢弃设备回执，不能再广播第二条。
            if mark_action_reported(session.device_id, action_id) is False:
                return
            publish_async(
                self.bus,
                {
                    "v": 2,
                    "type": "action.result",
                    "device_id": session.device_id,
                    "action_id": action_id,
                    "status": status,
                    "detail": detail,
                    "source": source,
                    "ts": now_ms(),
                },
            )
            return

        if message_type == "device.report":
            # 高频上报只保留最新一条，落库与广播交给独立 writer，避免阻塞读循环。
            session.queue_report(message)
            return

        if message_type == "voice.event":
            publish_async(
                self.bus,
                {
                    "v": 2,
                    "type": "voice.event",
                    "device_id": session.device_id,
                    "event": message.get("event"),
                    "turn_id": message.get("turn_id"),
                    "text": message.get("text"),
                    "ts": now_ms(),
                },
            )
            return

        logger.debug("ignore unknown device message: %s", message_type)

    async def _handle_report(self, session: DeviceSession, report: dict[str, Any]) -> None:
        """合并队列的消费者：应用上报、落库、广播协议 §5.2 形状。"""
        # 落库不能因 closing 跳过：close() 正靠这条路径冲刷最后一条上报。
        if session.closed or get_device_link(session.device_id) is not session:
            return
        internal = session.apply_report(report)
        active = await self._persist_report(session.device_id, internal)
        if not active:
            # 已撤销设备不再广播运行态：否则 App 会先收到 state=speaking，
            # 再收到 device.status online=false，短暂把 revoked 显示为在线。
            return
        # closing 时只落库不广播：链路正在下线/替换，事件流不应再把它展示成在线。
        if (
            session.closed
            or session.closing
            or get_device_link(session.device_id) is not session
        ):
            return
        await self.bus.publish(
            {
                "v": 2,
                "type": "device.report",
                "device_id": session.device_id,
                **session.public_report(),
            }
        )

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
        """记录固件信息与最后在线时间。

        与上报一样用带 `is_active` 条件的 UPDATE：读-改-写会把撤销提交前读到
        的旧 `is_active=True` 当作依据，随后仍刷新已撤销设备的固件/`last_seen_at`。
        """
        values: dict[str, Any] = {
            "protocol_version": protocol_version,
            "capabilities_json": capabilities,
            "last_seen_at": now_ms(),
        }
        firmware = hello.get("firmware_version")
        if isinstance(firmware, str):
            values["firmware_version"] = firmware[:32]
        try:
            async with self._session_factory() as db:
                await db.execute(
                    update(DeviceModel)
                    .where(
                        DeviceModel.id == device_id,
                        DeviceModel.is_active.is_(True),
                    )
                    .values(**values)
                )
                await db.commit()
        except Exception:
            logger.exception("persist hello failed: %s", device_id)

    async def _persist_report(self, device_id: str, report: dict[str, Any]) -> bool:
        """落库上报，返回设备是否仍是活动设备（决定能否广播运行态）。

        运行态字段用「带 `is_active` 条件的 UPDATE」写入，而不是先读 ORM 再改：
        撤销路由可能在读取与提交之间提交 `is_active=False`，读-改-写会把
        `state="revoked"` 覆盖回 `speaking`（窗口大小随调度变化，只能靠条件
        更新消除，不能靠 `close()` 的冲刷顺序）。
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    update(DeviceModel)
                    .where(
                        DeviceModel.id == device_id,
                        DeviceModel.is_active.is_(True),
                    )
                    .values(
                        volume=int(report["volume"]),
                        idle_enabled=bool(report["idle_enabled"]),
                        state=str(report["state"]),
                        pan_offset=float(report["pan_offset"]),
                        tilt_offset=float(report["tilt_offset"]),
                        last_report_json=report,
                        last_seen_at=now_ms(),
                    )
                )
                active = bool(result.rowcount)
                if not active:
                    # 已撤销/停用：只保留快照，运行态（含 state="revoked"）不动。
                    await db.execute(
                        update(DeviceModel)
                        .where(DeviceModel.id == device_id)
                        .values(last_report_json=report)
                    )
                await db.commit()
                return active
        except Exception:
            logger.exception("persist report failed: %s", device_id)
            # 落库失败不代表设备离线，保持原有广播行为。
            return True

    async def _publish_pending_offline(self, session: DeviceSession) -> None:
        """发出 `pending_offline_reason`；广播被取消时把会话放回集合重试。

        offline 在 unregister 之后才广播，取消可能落在两者之间。此时重试的
        `was_current` 已是 False、注册表里也没有链路，只有会话上的待发原因能
        让兜底把这条事件发出去。

        先清空再 `await`：`_teardown()` 或另一次 `shutdown()` 可能与本条广播并发，
        若等广播返回后才清空，它们会看到同一个待发原因并重复发 offline。广播被
        取消时再把原因写回并放回集合，宁可重试也不丢；取消发生在总线已投递之后
        时，接收方可能看到重复的 `online=false`（at-least-once）。

        若期间已有新链路接管（停机标志解除后重新连接），上线事件由新链路负责，
        这条过期的 offline 应被丢弃，避免把在线设备标成离线。注意这是 best-effort：
        检查与 `bus.publish()` 的逐订阅者 `await` 之间仍可能插入新链路的
        `_install()/_mark_ready()`，旧 offline 可能晚于 online 送达。该窗口在把广播
        移出生命周期锁后才出现（原 `_teardown()` 是锁内检查 + 锁内广播）；要彻底
        消除需把检查与投递放进同一把生命周期锁。
        """
        reason = session.pending_offline_reason
        if reason is None:
            return
        session.pending_offline_reason = None
        if get_device_link(session.device_id) is not None:
            return
        try:
            await self._publish_status(session.device_id, online=False, reason=reason)
        except BaseException:
            if session.pending_offline_reason is None:
                session.pending_offline_reason = reason
            self._sessions.add(session)
            raise

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
