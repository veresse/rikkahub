"""v2：设备能力/状态、Token 哈希、App Token、配对码、动作联动开关。

Revision ID: 0002_v2_actions_auth
Revises: 0001_v1_baseline
Create Date: 2026-11-25

数据迁移说明：
- `devices.token` 明文列在升级时被替换为 `devices.token_hash`（SHA-256）。
- 由于哈希不可逆，downgrade 只能把哈希写回 `token` 列占位，设备需要重新配对。
"""

from __future__ import annotations

import hashlib

from alembic import op
import sqlalchemy as sa

revision = "0002_v2_actions_auth"
down_revision = "0001_v1_baseline"
branch_labels = None
depends_on = None


def _index_exists(bind, table: str, index: str) -> bool:
    rows = bind.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:table"),
        {"table": table},
    ).fetchall()
    return any(row[0] == index for row in rows)


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("assistants") as batch:
        batch.add_column(
            sa.Column("enable_action_sync", sa.Boolean(), nullable=False, server_default="1")
        )

    # 先取出旧明文 Token，批处理重建表后无法再读取。
    rows = bind.execute(sa.text("SELECT id, token FROM devices")).fetchall()
    hashes = {row[0]: hashlib.sha256((row[1] or "").encode("utf-8")).hexdigest() for row in rows}

    if _index_exists(bind, "devices", "ix_devices_token"):
        op.drop_index("ix_devices_token", table_name="devices")

    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("token_hash", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("firmware_version", sa.String(length=32), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("capabilities_json", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("volume", sa.Integer(), nullable=False, server_default="70"))
        batch.add_column(
            sa.Column("idle_enabled", sa.Boolean(), nullable=False, server_default="1")
        )
        batch.add_column(sa.Column("pan_offset", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("tilt_offset", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(
            sa.Column("state", sa.String(length=16), nullable=False, server_default="idle")
        )
        batch.add_column(sa.Column("last_report_json", sa.JSON(), nullable=True))
        batch.drop_column("token")

    for device_id, token_hash in hashes.items():
        bind.execute(
            sa.text("UPDATE devices SET token_hash=:token_hash WHERE id=:device_id"),
            {"token_hash": token_hash, "device_id": device_id},
        )

    op.create_index("ix_devices_token_hash", "devices", ["token_hash"], unique=True)

    op.create_table(
        "app_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
    )
    op.create_index("ix_app_tokens_token_hash", "app_tokens", ["token_hash"], unique=True)

    op.create_table(
        "pairing_codes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("assistant_id", sa.String(length=36), nullable=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("used_at", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_pairing_codes_code_hash", "pairing_codes", ["code_hash"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(sa.text("SELECT id, token_hash FROM devices")).fetchall()
    hashes = {row[0]: (row[1] or "") for row in rows}

    op.drop_index("ix_pairing_codes_code_hash", table_name="pairing_codes")
    op.drop_table("pairing_codes")
    op.drop_index("ix_app_tokens_token_hash", table_name="app_tokens")
    op.drop_table("app_tokens")

    with op.batch_alter_table("assistants") as batch:
        batch.drop_column("enable_action_sync")

    if _index_exists(bind, "devices", "ix_devices_token_hash"):
        op.drop_index("ix_devices_token_hash", table_name="devices")

    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("token", sa.String(length=64), nullable=True))
        batch.drop_column("token_hash")
        batch.drop_column("protocol_version")
        batch.drop_column("firmware_version")
        batch.drop_column("capabilities_json")
        batch.drop_column("volume")
        batch.drop_column("idle_enabled")
        batch.drop_column("pan_offset")
        batch.drop_column("tilt_offset")
        batch.drop_column("state")
        batch.drop_column("last_report_json")

    for device_id, token_hash in hashes.items():
        bind.execute(
            sa.text("UPDATE devices SET token=:token WHERE id=:device_id"),
            {"token": token_hash, "device_id": device_id},
        )
    op.create_index("ix_devices_token", "devices", ["token"], unique=True)
