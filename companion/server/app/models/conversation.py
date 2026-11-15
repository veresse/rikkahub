from sqlalchemy import String, Integer, Boolean, Text, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from companion.server.app.database import Base
import time
import uuid

class ConversationModel(Base):
    """
    对应 RikkaHub 的 ConversationEntity（会话）
    """
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    assistant_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    custom_system_prompt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time() * 1000))
    updated_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time() * 1000))
    raw_config: Mapped[dict] = mapped_column(JSON, default=dict)

    # 关联
    assistant = relationship("AssistantModel", back_populates="conversations")
    nodes = relationship("MessageNodeModel", back_populates="conversation", cascade="all, delete-orphan", order_by="MessageNodeModel.node_index")
