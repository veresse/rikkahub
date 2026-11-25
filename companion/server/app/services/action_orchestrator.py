"""动作编排：参数校验、单轮限次、设备排队、限速与超时。

本模块只依赖 `DeviceLink` 抽象，不关心底层是 WebSocket、模拟器还是测试桩。
设备离线时立即失败，绝不缓存等待上线补发。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from companion.server.app.config import settings
from companion.server.app.services.action_tools import ROBOT_TOOL_NAMES
from companion.server.app.services.device_registry import ActionResult, get_device_link
from companion.shared.catalog import CatalogError, get_action, resolve_params

MAX_PENDING_COMMANDS = 8
MAX_COMMANDS_PER_SECOND = 10
RATE_WINDOW_SECONDS = 1.0
DEFAULT_COMMAND_TIMEOUT_MS = 3000

# 只有“真正让机器人动起来”的工具计入单轮次数；音量/待机/状态查询不计。
COUNTED_TOOLS = {"robot.action", "robot.move_head"}
# 覆盖语义：排队中同类指令只保留最后一条。
OVERRIDE_KINDS = {"volume.set", "servo.move"}


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ActionTurnState:
    turn_id: str
    max_actions: int = 3
    used_actions: int = 0

    def consume(self) -> bool:
        if self.used_actions >= self.max_actions:
            return False
        self.used_actions += 1
        return True


@dataclass
class _Job:
    command: dict[str, Any]
    future: asyncio.Future[ActionResult] = field(repr=False)
    deadline: float | None = None


class _DeviceScheduler:
    """单设备串行执行器：FIFO、队列上限、限速、覆盖与急停。"""

    def __init__(self, device_id: str, link: Any):
        self.device_id = device_id
        self.link = link
        self.queue: deque[_Job] = deque()
        self.worker: asyncio.Task | None = None
        self.current: _Job | None = None
        self._events: deque[float] = deque()

    def _allow_rate(self) -> bool:
        now = time.monotonic()
        while self._events and now - self._events[0] > RATE_WINDOW_SECONDS:
            self._events.popleft()
        if len(self._events) >= MAX_COMMANDS_PER_SECOND:
            return False
        self._events.append(now)
        return True

    def _ensure_worker(self) -> None:
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        try:
            while self.queue:
                job = self.queue.popleft()
                if job.future.done():
                    continue
                self.current = job
                try:
                    if job.deadline is not None and time.monotonic() > job.deadline:
                        if not job.future.done():
                            job.future.set_result(
                                ActionResult(status="failed", detail="timeout")
                            )
                        continue
                    try:
                        result = await self.link.send(job.command)
                    except Exception:
                        result = ActionResult(status="failed", detail="internal_error")
                    if not job.future.done():
                        job.future.set_result(result)
                finally:
                    self.current = None
        finally:
            self.current = None
            self.worker = None
            if self.queue:
                self._ensure_worker()

    async def submit(
        self, command: dict[str, Any], *, timeout_ms: int, override: bool = False
    ) -> ActionResult:
        if not self._allow_rate():
            return ActionResult(status="failed", detail="rate_limited")

        if override:
            self._drop_superseded(command.get("type"))

        if len(self.queue) >= MAX_PENDING_COMMANDS:
            return ActionResult(status="failed", detail="queue_full")

        job = _Job(command=command, future=asyncio.get_running_loop().create_future())
        self.queue.append(job)
        self._ensure_worker()

        try:
            return await asyncio.wait_for(asyncio.shield(job.future), timeout_ms / 1000)
        except asyncio.TimeoutError:
            self._abort(job)
            # 设备没在预期时间内回执，主动急停，避免舵机继续跑一个
            # 服务端已经放弃的动作。
            await self._send_stop("server_cancel")
            return ActionResult(status="timeout", detail="timeout")

    async def enqueue(
        self, command: dict[str, Any], *, timeout_ms: int, override: bool = False
    ) -> ActionResult:
        """App 遥控用：入队后立刻返回 `queued`，结果走事件广播。

        排队超过 `timeout_ms` 仍未下发的命令会被 worker 丢弃并标记
        `failed(timeout)`，遥控方通过事件流观察最终结果。
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
            deadline=time.monotonic() + timeout_ms / 1000,
        )
        self.queue.append(job)
        self._ensure_worker()
        # 让 worker 有机会在本调用返回前把命令写进通道。
        await asyncio.sleep(0)
        return ActionResult(status="queued")

    def _abort(self, job: _Job) -> None:
        if not job.future.done():
            job.future.set_result(ActionResult(status="timeout", detail="timeout"))
        try:
            self.queue.remove(job)
        except ValueError:
            pass

    def _drop_superseded(self, command_type: Any) -> None:
        """覆盖语义：同类型未下发的旧指令标记 superseded 并出队。"""
        kept: deque[_Job] = deque()
        for job in self.queue:
            if job.command.get("type") == command_type:
                if not job.future.done():
                    job.future.set_result(ActionResult(status="failed", detail="superseded"))
            else:
                kept.append(job)
        self.queue = kept

    async def _send_stop(self, reason: str) -> ActionResult:
        command = {
            "v": 2,
            "type": "action.stop",
            "reason": reason,
            "ts": _now_ms(),
        }
        try:
            return await self.link.send(command)
        except Exception:
            return ActionResult(status="failed", detail="internal_error")

    async def stop(self, reason: str) -> ActionResult:
        stopped = ActionResult(status="failed", detail="stopped")
        if self.current is not None and not self.current.future.done():
            self.current.future.set_result(stopped)
        for job in self.queue:
            if not job.future.done():
                job.future.set_result(stopped)
        self.queue.clear()
        return await self._send_stop(reason)


