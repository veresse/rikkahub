"""动作编排：参数校验、单轮限次、设备排队、限速与超时。

本模块只依赖 `DeviceLink` 抽象，不关心底层是 WebSocket、模拟器还是测试桩。
设备离线时立即失败，绝不缓存等待上线补发。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from companion.server.app.config import settings
from companion.server.app.services.action_tools import ROBOT_TOOL_NAMES
from companion.server.app.services.device_registry import ActionResult, get_device_link
from companion.server.app.services.events import get_event_bus, publish_async
from companion.shared.catalog import CatalogError, get_action, resolve_params

logger = logging.getLogger(__name__)

MAX_PENDING_COMMANDS = 8
MAX_COMMANDS_PER_SECOND = 10
RATE_WINDOW_SECONDS = 1.0
DEFAULT_COMMAND_TIMEOUT_MS = 3000


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ActionTurnState:
    turn_id: str
    max_actions: int = 3
    used_actions: int = 0
    # 本轮被取消时下发给设备的 action.stop 原因：SSE 为 server_cancel，
    # 语音 barge-in 为 barge_in。只影响本轮在途动作，不做全局急停。
    cancel_reason: str = "server_cancel"

    def consume(self) -> bool:
        if self.used_actions >= self.max_actions:
            return False
        self.used_actions += 1
        return True


@dataclass
class _Job:
    command: dict[str, Any]
    future: asyncio.Future[ActionResult] = field(repr=False)
    # 排队陈旧判定（不是执行超时）；超过该时刻仍未下发就丢弃。
    deadline: float | None = None
    sent: bool = False
    cancel_reason: str = "server_cancel"
    started: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    emitted: bool = False


# App 遥控命令允许在队列里等待的最长时间；执行超时另行按命令类型计算。
QUEUE_MAX_WAIT_MS = 30_000


def _command_timeout_ms(command: dict[str, Any]) -> int:
    """一条指令从下发到设备回执的服务器侧预算。"""
    kind = command.get("type")
    if kind == "action.play":
        try:
            base = int(get_action(str(command.get("name")))["max_duration_ms"])
        except KeyError:
            base = DEFAULT_COMMAND_TIMEOUT_MS
        return base + settings.ACTION_RESULT_TIMEOUT_EXTRA_MS
    if kind == "servo.move":
        speed = command.get("speed", 500)
        if isinstance(speed, bool) or not isinstance(speed, int):
            speed = 500
        return max(DEFAULT_COMMAND_TIMEOUT_MS, speed + 1000)
    return DEFAULT_COMMAND_TIMEOUT_MS


class _DeviceScheduler:
    """单设备串行执行器：FIFO、队列上限、限速、覆盖与急停。"""

    def __init__(self, device_id: str, link: Any):
        self.device_id = device_id
        self.link = link
        self.queue: deque[_Job] = deque()
        self.worker: asyncio.Task | None = None
        self.current: _Job | None = None
        self._events: deque[float] = deque()
        self._stopped = False

    def _allow_rate(self) -> bool:
        now = time.monotonic()
        while self._events and now - self._events[0] > RATE_WINDOW_SECONDS:
            self._events.popleft()
        if len(self._events) >= MAX_COMMANDS_PER_SECOND:
            return False
        self._events.append(now)
        return True

    def _ensure_worker(self) -> None:
        if self._stopped:
            return
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        try:
            while self.queue and not self._stopped:
                job = self.queue.popleft()
                if job.future.done():
                    continue
                self.current = job
                try:
                    if job.deadline is not None and time.monotonic() > job.deadline:
                        self._terminate(job, "failed", "timeout")
                        continue
                    job.sent = True
                    job.started.set()
                    try:
                        result = await asyncio.wait_for(
                            self.link.send(job.command),
                            timeout=_command_timeout_ms(job.command) / 1000,
                        )
                    except asyncio.TimeoutError:
                        self._terminate(job, "failed", "timeout")
                        await self._send_stop(job.cancel_reason)
                        continue
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self._terminate(job, "failed", "internal_error")
                        continue
                    # 设备自己上报的结果已经由 DeviceGateway 广播；这里只补链路层
                    # 失败（无 action_id）这类设备不会自己上报的终态，避免 App
                    # 永远停在 queued。
                    device_reported = result.action_id == job.command.get("action_id")
                    self._terminate(
                        job, result.status, result.detail, emit=not device_reported
                    )
                finally:
                    self.current = None
        finally:
            self.current = None
            self.worker = None
            if self.queue and not self._stopped:
                self._ensure_worker()

    async def submit(
        self,
        command: dict[str, Any],
        *,
        timeout_ms: int,
        override: bool = False,
        cancel_reason: str = "server_cancel",
    ) -> ActionResult:
        if not self._allow_rate():
            return ActionResult(status="failed", detail="rate_limited")
        if override:
            # 覆盖必须先于容量判断：队列已满时也要让最新一条替换旧角度。
            self._drop_superseded(command.get("type"))
        if len(self.queue) >= MAX_PENDING_COMMANDS:
            return ActionResult(status="failed", detail="queue_full")

        job = _Job(
            command=command,
            future=asyncio.get_running_loop().create_future(),
            cancel_reason=cancel_reason,
        )
        self.queue.append(job)
        self._ensure_worker()

        # 只有真正下发后才开始计算执行超时；排队最多等 QUEUE_MAX_WAIT_MS。
        try:
            await asyncio.wait_for(job.started.wait(), timeout=QUEUE_MAX_WAIT_MS / 1000)
        except asyncio.TimeoutError:
            self._abort(job)
            return ActionResult(status="timeout", detail="timeout")
        except asyncio.CancelledError:
            # 调用方（客户端断开/用户停止）取消：不要再让排队动作继续执行。
            was_running = job.sent and self.current is job
            self._abort(job)
            if was_running:
                await asyncio.shield(self._send_stop(job.cancel_reason))
            raise

        try:
            return await asyncio.wait_for(asyncio.shield(job.future), timeout_ms / 1000)
        except asyncio.TimeoutError:
            # 已下发且仍是当前任务才急停；纯排队超时不得打断别的动作。
            was_running = job.sent and self.current is job
            self._abort(job)
            if was_running:
                await self._send_stop(job.cancel_reason)
            return ActionResult(status="timeout", detail="timeout")
        except asyncio.CancelledError:
            was_running = job.sent and self.current is job
            self._abort(job)
            if was_running:
                await asyncio.shield(self._send_stop(job.cancel_reason))
            raise

    async def enqueue(
        self, command: dict[str, Any], *, override: bool = False
    ) -> ActionResult:
        """App 遥控用：入队后立刻返回 `queued`，结果走事件广播。

        排队超过 `QUEUE_MAX_WAIT_MS` 仍未下发的命令会被 worker 丢弃，并广播
        `action.result(failed, detail="timeout")`；执行阶段的超时由 worker 的
        `_command_timeout_ms` 负责。
        """
        if not self._allow_rate():
            return ActionResult(status="failed", detail="rate_limited")
        if override:
            self._drop_superseded(command.get("type"))
        if len(self.queue) >= MAX_PENDING_COMMANDS:
            return ActionResult(status="failed", detail="queue_full")

        job = _Job(
            command=command,
            future=asyncio.get_running_loop().create_future(),
            deadline=time.monotonic() + QUEUE_MAX_WAIT_MS / 1000,
        )
        self.queue.append(job)
        self._ensure_worker()
        # 让 worker 有机会在本调用返回前把命令写进通道。
        await asyncio.sleep(0)
        return ActionResult(status="queued")

    def _abort(self, job: _Job) -> None:
        if not job.future.done():
            job.future.set_result(ActionResult(status="timeout", detail="timeout"))
        job.started.set()
        try:
            self.queue.remove(job)
        except ValueError:
            pass

    def _drop_superseded(self, command_type: Any) -> None:
        """覆盖语义：同类型未下发的旧指令标记 superseded 并出队。"""
        kept: deque[_Job] = deque()
        for job in self.queue:
            if job.command.get("type") == command_type:
                self._terminate(job, "failed", "superseded")
            else:
                kept.append(job)
        self.queue = kept

    def _terminate(
        self,
        job: _Job,
        status: str,
        detail: str,
        *,
        emit: bool = True,
    ) -> ActionResult:
        result = ActionResult(status=status, detail=detail)
        if not job.future.done():
            job.future.set_result(result)
        job.started.set()
        if emit:
            self._emit_terminal(job, status, detail)
        return result

    def _emit_terminal(self, job: _Job, status: str, detail: str) -> None:
        """把服务端生成的终态广播给 App（设备自己上报的结果不重复发）。"""
        if job.emitted:
            return
        job.emitted = True
        command = job.command
        action_id = command.get("action_id")
        # 同步终止设备侧等待：即使调度器随后被 invalidate 移除，迟到的设备
        # `action.result` 也无法再从 `_pending` 里冒出来重复广播。
        if isinstance(action_id, str) and action_id:
            settle = getattr(self.link, "settle_pending", None)
            if callable(settle):
                settle(action_id, status, detail)
        if command.get("source") != "app":
            return
        if not isinstance(action_id, str) or not action_id:
            return
        event = {
            "v": 2,
            "type": "action.result",
            "device_id": self.device_id,
            "action_id": action_id,
            "status": status,
            "detail": detail,
            "source": "app",
            "ts": _now_ms(),
        }
        logger.info(
            "action terminal device=%s action_id=%s status=%s detail=%s",
            self.device_id,
            action_id,
            status,
            detail,
        )
        publish_async(get_event_bus(), event)

    async def _send_stop(self, reason: str) -> ActionResult:
        command = {
            "v": 2,
            "type": "action.stop",
            "reason": reason,
            "ts": _now_ms(),
        }
        try:
            return await asyncio.wait_for(self.link.send(command), timeout=1.0)
        except asyncio.TimeoutError:
            return ActionResult(status="failed", detail="timeout")
        except Exception:
            return ActionResult(status="failed", detail="internal_error")

    async def stop(self, reason: str) -> ActionResult:
        if self.current is not None:
            self._terminate(self.current, "failed", "stopped")
        for job in self.queue:
            self._terminate(job, "failed", "stopped")
        self.queue.clear()
        return await self._send_stop(reason)


_SCHEDULERS: dict[str, _DeviceScheduler] = {}


def _stop_scheduler(scheduler: _DeviceScheduler, detail: str, *, emit_current: bool = True) -> None:
    scheduler._stopped = True
    if scheduler.current is not None:
        scheduler._terminate(
            scheduler.current, "failed", detail, emit=emit_current
        )
    for job in scheduler.queue:
        scheduler._terminate(job, "failed", detail)
    scheduler.queue.clear()
    if scheduler.worker and not scheduler.worker.done():
        try:
            scheduler.worker.cancel()
        except RuntimeError:
            # 事件循环已关闭（测试 teardown / 进程退出）时无需再取消。
            pass


def invalidate_device_scheduler(device_id: str) -> None:
    """设备断开：终止所有排队/在途任务，且不再自动复活 worker。"""
    scheduler = _SCHEDULERS.pop(device_id, None)
    if scheduler is not None:
        _stop_scheduler(scheduler, "device_offline")


def mark_action_reported(device_id: str, action_id: str) -> bool | None:
    """设备已经回报该 `action_id` 的终态，调度器不得再生成第二条终态。

    必须在 `DeviceSession.resolve_result()` 解析 future 的同一事件循环 tick
    内同步调用。否则急停/断连可能在 worker 恢复前看到仍是 `current` 的 job，
    再广播一次 `failed/stopped`，违反协议「同一 action_id 只出现一次」。

    返回值：
    - `True`：本次首次标记，设备回执应当广播；
    - `False`：该 job 已有服务端终态（`emitted=True`），设备回执必须丢弃；
    - `None`：没有匹配的调度器（直接调用 `DeviceSession.send` 的测试/内部路径）。
    """
    scheduler = _SCHEDULERS.get(device_id)
    if scheduler is None or scheduler.current is None:
        return None
    job = scheduler.current
    if job.command.get("action_id") != action_id:
        return None
    if job.emitted:
        return False
    job.emitted = True
    return True


def get_device_scheduler(device_id: str, link: Any) -> _DeviceScheduler:
    """按设备取唯一调度器；设备链路变化时先终止旧队列，避免向旧连接发指令。"""
    scheduler = _SCHEDULERS.get(device_id)
    if scheduler is not None and scheduler.link is not link:
        _SCHEDULERS.pop(device_id, None)
        _stop_scheduler(scheduler, "device_offline")
        scheduler = None
    if scheduler is None:
        scheduler = _DeviceScheduler(device_id, link)
        _SCHEDULERS[device_id] = scheduler
    return scheduler


def reset_action_schedulers() -> None:
    """测试与设备重连用：丢弃队列、取消 worker。"""
    for scheduler in list(_SCHEDULERS.values()):
        _stop_scheduler(scheduler, "stopped", emit_current=False)
    _SCHEDULERS.clear()


class ActionOrchestrator:
    """把 5 个 `robot.*` 工具调用转成设备指令并等待结果。"""

    def __init__(self, link_resolver=get_device_link):
        self._resolve = link_resolver

    def _link(self, device_id: str):
        return self._resolve(device_id)

    def _scheduler(self, device_id: str, link: Any) -> _DeviceScheduler:
        return get_device_scheduler(device_id, link)

    @staticmethod
    def _coerce_args(arguments: Any) -> dict[str, Any] | None:
        if arguments is None:
            return {}
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    @staticmethod
    def _fail(error: str, message: str = "") -> dict[str, Any]:
        result: dict[str, Any] = {"ok": False, "error": error}
        if message:
            result["message"] = message
        return result

    @staticmethod
    def _ok(action_id: str, result: ActionResult) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": result.status == "done",
            "action_id": action_id,
            "status": result.status,
        }
        if result.detail:
            payload["detail"] = result.detail
        if result.status != "done":
            payload["error"] = result.detail or result.status
        return payload

    async def _submit(
        self,
        device_id: str,
        command: dict[str, Any],
        *,
        timeout_ms: int,
        override: bool = False,
        cancel_reason: str = "server_cancel",
    ) -> dict[str, Any]:
        link = self._link(device_id)
        if link is None:
            return self._fail("device_offline")
        action_id = command["action_id"]
        scheduler = self._scheduler(device_id, link)
        result = await scheduler.submit(
            command,
            timeout_ms=timeout_ms,
            override=override,
            cancel_reason=cancel_reason,
        )
        return self._ok(action_id, result)

    async def execute_tool(
        self,
        name: str,
        arguments: Any,
        device_id: str,
        turn: ActionTurnState,
    ) -> dict[str, Any]:
        if name not in ROBOT_TOOL_NAMES:
            return self._fail("unknown_tool", name)

        args = self._coerce_args(arguments)
        if args is None:
            return self._fail("invalid_arguments", "arguments 必须是 JSON 对象")

        link = self._link(device_id)
        if link is None:
            return self._fail("device_offline")

        if name == "robot.get_status":
            try:
                status = await asyncio.wait_for(
                    link.get_status(), timeout=settings.DEVICE_STATUS_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                return self._fail("timeout", "设备状态查询超时")
            except Exception:
                return self._fail("internal_error")
            return {"ok": True, "device_id": device_id, "status": status}

        if name == "robot.action":
            return await self._play(device_id, args, turn)
        if name == "robot.move_head":
            return await self._move_head(device_id, args, turn)
        if name == "robot.set_volume":
            return await self._set_volume(device_id, args, turn)
        if name == "robot.set_idle":
            return await self._set_idle(device_id, args, turn)
        return self._fail("unknown_tool", name)

    @staticmethod
    def _consume_or_fail(turn: ActionTurnState) -> dict[str, Any] | None:
        if turn.consume():
            return None
        return ActionOrchestrator._fail(
            "action_limit_exceeded", f"单轮最多 {turn.max_actions} 次动作工具调用"
        )

    async def _play(
        self, device_id: str, args: dict[str, Any], turn: ActionTurnState
    ) -> dict[str, Any]:
        name = args.get("name")
        if not isinstance(name, str) or not name:
            return self._fail("invalid_arguments", "缺少动作名 name")
        try:
            action = get_action(name)
        except KeyError:
            return self._fail("unknown_action", name)

        try:
            params = resolve_params(action, args)
        except (CatalogError, TypeError, ValueError) as exc:
            return self._fail("invalid_arguments", str(exc))

        limited = self._consume_or_fail(turn)
        if limited is not None:
            return limited

        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "action.play",
            "action_id": action_id,
            "name": action["name"],
            "intensity": params["intensity"],
            "speed": params["speed"],
            "repeat": params["repeat"],
            "source": "llm",
            "turn_id": turn.turn_id,
            "ts": _now_ms(),
        }
        timeout_ms = int(action["max_duration_ms"]) + settings.ACTION_RESULT_TIMEOUT_EXTRA_MS
        return await self._submit(
            device_id,
            command,
            timeout_ms=timeout_ms,
            cancel_reason=turn.cancel_reason,
        )

    async def _move_head(
        self, device_id: str, args: dict[str, Any], turn: ActionTurnState
    ) -> dict[str, Any]:
        pan = args.get("pan")
        tilt = args.get("tilt")
        speed = args.get("speed", 500)
        if not _is_number(pan) or not _is_number(tilt):
            return self._fail("invalid_arguments", "pan/tilt 必须是数字")
        if not (-90.0 <= float(pan) <= 90.0) or not (-20.0 <= float(tilt) <= 45.0):
            return self._fail("angle_out_of_range")
        if isinstance(speed, bool) or not isinstance(speed, int) or not (0 <= speed <= 1000):
            return self._fail("invalid_arguments", "speed 必须是 0-1000 的整数")

        limited = self._consume_or_fail(turn)
        if limited is not None:
            return limited

        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "servo.move",
            "action_id": action_id,
            "pan": float(pan),
            "tilt": float(tilt),
            "speed": speed,
            "source": "llm",
            "turn_id": turn.turn_id,
            "ts": _now_ms(),
        }
        timeout_ms = max(DEFAULT_COMMAND_TIMEOUT_MS, speed + 1000)
        return await self._submit(
            device_id,
            command,
            timeout_ms=timeout_ms,
            override=True,
            cancel_reason=turn.cancel_reason,
        )

    async def _set_volume(
        self, device_id: str, args: dict[str, Any], turn: ActionTurnState
    ) -> dict[str, Any]:
        volume = args.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or not (0 <= volume <= 100):
            return self._fail("invalid_arguments", "volume 必须是 0-100 的整数")

        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "volume.set",
            "action_id": action_id,
            "volume": volume,
            "source": "llm",
            "turn_id": turn.turn_id,
            "ts": _now_ms(),
        }
        return await self._submit(
            device_id,
            command,
            timeout_ms=DEFAULT_COMMAND_TIMEOUT_MS,
            override=True,
            cancel_reason=turn.cancel_reason,
        )

    async def _set_idle(
        self, device_id: str, args: dict[str, Any], turn: ActionTurnState
    ) -> dict[str, Any]:
        enabled = args.get("enabled")
        if not isinstance(enabled, bool):
            return self._fail("invalid_arguments", "enabled 必须是布尔值")

        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "idle.set",
            "action_id": action_id,
            "enabled": enabled,
            "source": "llm",
            "turn_id": turn.turn_id,
            "ts": _now_ms(),
        }
        return await self._submit(
            device_id,
            command,
            timeout_ms=DEFAULT_COMMAND_TIMEOUT_MS,
            cancel_reason=turn.cancel_reason,
        )

    async def stop(self, device_id: str, reason: str = "user_stop") -> dict[str, Any]:
        link = self._link(device_id)
        if link is None:
            return self._fail("device_offline")
        scheduler = self._scheduler(device_id, link)
        result = await scheduler.stop(reason)
        return {
            # 协议 4.6：设备停成功时回 action.result(failed, stopped)。
            "ok": result.status in ("done", "stopped")
            and result.detail in ("", "stopped"),
            "status": result.status,
            **({"detail": result.detail} if result.detail else {}),
        }


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))
