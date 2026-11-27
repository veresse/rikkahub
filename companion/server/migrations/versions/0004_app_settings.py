"""4D：服务端运行时可改设置 `app_settings`。

Revision ID: 0004_app_settings
Revises: 0003_assistant_model_search
Create Date: 2026-11-27

密钥列的 `value` 是 AES-GCM 密文（`v1:` 前缀），普通列是明文；
表本身不带任何默认 Key，启动时由 `AppSettingsService` 加载并回写到 settings。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_app_settings"
down_revision = "0003_assistant_model_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