_SCHEDULERS: dict[str, _DeviceScheduler] = {}


def invalidate_device_scheduler(device_id: str) -> None:
    scheduler = _SCHEDULERS.pop(device_id, None)
    if scheduler and scheduler.worker and not scheduler.worker.done():
        scheduler.worker.cancel()


def get_device_scheduler(device_id: str, link: Any) -> _DeviceScheduler:
    """按设备取唯一调度器；设备链路变化时自动重建，避免向旧连接发指令。"""
    scheduler = _SCHEDULERS.get(device_id)
    if scheduler is None or scheduler.link is not link:
        scheduler = _DeviceScheduler(device_id, link)
        _SCHEDULERS[device_id] = scheduler
    return scheduler


def reset_action_schedulers() -> None:
    """测试与设备重连用：丢弃队列、取消 worker。"""
    for scheduler in list(_SCHEDULERS.values()):
        stopped = ActionResult(status="failed", detail="stopped")
        if scheduler.current is not None and not scheduler.current.future.done():
            scheduler.current.future.set_result(stopped)
        for job in scheduler.queue:
            if not job.future.done():
                job.future.set_result(stopped)
        scheduler.queue.clear()
        if scheduler.worker and not scheduler.worker.done():
            scheduler.worker.cancel()
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
    ) -> dict[str, Any]:
        link = self._link(device_id)
        if link is None:
            return self._fail("device_offline")
        action_id = command["action_id"]
        scheduler = self._scheduler(device_id, link)
        result = await scheduler.submit(command, timeout_ms=timeout_ms, override=override)
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
                status = await link.get_status()
            except Exception:
                return self._fail("internal_error")
            return {"ok": True, "device_id": device_id, "status": status}

        if name in COUNTED_TOOLS and not turn.consume():
            return self._fail(
                "action_limit_exceeded", f"单轮最多 {turn.max_actions} 次动作工具调用"
            )

        if name == "robot.action":
            return await self._play(device_id, args)
        if name == "robot.move_head":
            return await self._move_head(device_id, args)
        if name == "robot.set_volume":
            return await self._set_volume(device_id, args)
        if name == "robot.set_idle":
            return await self._set_idle(device_id, args)
        return self._fail("unknown_tool", name)

    async def _play(self, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
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
            "ts": _now_ms(),
        }
        timeout_ms = int(action["max_duration_ms"]) + settings.ACTION_RESULT_TIMEOUT_EXTRA_MS
        return await self._submit(device_id, command, timeout_ms=timeout_ms)

    async def _move_head(self, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
        pan = args.get("pan")
        tilt = args.get("tilt")
        speed = args.get("speed", 500)
        if not _is_number(pan) or not _is_number(tilt):
            return self._fail("invalid_arguments", "pan/tilt 必须是数字")
        if not (-90.0 <= float(pan) <= 90.0) or not (-20.0 <= float(tilt) <= 45.0):
            return self._fail("angle_out_of_range")
        if isinstance(speed, bool) or not isinstance(speed, int) or not (0 <= speed <= 1000):
            return self._fail("invalid_arguments", "speed 必须是 0-1000 的整数")

        action_id = str(uuid.uuid4())
        command = {
            "v": 2,
            "type": "servo.move",
            "action_id": action_id,
            "pan": float(pan),
            "tilt": float(tilt),
            "speed": speed,
            "source": "llm",
            "ts": _now_ms(),
        }
        timeout_ms = max(DEFAULT_COMMAND_TIMEOUT_MS, speed + 1000)
        return await self._submit(device_id, command, timeout_ms=timeout_ms, override=True)

    async def _set_volume(self, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
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
            "ts": _now_ms(),
        }
        return await self._submit(
            device_id, command, timeout_ms=DEFAULT_COMMAND_TIMEOUT_MS, override=True
        )

    async def _set_idle(self, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
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
            "ts": _now_ms(),
        }
        return await self._submit(device_id, command, timeout_ms=DEFAULT_COMMAND_TIMEOUT_MS)

    async def stop(self, device_id: str, reason: str = "user_stop") -> dict[str, Any]:
        link = self._link(device_id)
        if link is None:
            return self._fail("device_offline")
        scheduler = self._scheduler(device_id, link)
        result = await scheduler.stop(reason)
        return {
            "ok": result.status == "done",
            "status": result.status,
            **({"detail": result.detail} if result.detail else {}),
        }


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))
