"""DeepSeek 机器人工具 Schema 与动态注入条件。"""

from __future__ import annotations

from typing import Any

from companion.shared.catalog import build_llm_action_menu, get_action_names

ROBOT_TOOL_NAMES = {
    "robot.action",
    "robot.move_head",
    "robot.set_volume",
    "robot.get_status",
    "robot.set_idle",
}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def build_robot_tools() -> list[dict[str, Any]]:
    """生成注入 DeepSeek 的 5 个工具定义（参数范围与 protocol_v2 一致）。"""
    action_names = get_action_names()
    action_menu = build_llm_action_menu()

    return [
        {
            "type": "function",
            "function": {
                "name": "robot.action",
                "description": (
                    "控制实体桌面机器人播放预设动作。动作允许与说话并行，不需要等待动作完成再回复。\n"
                    "可用动作：\n"
                    f"{action_menu}\n"
                    "档位语义：intensity 1=轻、2=中、3=用力；speed 1=慢、2=中、3=快；repeat 为重复次数。\n"
                    "单轮最多调用 3 次动作工具，超出会被拒绝。用户只是普通问候时不要调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": action_names,
                            "description": "动作名，必须来自给定枚举。",
                        },
                        "intensity": {
                            "type": "integer",
                            "enum": [1, 2, 3],
                            "default": 2,
                            "description": "1=轻，2=中，3=用力。动作不支持时忽略。",
                        },
                        "speed": {
                            "type": "integer",
                            "enum": [1, 2, 3],
                            "default": 2,
                            "description": "1=慢，2=中，3=快。",
                        },
                        "repeat": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                            "default": 1,
                            "description": "重复次数 1-5。",
                        },
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "robot.move_head",
                "description": (
                    "把机器人头部移动到连续角度。pan 为水平角（-90 左 ~ +90 右），"
                    "tilt 为俯仰角（-20 抬头 ~ +45 低头），speed 为到位时间毫秒（0 表示立即）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pan": {"type": "number", "minimum": -90, "maximum": 90},
                        "tilt": {"type": "number", "minimum": -20, "maximum": 45},
                        "speed": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1000,
                            "default": 500,
                        },
                    },
                    "required": ["pan", "tilt"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "robot.set_volume",
                "description": (
                    "设置扬声器音量 0-100。0 为静音，但不会影响麦克风采集与对话状态机。"
                    "用户说“声音小一点/大一点/静音”时使用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "volume": {"type": "integer", "minimum": 0, "maximum": 100}
                    },
                    "required": ["volume"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "robot.get_status",
                "description": "读取机器人当前音量、舵机角度、在线状态与待机开关。只读，不计入动作次数。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "robot.set_idle",
                "description": "开关待机随机小动作。仅在设备空闲时生效。",
                "parameters": {
                    "type": "object",
                    "properties": {"enabled": {"type": "boolean"}},
                    "required": ["enabled"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def should_inject_robot_tools(assistant: Any, device: Any, *, online: bool) -> bool:
    """只有「角色开启动作联动 + 设备在线 + v2 协议 + 支持动作能力」才注入。"""
    if assistant is None or not bool(_get(assistant, "enable_action_sync", False)):
        return False
    if not online or device is None:
        return False
    if int(_get(device, "protocol_version", 1) or 1) < 2:
        return False
    capabilities = _get(device, "capabilities_json", None) or []
    return "action" in capabilities


def offline_note(bound: bool = True) -> str:
    """设备不可用时注入的系统提示，避免模型声称自己做了动作。

    `bound=True` 表示角色/会话绑定过设备但当前不在线；`bound=False` 表示
    从未绑定设备。两种情况都不注入 `robot.*` 工具，只给一句轻量提示。
    """
    if bound:
        return (
            "【设备状态】当前实体机器人离线，无法执行动作、调节音量或读取机器人状态。"
            "如果用户要求做动作，请礼貌说明设备当前离线；仍可正常进行文字对话。"
        )
    return (
        "【设备状态】当前没有绑定实体机器人，无法执行任何实体动作、调节音量或读取机器人状态。"
        "如果用户要求做动作，请礼貌说明还没有绑定设备；仍可正常进行文字对话。"
    )
