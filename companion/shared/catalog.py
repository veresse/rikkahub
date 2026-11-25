"""动作库加载、校验与派生工具。

`actions.json` 是 Server / Android App / ESP32 固件三端共享的唯一事实源。
本模块只做纯 Python 的读取与结构性校验，不依赖 FastAPI 或数据库，
方便在服务启动、测试与构建期脚本中复用。
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent / "actions.json"
SCHEMA_PATH = Path(__file__).resolve().parent / "actions.schema.json"

EXPECTED_ACTIONS = {
    "home",
    "nod",
    "shake",
    "look_left",
    "look_right",
    "look_up",
    "look_down",
    "look_around",
    "bow",
    "peek",
    "surprised",
    "sleep",
    "wake",
    "panic",
    "swing",
    "tilt_left",
    "tilt_right",
    "dance",
}

# 仅这些动作允许超过 default_max_duration_ms，且不得超过 hard_max_duration_ms。
EXTENDED_DURATION_WHITELIST = {"dance"}

VALID_EASES = {"linear", "ease_in", "ease_out", "ease_in_out"}


class CatalogError(ValueError):
    """动作库结构或语义不合法。"""


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    target = path or CATALOG_PATH
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def get_catalog() -> dict[str, Any]:
    catalog = load_catalog()
    errors = validate_catalog(catalog)
    if errors:
        raise CatalogError("; ".join(errors))
    return catalog


def get_action(name: str) -> dict[str, Any]:
    for action in get_catalog()["actions"]:
        if action["name"] == name:
            return action
    raise KeyError(f"未知动作: {name}")


def get_action_names() -> list[str]:
    return [a["name"] for a in get_catalog()["actions"]]


def validate_catalog(catalog: dict[str, Any]) -> list[str]:
    """返回错误列表；空列表表示通过。"""
    errors: list[str] = []
    limits = catalog.get("limits") or {}
    actions = catalog.get("actions") or []

    if not isinstance(actions, list):
        return ["actions 必须是数组"]

    names = [a.get("name") for a in actions if isinstance(a, dict)]
    if len(names) != len(set(names)):
        errors.append("动作名存在重复")
    missing = EXPECTED_ACTIONS - set(names)
    extra = set(names) - EXPECTED_ACTIONS
    if missing:
        errors.append(f"缺少动作: {sorted(missing)}")
    if extra:
        errors.append(f"未知动作: {sorted(extra)}")

    pan_limit = limits.get("pan") or {}
    tilt_limit = limits.get("tilt") or {}
    default_max = limits.get("default_max_duration_ms", 3000)
    hard_max = limits.get("hard_max_duration_ms", 5000)
    repeat_max = (limits.get("repeat") or {}).get("max", 5)
    intensity_levels = (limits.get("intensity") or {}).get("levels", [1, 2, 3])
    speed_levels = (limits.get("speed") or {}).get("levels", [1, 2, 3])
    expressions = catalog.get("expressions") or {}
    categories = catalog.get("categories") or {}

    for action in actions:
        if not isinstance(action, dict):
            errors.append("actions 中存在非对象元素")
            continue
        name = action.get("name", "<unnamed>")

        def err(msg: str) -> None:
            errors.append(f"[{name}] {msg}")

        if action.get("category") not in categories:
            err(f"category 未在 categories 中定义: {action.get('category')!r}")

        linked = action.get("linked_expression")
        if linked is not None and linked not in expressions:
            err(f"linked_expression 未在 expressions 中定义: {linked!r}")

        axes = action.get("axes") or []
        if len(set(axes)) != len(axes):
            err("axes 存在重复")
        if not axes or any(a not in ("pan", "tilt") for a in axes):
            err(f"axes 非法: {axes!r}")

        scale = action.get("intensity_scale") or []
        if len(scale) != 3:
            err("intensity_scale 必须正好 3 个元素")
        else:
            if any((not isinstance(v, (int, float))) or v <= 0 or v > 1.0 for v in scale):
                err("intensity_scale 元素必须在 (0, 1] 内")
            if any(scale[i] > scale[i + 1] for i in range(2)):
                err("intensity_scale 必须非递减")
            if not math.isclose(scale[-1], 1.0, abs_tol=1e-9):
                err("intensity_scale 第三档必须为 1.0（满行程）")

        supports = action.get("supports") or {}
        defaults = action.get("default_params") or {}
        if defaults:
            if defaults.get("intensity") not in intensity_levels:
                err(f"default intensity 非法: {defaults.get('intensity')!r}")
            if defaults.get("speed") not in speed_levels:
                err(f"default speed 非法: {defaults.get('speed')!r}")
            repeat = defaults.get("repeat")
            if not isinstance(repeat, int) or not (1 <= repeat <= repeat_max):
                err(f"default repeat 非法: {repeat!r}")
            if supports.get("intensity") is False and defaults.get("intensity") != 2:
                err("不支持 intensity 的动作，默认 intensity 应为 2")
            if supports.get("repeat") is False and repeat != 1:
                err("不支持 repeat 的动作，默认 repeat 应为 1")

        keyframes = action.get("keyframes") or []
        if not keyframes:
            err("keyframes 不能为空")
        else:
            prev_at = -1
            for kf in keyframes:
                at = kf.get("at_ms")
                if not isinstance(at, int) or at < 0:
                    err(f"keyframe.at_ms 非法: {at!r}")
                    continue
                if at < prev_at:
                    err("keyframes 必须按 at_ms 升序")
                prev_at = at
                if kf.get("ease") is not None and kf.get("ease") not in VALID_EASES:
                    err(f"keyframe.ease 非法: {kf.get('ease')!r}")
                pan = kf.get("pan", 0.0)
                tilt = kf.get("tilt", 0.0)
                if not isinstance(pan, (int, float)) or not (
                    pan_limit.get("min", -90) <= pan <= pan_limit.get("max", 90)
                ):
                    err(f"keyframe.pan 超出安全范围: {pan!r}")
                if not isinstance(tilt, (int, float)) or not (
                    tilt_limit.get("min", -20) <= tilt <= tilt_limit.get("max", 45)
                ):
                    err(f"keyframe.tilt 超出安全范围: {tilt!r}")

            default_duration = _estimate_duration_ms(action, defaults, limits)
            max_duration = action.get("max_duration_ms")
            if not isinstance(max_duration, int) or max_duration <= 0:
                err("max_duration_ms 非法")
            elif max_duration > hard_max:
                err(f"max_duration_ms 超过固件硬顶 {hard_max}")
            elif max_duration > default_max and name not in EXTENDED_DURATION_WHITELIST:
                err(
                    f"max_duration_ms 超过默认上限 {default_max}，"
                    f"仅白名单动作 {sorted(EXTENDED_DURATION_WHITELIST)} 可放宽"
                )
            elif default_duration > max_duration:
                err(
                    f"默认参数下预计耗时 {default_duration}ms 超过 max_duration_ms {max_duration}ms"
                )

    return errors


def _estimate_duration_ms(
    action: dict[str, Any], params: dict[str, Any] | None, limits: dict[str, Any]
) -> int:
    speed_limits = limits["speed"]
    authored = int(speed_limits.get("authored_at_level", 2))
    params = params or action.get("default_params") or {}
    speed = int(params.get("speed", 2))
    repeat = int(params.get("repeat", 1))
    keyframes = action.get("keyframes") or []
    base_ms = max((kf.get("at_ms", 0) for kf in keyframes), default=0)
    if base_ms <= 0:
        return 0
    ratio = speed_limits["one_way_ms"][str(speed)] / speed_limits["one_way_ms"][str(authored)]
    return int(round(base_ms * ratio * repeat))


def estimate_duration_ms(action: dict[str, Any], params: dict[str, Any] | None = None) -> int:
    """按动作表中以 speed 档位 2 编排的 at_ms 估算实际时长。"""
    return _estimate_duration_ms(action, params, get_catalog()["limits"])


def resolve_params(action: dict[str, Any], params: dict[str, Any] | None) -> dict[str, int]:
    """把模型/App 传入参数与动作默认值合并，并做边界与能力校验。

    动作不支持的参数按协议直接忽略并使用默认值（而不是报错）；
    显式 `None` 视为缺省。
    """
    limits = get_catalog()["limits"]
    defaults = action["default_params"]
    supports = action["supports"]
    params = params or {}

    def int_param(key: str) -> int:
        value = params.get(key)
        if value is None or supports.get(key, True) is False:
            return int(defaults[key])
        if isinstance(value, bool) or not isinstance(value, int):
            raise CatalogError(f"{key} 必须是整数，收到 {value!r}")
        return value

    intensity = int_param("intensity")
    speed = int_param("speed")
    repeat = int_param("repeat")

    if intensity not in limits["intensity"]["levels"]:
        raise CatalogError(f"intensity 必须为 1/2/3，收到 {intensity}")
    if speed not in limits["speed"]["levels"]:
        raise CatalogError(f"speed 必须为 1/2/3，收到 {speed}")
    if not (limits["repeat"]["min"] <= repeat <= limits["repeat"]["max"]):
        raise CatalogError(
            f"repeat 必须在 {limits['repeat']['min']}..{limits['repeat']['max']}，收到 {repeat}"
        )

    return {"intensity": intensity, "speed": speed, "repeat": repeat}


def build_llm_action_menu() -> str:
    """生成注入 DeepSeek 工具 description 的动作清单文本。"""
    catalog = get_catalog()
    lines = []
    for action in catalog["actions"]:
        caps = []
        if action["supports"]["intensity"]:
            caps.append("intensity 1/2/3")
        if action["supports"]["speed"]:
            caps.append("speed 1/2/3")
        if action["supports"]["repeat"]:
            caps.append("repeat 1-5")
        cap_text = f"（支持 {'、'.join(caps)}）" if caps else "（无档位参数）"
        lines.append(
            f"- {action['name']}（{action['label_zh']}）：{action['llm_description_zh']}{cap_text}"
        )
    return "\n".join(lines)
