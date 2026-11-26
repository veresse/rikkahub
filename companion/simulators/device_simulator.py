"""设备侧模拟器：实现 `protocol_v2.md` 的握手、动作执行与上报。

用途：
- pytest 端到端测试的设备桩（真实 WebSocket，不是 `FakeChannel`）；
- 无硬件时联调服务端 / App：`python -m companion.simulators.device_simulator ...`。

模拟的行为：
- v2 握手（`hello` / `hello_ack`）、v1 降级；
- `action.play` 按动作库时长/姿态执行，`action.stop` 立即取消并回中；
- `servo.move`、`volume.set`、`idle.set`、`expression.set`、校准；
- 动作完成 / 音量变化 / 校准保存后主动 `device.report`，可选周期心跳。

限制：不实现音频帧、真实舵机 PWM 与表情屏；这些必须用真机验收。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from companion.server.app.audio.framing import (
    KIND_UPLINK,
    AudioFrame,
    FrameError,
)
from companion.shared.catalog import estimate_duration_ms, get_action

DEFAULT_AUDIO = {
    "uplink": {"codec": "opus", "sample_rate": 16000, "channels": 1, "frame_ms": 60},
    "downlink": {"codec": "opus", "sample_rate": 24000, "channels": 1, "frame_ms": 60},
}

PAN_MIN, PAN_MAX = -90.0, 90.0
TILT_MIN, TILT_MAX = -20.0, 45.0
OFFSET_LIMIT = 30.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _ws_url(base_url: str, path: str) -> str:
    if base_url.startswith("https://"):
        root = "wss://" + base_url[len("https://") :]
    elif base_url.startswith("http://"):
        root = "ws://" + base_url[len("http://") :]
    else:
        root = base_url
    return root.rstrip("/") + path


class SimulatedDevice:
    """一台虚拟设备。线程/任务模型：一个读循环 + 每动作一个子任务。"""

    def __init__(
        self,
        base_url: str,
        *,
        device_id: str,
        token: str,
        protocol_version: int = 2,
        capabilities: list[str] | None = None,
        time_scale: float = 0.01,
        auto_complete: bool = True,
        report_interval: float | None = None,
        hello_timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        self.token = token
        self.protocol_version = protocol_version
        self.capabilities = (
            list(capabilities)
            if capabilities is not None
            else ["action", "volume", "expression"]
        )
        self.time_scale = max(0.0, time_scale)
        self.auto_complete = auto_complete
        self.report_interval = report_interval
        self.hello_timeout = hello_timeout

        # 虚拟硬件状态
        self.volume = 70
        self.pan = 0.0
        self.tilt = 0.0
        self.idle_enabled = True
        self.state = "idle"
        self.expression = "idle"
        self.pan_offset = 0.0
        self.tilt_offset = 0.0
        self.power = "usb"

        self.hello_ack: dict[str, Any] | None = None
        self.received: list[dict[str, Any]] = []
        self.results_sent: list[dict[str, Any]] = []
        self.downlink_frames: list[AudioFrame] = []
        self.voice_messages: list[dict[str, Any]] = []
        self.close_code: int | None = None
        self.close_reason: str = ""
        self.last_poses: dict[str, tuple[float, float]] = {}

        self.ws: Any = None
        self._receiver: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._actions: dict[str, asyncio.Task] = {}
        self._send_lock = asyncio.Lock()
        self._audio_seq = 0
        self._audio_event = asyncio.Event()
        self._closed = asyncio.Event()
        self._release = asyncio.Event()
        self._closing = False

    # ---- 生命周期 ----

    async def connect(self) -> "SimulatedDevice":
        try:
            self.ws = await websockets.connect(
                _ws_url(self.base_url, "/device/ws"),
                additional_headers={"Authorization": f"Bearer {self.token}"},
                max_size=None,
                ping_interval=None,
            )
        except Exception as exc:
            # HTTP 升级阶段被拒（如 403/404）不会建立 WS，也没有 close frame；
            # 统一折算成「握手失败」，让调用方按 hello_ack=None 处理。
            self.close_reason = f"connect_failed: {exc}"
            self._closed.set()
            return self
        await self._send(
            {
                "v": self.protocol_version,
                "type": "hello",
                "device_id": self.device_id,
                "firmware_version": "2.0.0",
                "action_catalog_version": "2.0.0",
                "capabilities": self.capabilities,
                "audio": DEFAULT_AUDIO,
                "ts": _now_ms(),
            }
        )

        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=self.hello_timeout)
        except ConnectionClosed as exc:
            self._record_close(exc)
            return self
        except asyncio.TimeoutError:
            await self.close()
            return self

        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if not isinstance(parsed, dict) or parsed.get("type") != "hello_ack":
            self.hello_ack = None
            await self.close()
            return self
        self.hello_ack = parsed

        self._receiver = asyncio.create_task(self._receive_loop())
        if self.report_interval:
            self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        await self.send_report()
        return self

    async def close(self) -> None:
        # 先断链再取消动作，模拟真机掉电/断网：在途动作没有机会补发
        # `action.result`，服务端应走 device_offline 路径。需要「优雅停止」
        # 语义的测试请显式发送 `action.stop`。
        self._closing = True
        ws = self.ws
        self.ws = None
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=2.0)
            except Exception:
                pass
        for task in list(self._actions.values()):
            task.cancel()
        if self._actions:
            await asyncio.gather(*self._actions.values(), return_exceptions=True)
            self._actions.clear()
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            await asyncio.gather(self._heartbeat, return_exceptions=True)
            self._heartbeat = None
        if self._receiver is not None:
            try:
                await asyncio.wait_for(self._receiver, timeout=2.0)
            except Exception:
                self._receiver.cancel()
                await asyncio.gather(self._receiver, return_exceptions=True)
            self._receiver = None
        self._closed.set()

    async def wait_closed(self, timeout: float | None = 5.0) -> "SimulatedDevice":
        if timeout is None:
            await self._closed.wait()
        else:
            await asyncio.wait_for(self._closed.wait(), timeout=timeout)
        return self

    # ---- 上行 ----

    async def _send(self, payload: dict[str, Any]) -> bool:
        ws = self.ws
        if ws is None:
            return False
        try:
            async with self._send_lock:
                await ws.send(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception:
            return False

    async def _send_bytes(self, data: bytes) -> bool:
        ws = self.ws
        if ws is None:
            return False
        try:
            async with self._send_lock:
                await ws.send(data)
            return True
        except Exception:
            return False

    async def send_audio(
        self, payload: bytes, *, turn_seq: int, flags: int = 0
    ) -> bool:
        frame = AudioFrame(
            kind=KIND_UPLINK,
            flags=flags,
            seq=self._audio_seq,
            turn_seq=turn_seq,
            payload=payload,
        )
        self._audio_seq = (self._audio_seq + 1) & 0xFFFFFFFF
        return await self._send_bytes(frame.encode())

    async def start_voice(self, turn_id: str) -> bool:
        self.state = "listening"
        return await self._send(
            {
                "v": 2,
                "type": "voice.start",
                "turn_id": turn_id,
                "ts": _now_ms(),
            }
        )

    async def end_voice(self, turn_id: str) -> bool:
        self.state = "thinking"
        return await self._send(
            {"v": 2, "type": "voice.end", "turn_id": turn_id, "ts": _now_ms()}
        )

    async def cancel_voice(self, turn_id: str, reason: str = "user_stop") -> bool:
        self.state = "idle"
        return await self._send(
            {
                "v": 2,
                "type": "voice.cancel",
                "turn_id": turn_id,
                "reason": reason,
                "ts": _now_ms(),
            }
        )

    async def segment_played(self, turn_id: str, segment_seq: int) -> bool:
        return await self._send(
            {
                "v": 2,
                "type": "voice.segment_played",
                "turn_id": turn_id,
                "segment_seq": segment_seq,
                "ts": _now_ms(),
            }
        )

    async def wait_for_audio(self, predicate=None, *, timeout: float = 5.0) -> AudioFrame:
        deadline = time.monotonic() + timeout
        while True:
            for frame in self.downlink_frames:
                if predicate is None or predicate(frame):
                    return frame
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError("等待下行音频帧超时")
            await asyncio.sleep(0.01)

    async def send_report(self) -> bool:
        if self.protocol_version < 2:
            # v1 设备只走音频通道，不发送 v2 控制面状态帧。
            return False
        return await self._send(
            {
                "v": self.protocol_version,
                "type": "device.report",
                "volume": int(self.volume),
                "pan": float(self.pan),
                "tilt": float(self.tilt),
                "idle": bool(self.idle_enabled),
                "power": self.power,
                "state": self.state,
                "pan_offset": float(self.pan_offset),
                "tilt_offset": float(self.tilt_offset),
                "ts": _now_ms(),
            }
        )

    async def _send_result(self, action_id: str, status: str, detail: str = "") -> None:
        payload = {
            "v": self.protocol_version,
            "type": "action.result",
            "action_id": action_id,
            "status": status,
            "detail": detail,
            "ts": _now_ms(),
        }
        if await self._send(payload):
            self.results_sent.append(payload)

    # ---- 测试辅助 ----

    def commands_of_type(self, message_type: str) -> list[dict[str, Any]]:
        return [m for m in self.received if m.get("type") == message_type]

    def last_pose_for(self, action_name: str) -> tuple[float, float]:
        return self.last_poses.get(action_name, (self.pan, self.tilt))

    def release(self) -> None:
        """`auto_complete=False` 时放行挂起的动作。"""
        self._release.set()

    async def wait_for(self, predicate, *, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for message in self.received:
                if predicate(message):
                    return message
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError("等待设备消息超时")
            await asyncio.sleep(0.01)

    # ---- 读循环与消息分发 ----

    def _record_close(self, exc: ConnectionClosed) -> None:
        if exc.rcvd is not None:
            self.close_code = exc.rcvd.code
            self.close_reason = exc.rcvd.reason
        self._closed.set()

    async def _receive_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                if isinstance(raw, (bytes, bytearray)):
                    try:
                        frame = AudioFrame.parse(bytes(raw))
                    except FrameError:
                        continue
                    self.downlink_frames.append(frame)
                    self._audio_event.set()
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                self.received.append(message)
                await self._dispatch(message)
        except ConnectionClosed as exc:
            self._record_close(exc)
        finally:
            self._closed.set()

    async def _dispatch(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if self._closing:
            # 已开始关闭：不再派发新命令，避免在取消动作之后又拉起孤儿任务。
            return

        if message_type == "action.stop":
            await self._handle_stop(message)
            return
        if message_type == "servo.calibrate_preview":
            self._apply_offsets(message)
            await self.send_report()
            return
        if message_type == "expression.set":
            name = message.get("name")
            if isinstance(name, str) and name:
                self.expression = name
            await self.send_report()
            # §4.4 是 fire-and-forget（无 action_id）；但若服务端/固件变体带了
            # action_id，必须回执，否则调用方会一直等到超时。
            action_id = message.get("action_id")
            if isinstance(action_id, str) and action_id:
                await self._send_result(action_id, "done", "")
            return

        if message_type in {"voice.segment", "voice.cancel_ack", "voice.error"}:
            self.voice_messages.append(message)
            if message_type == "voice.segment":
                self.state = "speaking"
            elif message_type in {"voice.cancel_ack", "voice.error"}:
                self.state = "idle"
            return

        action_id = message.get("action_id")
        if not isinstance(action_id, str) or not action_id:
            return
        task = asyncio.create_task(self._run_action(message))
        self._actions[action_id] = task
        task.add_done_callback(lambda t, aid=action_id: self._actions.pop(aid, None))

    async def _handle_stop(self, message: dict[str, Any]) -> None:
        self.state = "idle"
        self.expression = "idle"
        self._home()
        tasks = list(self._actions.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.send_report()

    def _home(self) -> None:
        self.pan = 0.0
        self.tilt = 0.0

    def _apply_offsets(self, message: dict[str, Any]) -> None:
        try:
            self.pan_offset = _clamp(
                float(message.get("pan_offset", 0.0)), -OFFSET_LIMIT, OFFSET_LIMIT
            )
            self.tilt_offset = _clamp(
                float(message.get("tilt_offset", 0.0)), -OFFSET_LIMIT, OFFSET_LIMIT
            )
        except (TypeError, ValueError):
            pass

    # ---- 命令执行 ----

    async def _run_action(self, message: dict[str, Any]) -> None:
        action_id = message["action_id"]
        try:
            status, detail = await self._execute(message)
        except asyncio.CancelledError:
            self._home()
            self.state = "idle"
            await self._send_result(action_id, "failed", "stopped")
            await self.send_report()
            raise
        await self._send_result(action_id, status, detail)
        await self.send_report()

    async def _execute(self, message: dict[str, Any]) -> tuple[str, str]:
        message_type = message.get("type")
        if message_type == "action.play":
            return await self._execute_action(message)
        if message_type == "servo.move":
            return await self._execute_servo(message)
        if message_type == "volume.set":
            return await self._execute_volume(message)
        if message_type == "idle.set":
            return await self._execute_idle(message)
        if message_type == "servo.calibrate_save":
            self._apply_offsets(message)
            return "done", ""
        return "failed", "unknown_action"

    async def _execute_action(self, message: dict[str, Any]) -> tuple[str, str]:
        name = message.get("name")
        try:
            action = get_action(str(name))
        except KeyError:
            return "failed", "unknown_action"

        supports = action.get("supports") or {}
        intensity = message.get("intensity", 2)
        speed = message.get("speed", 2)
        repeat = message.get("repeat", 1)
        if not supports.get("intensity"):
            intensity = 2
        if not supports.get("speed"):
            speed = 2
        if not supports.get("repeat"):
            repeat = 1

        duration_ms = estimate_duration_ms(
            action, {"intensity": intensity, "speed": speed, "repeat": repeat}
        )
        await self._wait(duration_ms)

        scale = 1.0
        if supports.get("intensity"):
            try:
                scale = float(action["intensity_scale"][int(intensity) - 1])
            except (KeyError, IndexError, TypeError, ValueError):
                scale = 1.0
        last = action["keyframes"][-1]
        self.pan = _clamp(float(last.get("pan", 0.0)) * scale, PAN_MIN, PAN_MAX)
        self.tilt = _clamp(float(last.get("tilt", 0.0)) * scale, TILT_MIN, TILT_MAX)
        linked = action.get("linked_expression")
        if isinstance(linked, str) and linked:
            self.expression = linked
        self.state = "idle"
        self.last_poses[action["name"]] = (self.pan, self.tilt)
        return "done", ""

    async def _execute_servo(self, message: dict[str, Any]) -> tuple[str, str]:
        try:
            pan = float(message.get("pan"))
            tilt = float(message.get("tilt"))
            speed = int(message.get("speed", 500))
        except (TypeError, ValueError):
            return "failed", "invalid_arguments"
        if not (PAN_MIN <= pan <= PAN_MAX and TILT_MIN <= tilt <= TILT_MAX):
            return "failed", "angle_out_of_range"
        await self._wait(speed)
        self.pan = pan
        self.tilt = tilt
        self.state = "idle"
        return "done", ""

    async def _execute_volume(self, message: dict[str, Any]) -> tuple[str, str]:
        volume = message.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or not (0 <= volume <= 100):
            return "failed", "invalid_arguments"
        self.volume = volume
        return "done", ""

    async def _execute_idle(self, message: dict[str, Any]) -> tuple[str, str]:
        enabled = message.get("enabled")
        if not isinstance(enabled, bool):
            return "failed", "invalid_arguments"
        if self.state == "idle":
            self.idle_enabled = enabled
        return "done", ""

    async def _wait(self, duration_ms: float) -> None:
        if not self.auto_complete:
            await self._release.wait()
            return
        seconds = max(0.0, float(duration_ms) / 1000.0 * self.time_scale)
        await asyncio.sleep(seconds)

    async def _heartbeat_loop(self) -> None:
        assert self.report_interval
        try:
            while True:
                await asyncio.sleep(self.report_interval)
                await self.send_report()
        except asyncio.CancelledError:
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="设备侧协议模拟器")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--token", default=os.environ.get("DEVICE_TOKEN", ""))
    parser.add_argument("--protocol-version", type=int, default=2)
    parser.add_argument("--time-scale", type=float, default=1.0)
    parser.add_argument("--report-interval", type=float, default=30.0)
    args = parser.parse_args()

    if not args.token:
        parser.error("必须提供 --token 或 DEVICE_TOKEN 环境变量")

    async def run() -> int:
        device = SimulatedDevice(
            args.base_url,
            device_id=args.device_id,
            token=args.token,
            protocol_version=args.protocol_version,
            time_scale=args.time_scale,
            report_interval=args.report_interval,
        )
        await device.connect()
        if device.hello_ack is None:
            print(
                "device handshake failed: "
                f"close_code={device.close_code} reason={device.close_reason}",
                file=sys.stderr,
            )
            return 1
        print(
            "device ws connected, "
            f"hello_ack={json.dumps(device.hello_ack, ensure_ascii=False)}"
        )
        try:
            await device.wait_closed(timeout=None)
        finally:
            await device.close()
        return 0

    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
