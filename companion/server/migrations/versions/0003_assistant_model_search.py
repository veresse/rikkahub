"""4B：角色联网搜索三态 `web_search_mode`。

Revision ID: 0003_assistant_model_search
Revises: 0002_v2_actions_auth
Create Date: 2026-11-27

数据迁移说明：
- 新列 `web_search_mode` ∈ {off, auto, on}，默认 off；
- 旧的布尔开关 `enable_web_search=1` 回填为 `auto`（保留“关键词触发”语义），
  `0` 回填为 `off`。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_assistant_model_search"
down_revision = "0002_v2_actions_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assistants") as batch:
        batch.add_column(
            sa.Column(
                "web_search_mode",
                sa.String(length=8),
                nullable=False,
                server_default="off",
            )
        )
    op.execute(
        "UPDATE assistants SET web_search_mode = "
        "CASE WHEN enable_web_search THEN 'auto' ELSE 'off' END"
    )


def downgrade() -> None:
    with op.batch_alter_table("assistants") as batch:
        batch.drop_column("web_search_mode")
