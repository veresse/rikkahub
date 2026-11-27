"""4D：服务端运行时可改设置。

所有端点都需要 App Token（在 `main.py` 统一注册时加 `app_protected`）。
密钥**只写不回显**：响应永远是 `AppSettingsService.view` 的脱敏视图。
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from companion.server.app.config import settings
from companion.server.app.database import get_db
from companion.server.app.services.app_settings import (
    SECRET_KEYS,
    AppSettingsService,
)

router = APIRouter(prefix="/settings", tags=["settings"])

_VOICE_PROVIDERS = {"unavailable", "dashscope"}

_MAX_TEXT = {
    "deepseek_model": 80,
    "dashscope_region": 32,
    "dashscope_asr_model": 80,
    "dashscope_tts_model": 80,
    "dashscope_tts_voice": 80,
}

_REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
# 允许的密钥字符：可打印 ASCII 且不含空白/控制字符。真实 Key 只含
# 字母数字与 `-_.`；拒绝换行等字符，避免它们进入 Authorization 头/异常文案。
_SECRET_RE = re.compile(r"^[\x21-\x7E]+$")

_MAX_SECRET_LENGTH = 512

# 只有这些字段会改变已连接设备的语音运行时。搜索开关、DeepSeek 模型/Key
# 等普通设置不能连带重建运行时——那会关闭正在进行的语音会话。
_VOICE_RUNTIME_FIELDS = (
    "VOICE_ENABLED",
    "VOICE_PROVIDER",
    "VOICE_IDLE_TIMEOUT_SECONDS",
    "VOICE_MAX_TURN_SECONDS",
    "VOICE_ASR_FINAL_GRACE_SECONDS",
    "VOICE_TTS_FIRST_PACKET_TIMEOUT_SECONDS",
    "VOICE_SEGMENT_IDLE_TIMEOUT_SECONDS",
    "VOICE_PLAYBACK_DONE_TIMEOUT_SECONDS",
    "VOICE_DOWNLINK_BUFFER_SECONDS",
    "VOICE_DOWNLINK_FRAME_MS",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_REGION",
    "DASHSCOPE_ASR_MODEL",
    "DASHSCOPE_TTS_MODEL",
    "DASHSCOPE_TTS_VOICE",
    "DASHSCOPE_TTS_CONTAINER",
    "DASHSCOPE_WORKSPACE_ID",
    "DASHSCOPE_ASR_WS_URL",
    "DASHSCOPE_TTS_HTTP_URL",
)


def _voice_fingerprint() -> tuple[tuple[str, Any], ...]:
    """当前语音运行时的配置快照（只在内存里比较，不写日志/响应）。"""
    return tuple(
        (field, getattr(settings, field, None)) for field in _VOICE_RUNTIME_FIELDS
    )


class SettingsUpdateBody(BaseModel):
    # 防止客户端把密钥误发到普通端点后“200 但被丢弃”。
    model_config = ConfigDict(extra="forbid")

    deepseek_base_url: str | None = None
    deepseek_model: str | None = None
    voice_enabled: bool | None = None
    voice_provider: str | None = None
    dashscope_region: str | None = None
    dashscope_asr_model: str | None = None
    dashscope_tts_model: str | None = None
    dashscope_tts_voice: str | None = None
    search_enabled: bool | None = None


class SecretsUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deepseek_api_key: str | None = None
    dashscope_api_key: str | None = None


def _invalid(field: str) -> HTTPException:
    return HTTPException(status_code=400, detail=f"invalid_{field}")


def _validate_https_url(text: str) -> str:
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=400, detail="invalid_base_url")

    host_allowed = host == "api.deepseek.com" or host.endswith(".deepseek.com")
    custom_allowed = bool(settings.DEEPSEEK_ALLOW_CUSTOM_BASE_URL)
    if not (host_allowed or custom_allowed):
        raise HTTPException(status_code=400, detail="invalid_base_url")

    # 自定义地址也必须是 HTTPS：Key 不能以明文经过网络。
    if parts.scheme != "https":
        raise HTTPException(status_code=400, detail="invalid_base_url")
    if parts.username or parts.password or "@" in parts.netloc:
        raise HTTPException(status_code=400, detail="invalid_base_url")
    if not text or len(text) > 200:
        raise HTTPException(status_code=400, detail="invalid_base_url")
    return text


def _validate_plain(body: SettingsUpdateBody) -> dict[str, str]:
    values: dict[str, str] = {}
    provided = body.model_dump(exclude_none=True)

    for field, raw in provided.items():
        if field in {"voice_enabled", "search_enabled"}:
            values[field] = "true" if raw else "false"
            continue
        text = str(raw).strip()
        if field == "deepseek_base_url":
            values[field] = _validate_https_url(text)
            continue
        if field == "voice_provider":
            if text not in _VOICE_PROVIDERS:
                raise HTTPException(
                    status_code=400, detail="invalid_voice_provider"
                )
            values[field] = text
            continue
        if field == "dashscope_region":
            if not _REGION_RE.fullmatch(text):
                raise _invalid(field)
            values[field] = text
            continue
        if not text or len(text) > _MAX_TEXT.get(field, 200):
            raise _invalid(field)
        values[field] = text

    return values


def _validate_secrets(body: SecretsUpdateBody) -> dict[str, str | None]:
    secrets: dict[str, str | None] = {}
    for field, raw in body.model_dump(exclude_none=True).items():
        text = str(raw).strip()
        if not text:
            # 空字符串表示清除该覆盖值。
            secrets[field] = None
            continue
        if len(text) > _MAX_SECRET_LENGTH or not _SECRET_RE.fullmatch(text):
            raise HTTPException(status_code=400, detail="invalid_secret")
        secrets[field] = text
    return secrets


def _refresh_voice_runtime(
    before: tuple[tuple[str, Any], ...], after: tuple[tuple[str, Any], ...]
) -> None:
    """配置确实变了才重建；失败不影响设置已保存的事实。

    重建会读 provider 配置并可能抛错；此时设置已经 commit，若把异常抛给
    客户端，用户会看到保存失败并重试（实际已生效）。只有语音相关字段
    变化时才走这里：普通设置（搜索开关、DeepSeek 模型/Key）不应关闭
    正在进行的语音会话。

    `before`/`after` 由 `AppSettingsService.update` 在写入锁内快照，因此
    并发保存不会拿到过期值；“原值保存”也不会误关正在进行的语音通话。
    """
    if before == after:
        return

    from companion.server.app.routers import ws

    try:
        ws.rebuild_voice_runtime()
    except Exception:  # noqa: BLE001 - 只记录，不回显任何配置/Key
        logging.getLogger(__name__).exception("voice runtime rebuild failed")


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)):
    return await AppSettingsService.view(db)


@router.put("")
async def update_settings(
    body: SettingsUpdateBody, db: AsyncSession = Depends(get_db)
):
    plain = _validate_plain(body)
    before, after = await AppSettingsService.update(
        db, plain=plain, snapshot=_voice_fingerprint
    )
    _refresh_voice_runtime(before, after)
    return await AppSettingsService.view(db)


@router.put("/secrets")
async def update_secrets(
    body: SecretsUpdateBody, db: AsyncSession = Depends(get_db)
):
    secrets = _validate_secrets(body)
    before, after = await AppSettingsService.update(
        db, secrets=secrets, snapshot=_voice_fingerprint
    )
    _refresh_voice_runtime(before, after)
    return await AppSettingsService.view(db)


@router.delete("/secrets/{key}")
async def clear_secret(key: str, db: AsyncSession = Depends(get_db)):
    if key not in SECRET_KEYS:
        raise HTTPException(status_code=400, detail="unknown_setting")
    before, after = await AppSettingsService.update(
        db, secrets={key: None}, snapshot=_voice_fingerprint
    )
    _refresh_voice_runtime(before, after)
    return await AppSettingsService.view(db)
