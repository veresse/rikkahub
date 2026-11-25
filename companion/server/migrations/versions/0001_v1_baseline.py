"""v1 基线：角色、会话、分支消息、记忆、设备。

Revision ID: 0001_v1_baseline
Revises:
Create Date: 2026-11-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_v1_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("avatar", sa.JSON(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("top_p", sa.Float(), nullable=True),
        sa.Column("context_message_limit", sa.Integer(), nullable=False),
        sa.Column("enable_memory", sa.Boolean(), nullable=False),
        sa.Column("use_global_memory", sa.Boolean(), nullable=False),
        sa.Column("enable_web_search", sa.Boolean(), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("raw_snapshot", sa.JSON(), nullable=False),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "assistant_id",
            sa.String(length=36),
            sa.ForeignKey("assistants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False),
        sa.Column("custom_system_prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("raw_config", sa.JSON(), nullable=False),
    )

    op.create_table(
        "message_nodes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_index", sa.Integer(), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("select_index", sa.Integer(), nullable=False),
    )

    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "assistant_id",
            sa.String(length=36),
            sa.ForeignKey("assistants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )

    op.create_table(
        "devices",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column(
            "bound_assistant_id",
            sa.String(length=36),
            sa.ForeignKey("assistants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "bound_conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.Integer(), nullable=True),
    )
    op.create_index("ix_devices_token", "devices", ["token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_devices_token", table_name="devices")
    op.drop_table("devices")
    op.drop_table("memories")
    op.drop_table("message_nodes")
    op.drop_table("conversations")
    op.drop_table("assistants")
