from sqlalchemy import String, Integer, Float, Boolean, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from companion.server.app.database import Base
import uuid

class AssistantModel(Base):
    """
    对应 RikkaHub 的 Assistant（助手/角色）
    """
    __tablename__ = "assistants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar: Mapped[dict] = mapped_column(JSON, default=dict)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_message_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 为不限制
    enable_memory: Mapped[bool] = mapped_column(Boolean, default=False)
    use_global_memory: Mapped[bool] = mapped_column(Boolean, default=False)
    enable_web_search: Mapped[bool] = mapped_column(Boolean, default=False)
    enable_action_sync: Mapped[bool] = mapped_column(Boolean, default=True)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # 保留原始未映射字段，防止数据丢失

    # 关联
    conversations = relationship("ConversationModel", back_populates="assistant", cascade="all, delete-orphan")
    memories = relationship("MemoryModel", back_populates="assistant", cascade="all, delete-orphan")
