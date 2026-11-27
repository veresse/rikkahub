"""服务端设置的读写与运行时覆盖。

DB 里的值通过本服务回写到 `settings`（pydantic-settings 实例允许赋值），
消费方（`DeepSeekClient`、语音绑定、搜索判定）都是调用时读取，因此写入后
下一轮请求即刻生效，无需重启进程。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from companion.server.app.config import settings
from companion.server.app.models.app_setting import AppSettingModel
from companion.server.app.services.secret_box import decrypt_secret, encrypt_secret

SECRET_KEYS = ("deepseek_api_key", "dashscope_api_key")

PLAIN_KEYS = (
    "deepseek_base_url",
    "deepseek_model",
    "voice_enabled",
    "voice_provider",
    "dashscope_region",
    "dashscope_asr_model",
    "dashscope_tts_model",
    "dashscope_tts_voice",
    "search_enabled",
)

# key -> (settings 字段, 值类型)。bool 在 DB 里存 "true"/"false"。
FIELD_BY_KEY: dict[str, tuple[str, type]] = {
    "deepseek_api_key": ("DEEPSEEK_API_KEY", str),
    "dashscope_api_key": ("DASHSCOPE_API_KEY", str),
    "deepseek_base_url": ("DEEPSEEK_BASE_URL", str),
    "deepseek_model": ("DEEPSEEK_DEFAULT_MODEL", str),
    "voice_enabled": ("VOICE_ENABLED", "bool"),
    "voice_provider": ("VOICE_PROVIDER", str),
    "dashscope_region": ("DASHSCOPE_REGION", str),
    "dashscope_asr_model": ("DASHSCOPE_ASR_MODEL", str),
    "dashscope_tts_model": ("DASHSCOPE_TTS_MODEL", str),
    "dashscope_tts_voice": ("DASHSCOPE_TTS_VOICE", str),
    "search_enabled": ("SEARCH_ENABLED", "bool"),
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _env_configured(env_name: str) -> bool:
    """该配置是否来自环境：进程环境变量或 pydantic-settings 读取的 `.env`。

    `BaseSettings` 读 `.env` 不会写回 `os.environ`，只看 `os.environ` 会把
    `.env` 里的 Key 误报成 `unset`。
    """
    if os.environ.get(env_name):
        return True
    env_file = settings.model_config.get("env_file")
    if not env_file:
        return False
    path = Path(env_file)
    if not path.is_file():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == env_name and value.strip().strip("'\""):
                return True
    except OSError:
        return False
    return False


class AppSettingsService:
    """DB 覆盖值的进程内缓存 + 应用。

    `_baseline` 记录首次加载前每个字段的原值（环境变量/代码默认），
    删除某个覆盖项时用它恢复，避免“清掉 Key 后 settings 里还留着旧值”。
    """

    _cache: dict[str, str] = {}
    _broken: set[str] = set()
    _baseline: dict[str, Any] | None = None
    # 写入与重载串行化：两个并发写入各自 select 全表时，后应用者会把先提交的
    # 覆盖值回滚成自己的旧快照。
    _LOCK = asyncio.Lock()

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache.clear()
        cls._broken.clear()
        cls._baseline = None

    @classmethod
    def _capture_baseline(cls) -> None:
        if cls._baseline is not None:
            return
        cls._baseline = {
            field: getattr(settings, field, "")
            for field, _ in FIELD_BY_KEY.values()
        }

    @classmethod
    def _apply_all(cls) -> None:
        cls._capture_baseline()
        assert cls._baseline is not None
        for field, value in cls._baseline.items():
            setattr(settings, field, value)
        for key, value in cls._cache.items():
            field, kind = FIELD_BY_KEY[key]
            setattr(settings, field, value == "true" if kind == "bool" else value)

    @classmethod
    async def load_and_apply(cls, db: AsyncSession) -> None:
        async with cls._LOCK:
            await cls._load_locked(db)

    @classmethod
    async def _load_locked(cls, db: AsyncSession) -> None:
        rows = (await db.execute(select(AppSettingModel))).scalars().all()
        cls._cache.clear()
        cls._broken.clear()
        for row in rows:
            if row.key not in FIELD_BY_KEY:
                continue
            # 以“键是否在密钥白名单”决定是否解密，而不是信任行上的 is_secret：
            # 否则手工/旧版本写入的明文密钥会被原样加载并留在磁盘上。
            if row.key in SECRET_KEYS:
                try:
                    value = decrypt_secret(row.value or "", aad=row.key)
                except ValueError:
                    # 损坏/轮换 SECRET_KEY：按未配置处理，绝不回显或崩溃。
                    cls._broken.add(row.key)
                    continue
            else:
                value = row.value or ""
            cls._cache[row.key] = value
        cls._apply_all()

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        *,
        plain: dict[str, str] | None = None,
        secrets: dict[str, str | None] | None = None,
        snapshot: Callable[[], Any] | None = None,
    ) -> tuple[Any, Any]:
        """写入并重载 DB 覆盖值。

        `snapshot` 在同一把锁内、写入前后各取一次并返回 `(before, after)`：
        调用方据此判断某项配置是否真的变化。必须在锁内取值——在锁外取会在
        并发写入（A: x→y，B: y→x）时拿到过期快照，导致该重建的运行时不重建。
        """
        async with cls._LOCK:
            before = snapshot() if snapshot is not None else None
            for key, value in (plain or {}).items():
                if key not in PLAIN_KEYS:
                    raise ValueError("unknown_setting")
                await cls._upsert(db, key, value)
            for key, value in (secrets or {}).items():
                if key not in SECRET_KEYS:
                    raise ValueError("unknown_setting")
                if value is None or value == "":
                    await db.execute(
                        delete(AppSettingModel).where(AppSettingModel.key == key)
                    )
                else:
                    await cls._upsert(db, key, encrypt_secret(value, aad=key))
            await db.commit()
            await cls._load_locked(db)
            after = snapshot() if snapshot is not None else None
        return before, after

    @classmethod
    async def _upsert(
        cls,
        db: AsyncSession,
        key: str,
        value: str,
    ) -> None:
        # 密的判定永远由键名决定：即使调用方传错标志，也不会把密钥存成明文行。
        is_secret = key in SECRET_KEYS
        existing = await db.get(AppSettingModel, key)
        if existing is None:
            db.add(
                AppSettingModel(
                    key=key,
                    value=value,
                    is_secret=is_secret,
                    updated_at=_now_ms(),
                )
            )
            return
        existing.value = value
        existing.is_secret = is_secret
        existing.updated_at = _now_ms()

    @classmethod
    def effective(cls, key: str) -> str:
        if key in cls._cache:
            return cls._cache[key]
        field, _ = FIELD_BY_KEY[key]
        return str(getattr(settings, field, "") or "")

    @classmethod
    async def view(cls, db: AsyncSession) -> dict[str, Any]:
        rows = (await db.execute(select(AppSettingModel))).scalars().all()
        stored = {row.key for row in rows}

        def source(key: str) -> str:
            env_name = FIELD_BY_KEY[key][0]
            # 解密失败的数据库行不会被加载进 `_cache`（见 `_load_locked`），
            # 此时实际生效的是环境变量；不能因为行还在就谎报 "database"。
            if key in stored and key not in cls._broken:
                return "database"
            if _env_configured(env_name):
                return "environment"
            return "unset"

        def configured(key: str) -> bool:
            return bool(cls.effective(key).strip())

        def secret_error(key: str) -> dict[str, str]:
            return {"error": "decrypt_failed"} if key in cls._broken else {}

        provider = (str(settings.VOICE_PROVIDER or "unavailable")).strip().lower()
        if not settings.VOICE_ENABLED:
            effective, reason = "unavailable", "voice_disabled"
        elif provider == "dashscope":
            if configured("dashscope_api_key"):
                effective, reason = "dashscope", None
            else:
                effective, reason = "unavailable", "dashscope_key_missing"
        elif provider == "mock":
            effective, reason = "mock", None
        elif provider == "unavailable":
            effective, reason = "unavailable", None
        else:
            effective, reason = "unavailable", "provider_unavailable"

        return {
            "deepseek": {
                "configured": configured("deepseek_api_key"),
                "source": source("deepseek_api_key"),
                "base_url": str(settings.DEEPSEEK_BASE_URL or ""),
                "model": str(settings.DEEPSEEK_DEFAULT_MODEL or ""),
                **secret_error("deepseek_api_key"),
            },
            "dashscope": {
                "configured": configured("dashscope_api_key"),
                "source": source("dashscope_api_key"),
                "region": str(settings.DASHSCOPE_REGION or ""),
                "asr_model": str(settings.DASHSCOPE_ASR_MODEL or ""),
                "tts_model": str(settings.DASHSCOPE_TTS_MODEL or ""),
                "tts_voice": str(settings.DASHSCOPE_TTS_VOICE or ""),
                **secret_error("dashscope_api_key"),
            },
            "voice": {
                "enabled": bool(settings.VOICE_ENABLED),
                "provider": provider,
                "effective": effective,
                "reason": reason,
            },
            "search": {
                "enabled": bool(settings.SEARCH_ENABLED),
                "provider": "builtin_bing",
                "requires_key": False,
            },
        }
