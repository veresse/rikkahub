from sqlalchemy import String, Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from companion.server.app.database import Base
import time
import uuid

class MemoryModel(Base):
    """
    对应 RikkaHub 的 MemoryFileEntity（长期记忆）
    assistant_id 为空代表全局共享记忆；非空代表角色专属记忆
    """
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    assistant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=True)
    path: Mapped[str] = mapped_column(String(255), default="default.md")
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time() * 1000))

    # 关联
    assistant = relationship("AssistantModel", back_populates="memories")
