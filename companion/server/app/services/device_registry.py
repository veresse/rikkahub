"""在线设备链路注册表。

Block C 只定义协议与内存实现；Block D 的 `DeviceGateway` 在设备连接/断开时
调用 `register_device_link` / `unregister_device_link` 即可接入。
"""

from __future__ import annotations

import asyncio
import weakref
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

    # 语音管线用：控制消息与音频帧必须走同一条按序链路。
    async def send_control(self, payload: dict[str, Any]) -> bool: ...

    async def send_segment(
        self,
        segment: dict[str, Any],
        first_payload: bytes | None,
        *,
        turn_seq: int,
        flags: int = 0,
    ) -> bool: ...

    async def send_audio(
        self, payload: bytes, *, turn_seq: int, flags: int = 0
    ) -> bool: ...


_LINKS: dict[str, DeviceLink] = {}
# 每设备生命周期锁：`DeviceGateway._install()`（复核活跃状态 + 注册链路）与
# 撤销路由（查链路 + 关闭）必须互斥。否则撤销恰好发生在「已鉴权、还没 hello」
# 的窗口时，路由查不到链路就直接返回，随后该连接在撤销后重新注册并可控。
# 锁放在注册表而不是网关实例上，保证同进程内多个网关/路由看到同一把锁。
#
# 按事件循环分表：锁只在同一条循环内的任务之间互斥，而测试（以及将来的多循环
# 场景）会在不同循环里复用同一批 device_id。`asyncio.Lock` 被竞争后会绑定到
# 首次使用的循环，跨循环复用会抛 RuntimeError；用弱引用键让循环结束后整表回收。
_LIFECYCLE_LOCKS: "weakref.WeakKeyDictionary[Any, dict[str, asyncio.Lock]]" = (
    weakref.WeakKeyDictionary()
)


def device_lifecycle_lock(device_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    # 被竞争过的 `asyncio.Lock` 会强引用其循环，而 value（dict）又强引用 lock，
    # 形成「value → key(loop)」环，WeakKeyDictionary 无法自动回收。这里顺手剔除
    # 已关闭的循环，避免测试/多循环场景下残留。
    stale = [
        lp for lp in list(_LIFECYCLE_LOCKS) if lp is not loop and lp.is_closed()
    ]
    for closed_loop in stale:
        del _LIFECYCLE_LOCKS[closed_loop]
    locks = _LIFECYCLE_LOCKS.get(loop)
    if locks is None:
        locks = {}
        _LIFECYCLE_LOCKS[loop] = locks
    lock = locks.get(device_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[device_id] = lock
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


def device_link_is_online(link: DeviceLink | None) -> bool:
    """链路是否可下发。

    与 `DeviceSession.send()` 保持一致：必须存在、已完成握手（`ready`）且
    没有在收尾（`closing`/`closed`）。旧实现只看「注册表里有这个 id」，
    于是 hello_ack 之前、以及写失败后的收尾阶段都会被当成「在线但不可控」：
    LLM 仍会注入 `robot.*` 工具，每次调用都返回 `device_offline`。
    """
    return (
        link is not None
        and getattr(link, "ready", True)
        and not getattr(link, "closing", False)
        and not getattr(link, "closed", False)
    )


def reset_device_links() -> None:
    # 只清链路；生命周期锁按循环分表并由弱引用回收，不能在任务仍持有旧锁时丢弃，
    # 否则会为同一设备生成第二把锁、破坏撤销与安装的互斥。
    _LINKS.clear()


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
