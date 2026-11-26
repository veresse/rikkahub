"""在线设备链路注册表。

Block C 只定义协议与内存实现；Block D 的 `DeviceGateway` 在设备连接/断开时
调用 `register_device_link` / `unregister_device_link` 即可接入。
"""

from __future__ import annotations

import asyncio
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
# 每设备生命周期锁：`DeviceGateway._install()`（复核活跃状态 + 注册链路）与
# 撤销路由（查链路 + 关闭）必须互斥。否则撤销恰好发生在「已鉴权、还没 hello」
# 的窗口时，路由查不到链路就直接返回，随后该连接在撤销后重新注册并可控。
# 锁放在注册表而不是网关实例上，保证同进程内多个网关/路由看到同一把锁。
_LIFECYCLE_LOCKS: dict[str, asyncio.Lock] = {}


def device_lifecycle_lock(device_id: str) -> asyncio.Lock:
    lock = _LIFECYCLE_LOCKS.get(device_id)
    if lock is None:
        lock = asyncio.Lock()
        _LIFECYCLE_LOCKS[device_id] = lock
    return lock


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
    _LIFECYCLE_LOCKS.clear()


async def resolve_device_for_conversation(db: AsyncSession, conversation_id: str):
    """找会话绑定的设备；没有会话级绑定时回退到角色级绑定。

    解析顺序固定为：**未撤销会话级 → 未撤销角色级 → 已撤销会话级 → 已撤销角色级**。
    `is_active` 只表示绑定未撤销，是否在线由调用方通过 `get_device_link()` 判定。
    后两步返回已撤销设备，仅用于让调用方区分「从没绑定过（unbound）」和
    「绑定过但已被撤销（inactive）」。角色级撤销回退不会遮蔽前面的可用设备。
    """
    from companion.server.app.models.conversation import ConversationModel
    from companion.server.app.models.device import DeviceModel

    conversation = await db.get(ConversationModel, conversation_id)
    if conversation is None:
        return None

    async def _first(*conditions):
        result = await db.execute(
            select(DeviceModel)
            .where(*conditions)
            .order_by(DeviceModel.last_seen_at.desc().nullslast())
        )
        return result.scalars().first()

    device = await _first(
        DeviceModel.bound_conversation_id == conversation_id,
        DeviceModel.is_active.is_(True),
    )
    if device is not None:
        return device

    device = await _first(
        DeviceModel.bound_conversation_id.is_(None),
        DeviceModel.bound_assistant_id == conversation.assistant_id,
        DeviceModel.is_active.is_(True),
    )
    if device is not None:
        return device

    device = await _first(
        DeviceModel.bound_conversation_id == conversation_id,
        DeviceModel.is_active.is_(False),
    )
    if device is not None:
        return device

    return await _first(
        DeviceModel.bound_conversation_id.is_(None),
        DeviceModel.bound_assistant_id == conversation.assistant_id,
        DeviceModel.is_active.is_(False),
    )
