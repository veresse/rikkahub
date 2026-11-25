from sqlalchemy import String, Integer, Boolean, Float, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from companion.server.app.database import Base
import time
import uuid


class DeviceModel(Base):
    """实体机器人设备。

    Token 只保存 SHA-256 哈希；协议版本、能力、音量、校准偏移与最近一次
    上报快照都落库，供 App 与动作编排读取。
    """

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), default="我的桌面机器人")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    protocol_version: Mapped[int] = mapped_column(Integer, default=2)
    firmware_version: Mapped[str] = mapped_column(String(32), default="")
    capabilities_json: Mapped[list] = mapped_column(JSON, default=list)
    bound_assistant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("assistants.id", ondelete="SET NULL"), nullable=True
    )
    bound_conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    volume: Mapped[int] = mapped_column(Integer, default=70)
    idle_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    pan_offset: Mapped[float] = mapped_column(Float, default=0.0)
    tilt_offset: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[str] = mapped_column(String(16), default="idle")
    last_report_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time() * 1000))
    last_seen_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
