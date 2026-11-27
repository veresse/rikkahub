from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from companion.server.app.database import Base


class AppSettingModel(Base):
    """服务端运行时可改的设置（键值白名单）。

    密钥列的 `value` 存的是 AES-GCM 密文（`v1:` 前缀），绝不存明文；
    普通列直接存文本。改完由 `AppSettingsService` 立即回写到 `settings`。
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
