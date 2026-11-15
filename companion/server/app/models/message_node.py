from sqlalchemy import String, Integer, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from companion.server.app.database import Base
import uuid

class MessageNodeModel(Base):
    """
    对应 RikkaHub 的 MessageNodeEntity（树状分支节点）
    一个节点代表一轮消息互动。
    messages: 存储 List[UIMessage]，支持同一轮重新生成产生的多个候选版本
    select_index: 用户当前选中的分支索引 (0 <= select_index < len(messages))
    """
    __tablename__ = "message_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    node_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    select_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 关联
    conversation = relationship("ConversationModel", back_populates="nodes")
