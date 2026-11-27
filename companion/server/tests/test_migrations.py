"""Alembic 迁移双向测试（Phase 2 基础）。

覆盖：
1. 空库 upgrade head -> downgrade -1 -> upgrade head；
2. 模拟 v1 库（只升级到 0001）插入数据后再升级到 head；
3. v2 新列、app_tokens、pairing_codes 表存在；
4. 迁移不会破坏 v1 的角色/会话/记忆数据。
"""

import sqlite3
from pathlib import Path

import pytest

alembic = pytest.importorskip("alembic")
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

SERVER_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = SERVER_DIR / "alembic.ini"


def make_config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def table_columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_empty_database_round_trip(tmp_path):
    db_path = tmp_path / "empty.db"
    cfg = make_config(db_path)

    command.upgrade(cfg, "head")
    cols = table_columns(db_path, "devices")
    assert {
        "protocol_version",
        "capabilities_json",
        "volume",
        "idle_enabled",
        "pan_offset",
        "tilt_offset",
        "last_report_json",
        "token_hash",
    } <= cols
    assert "enable_action_sync" in table_columns(db_path, "assistants")
    assert "web_search_mode" in table_columns(db_path, "assistants")
    assert {"app_tokens", "pairing_codes", "app_settings", "asset_packages"} <= table_names(db_path)

    # 4D 的 0005 只加素材包表；按 revision 显式回退，避免“head-1”随新迁移漂移。
    command.downgrade(cfg, "0004_app_settings")
    assert "asset_packages" not in table_names(db_path)
    assert "app_settings" in table_names(db_path)

    # 0004 只加设置表。
    command.downgrade(cfg, "0003_assistant_model_search")
    assert "app_settings" not in table_names(db_path)
    assert "web_search_mode" in table_columns(db_path, "assistants")
    assert "app_tokens" in table_names(db_path)

    command.downgrade(cfg, "0002_v2_actions_auth")
    assert "web_search_mode" not in table_columns(db_path, "assistants")
    assert "app_tokens" in table_names(db_path)
    assert "enable_action_sync" in table_columns(db_path, "assistants")

    command.downgrade(cfg, "0001_v1_baseline")
    assert "app_tokens" not in table_names(db_path)
    assert "enable_action_sync" not in table_columns(db_path, "assistants")

    command.upgrade(cfg, "head")
    assert "app_tokens" in table_names(db_path)
    assert "web_search_mode" in table_columns(db_path, "assistants")
    assert "app_settings" in table_names(db_path)
    assert "asset_packages" in table_names(db_path)


def test_0003_backfills_web_search_mode(tmp_path):
    db_path = tmp_path / "backfill.db"
    cfg = make_config(db_path)

    command.upgrade(cfg, "0002_v2_actions_auth")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO assistants (id, name, avatar, system_prompt, context_message_limit, "
        "enable_memory, use_global_memory, enable_web_search, enable_action_sync, raw_snapshot) "
        "VALUES ('on-1', '开搜索', '{}', '', 0, 0, 0, 1, 1, '{}')"
    )
    conn.execute(
        "INSERT INTO assistants (id, name, avatar, system_prompt, context_message_limit, "
        "enable_memory, use_global_memory, enable_web_search, enable_action_sync, raw_snapshot) "
        "VALUES ('off-1', '关搜索', '{}', '', 0, 0, 0, 0, 1, '{}')"
    )
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    rows = dict(conn.execute("SELECT id, web_search_mode FROM assistants").fetchall())
    conn.close()

    assert rows == {"on-1": "auto", "off-1": "off"}


def test_v1_database_upgrade_preserves_data(tmp_path):
    db_path = tmp_path / "v1.db"
    cfg = make_config(db_path)

    command.upgrade(cfg, "0001_v1_baseline")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO assistants (id, name, avatar, system_prompt, context_message_limit, "
        "enable_memory, use_global_memory, enable_web_search, raw_snapshot) "
        "VALUES ('ast-1', '夏以昼', '{}', '人设', 20, 1, 0, 1, '{}')"
    )
    conn.execute(
        "INSERT INTO conversations (id, assistant_id, title, is_pinned, custom_system_prompt, "
        "created_at, updated_at, raw_config) "
        "VALUES ('conv-1', 'ast-1', '旧对话', 0, '', 1, 2, '{}')"
    )
    conn.execute(
        "INSERT INTO memories (id, assistant_id, path, content, updated_at) "
        "VALUES ('mem-1', 'ast-1', 'default.md', '喜欢摄影', 3)"
    )
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT enable_action_sync FROM assistants WHERE id='ast-1'"
    ).fetchone()
    assert row == (1,)
    assert conn.execute("SELECT title FROM conversations WHERE id='conv-1'").fetchone() == ("旧对话",)
    assert conn.execute("SELECT content FROM memories WHERE id='mem-1'").fetchone() == ("喜欢摄影",)
    conn.close()

    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT count(*) FROM assistants").fetchone() == (1,)
    conn.close()


def test_devices_plaintext_token_is_hashed_on_upgrade(tmp_path):
    db_path = tmp_path / "with_device.db"
    cfg = make_config(db_path)

    command.upgrade(cfg, "0001_v1_baseline")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO devices (id, name, token, is_active, created_at) "
        "VALUES ('dev-1', '旧设备', 'plaintext-token', 1, 100)"
    )
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    token_hash = conn.execute("SELECT token_hash FROM devices WHERE id='dev-1'").fetchone()[0]
    conn.close()
    import hashlib

    assert token_hash == hashlib.sha256(b"plaintext-token").hexdigest()
    assert "token" not in table_columns(db_path, "devices")
