from sqlalchemy import String, Integer, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from companion.server.app.database import Base
import time
import uuid

class DeviceModel(Base):
    """
    用于第 2 步实体挂件设备的配对与连接管理
    """
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), default="我的语音挂件")
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    bound_assistant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("assistants.id", ondelete="SET NULL"), nullable=True)
    bound_conversation_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time() * 1000))
    last_seen_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
