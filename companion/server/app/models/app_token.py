from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from companion.server.app.database import Base
import time
import uuid


class AppTokenModel(Base):
    """Android App 登录后签发的 Bearer Token（只存哈希）。"""

    __tablename__ = "app_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), default="admin")
    device_name: Mapped[str] = mapped_column(String(100), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time() * 1000))
    last_used_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
