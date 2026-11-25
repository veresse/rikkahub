from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from companion.server.app.database import Base
import time
import uuid


class PairingCodeModel(Base):
    """一次性设备配对码（只存哈希）。

    `attempts` 记录该码被错误尝试的次数，便于审计；IP 维度的限速由
    `security.rate_limit` 负责，因为错误码不会命中任何记录。
    """

    __tablename__ = "pairing_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    assistant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time() * 1000))
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    used_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
