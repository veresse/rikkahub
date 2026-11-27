"""4D：素材包 `asset_packages`。

Revision ID: 0005_asset_packages
Revises: 0004_app_settings
Create Date: 2026-11-27

帧文件本身放在 `DATA_DIR/assets/<id>/frames/`，本表只记元数据与相对目录名。
`state` 只允许 active / previous，由服务层用模块级锁保证唯一。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_asset_packages"
down_revision = "0004_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_packages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("fps", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("frame_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("storage_path", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activated_at", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("state IN ('active', 'previous')", name="ck_asset_state"),
    )
    op.create_index("ix_asset_packages_state", "asset_packages", ["state"])


def downgrade() -> None:
    op.drop_index("ix_asset_packages_state", table_name="asset_packages")
    op.drop_table("asset_packages")
