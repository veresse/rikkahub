"""在线设备链路注册表。

Block C 只定义协议与内存实现；Block D 的 `DeviceGateway` 在设备连接/断开时
调用 `register_device_link` / `unregister_device_link` 即可接入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ActionResult:
    status: str
    detail: str = ""
    action_id: str = ""


class DeviceLink(Protocol):
    """一台在线设备的发送通道（由 DeviceGateway 实现）。"""

    async def send(self, command: dict[str, Any]) -> ActionResult: ...

    async def get_status(self) -> dict[str, Any]: ...


_LINKS: dict[str, DeviceLink] = {}


def register_device_link(device_id: str, link: DeviceLink) -> None:
    _LINKS[device_id] = link


def unregister_device_link(device_id: str, link: DeviceLink | None = None) -> None:
    """注销设备链路。

    `link` 非空时必须与当前链路是同一对象才注销，避免旧连接在新连接替换后
    把新会话误删。
    """
    if link is not None and _LINKS.get(device_id) is not link:
        return
    _LINKS.pop(device_id, None)


def get_device_link(device_id: str) -> DeviceLink | None:
    return _LINKS.get(device_id)


def is_device_online(device_id: str) -> bool:
    return device_id in _LINKS


def reset_device_links() -> None:
    _LINKS.clear()


async def resolve_device_for_conversation(db: AsyncSession, conversation_id: str):
    """找会话绑定的设备；没有会话级绑定时回退到角色级绑定。

    返回最近绑定/在线的设备，包括**已撤销**的设备：调用方需要它来区分
    “从没绑定过（unbound）”和“绑定过但已被撤销（inactive）”。是否可用由
    `robot_tool_injection()` 判定；未激活的设备永远拿不到 `DeviceLink`。
    """
    from companion.server.app.models.conversation import ConversationModel
    from companion.server.app.models.device import DeviceModel

    conversation = await db.get(ConversationModel, conversation_id)
    if conversation is None:
        return None

    result = await db.execute(
        select(DeviceModel)
        .where(DeviceModel.bound_conversation_id == conversation_id)
        .order_by(
            DeviceModel.is_active.desc(),
            DeviceModel.last_seen_at.desc().nullslast(),
        )
    )
    device = result.scalars().first()
    if device is not None:
        return device

    result = await db.execute(
        select(DeviceModel)
        .where(
            DeviceModel.bound_conversation_id.is_(None),
            DeviceModel.bound_assistant_id == conversation.assistant_id,
        )
        .order_by(
            DeviceModel.is_active.desc(),
            DeviceModel.last_seen_at.desc().nullslast(),
        )
    )
    return result.scalars().first()
