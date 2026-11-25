"""动作库只读接口。

Provider（App/固件构建脚本/调试工具）从这里获取与 Server 完全一致的
动作目录；实际写权限只属于 `companion/shared/actions.json`。
"""

from __future__ import annotations

from fastapi import APIRouter

from companion.shared.catalog import get_catalog

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("/catalog")
async def action_catalog():
    return get_catalog()
