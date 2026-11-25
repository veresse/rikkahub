"""把 DeepSeek 流式 `tool_calls` 增量聚合为完整调用列表。

OpenAI/DeepSeek 的流式 tool call 以 `index` 区分多个调用，`id`/`name` 通常只出现
一次，`arguments` 会被拆成多个片段。这里按 index 合并并在结束时按 index 排序。
"""

from __future__ import annotations

from typing import Any


class ToolCallAccumulator:
    def __init__(self) -> None:
        self._parts: dict[int, dict[str, str]] = {}

    def add(self, deltas: list[dict[str, Any]]) -> None:
        for delta in deltas or []:
            if not isinstance(delta, dict):
                continue
            try:
                index = int(delta.get("index", 0))
            except (TypeError, ValueError):
                index = 0
            part = self._parts.setdefault(
                index, {"id": "", "type": "function", "name": "", "arguments": ""}
            )
            if delta.get("id"):
                part["id"] = str(delta["id"])
            if delta.get("type"):
                part["type"] = str(delta["type"])
            function = delta.get("function") or {}
            name = function.get("name")
            if name:
                name = str(name)
                # 多数 Provider 只在首个增量里给完整 name；个别会重复整帧，
                # 重复时直接跳过，避免拼成 "robot.actionrobot.action"。
                if part["name"] != name:
                    part["name"] += name
            if function.get("arguments"):
                part["arguments"] += str(function["arguments"])

    def build(self) -> list[dict[str, Any]]:
        return [
            {
                "id": self._parts[index]["id"],
                "type": self._parts[index]["type"],
                "function": {
                    "name": self._parts[index]["name"],
                    "arguments": self._parts[index]["arguments"],
                },
            }
            for index in sorted(self._parts)
        ]
