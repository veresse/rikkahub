import asyncio
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event

from companion.server.app.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=settings.DEBUG,
    connect_args={"timeout": 15.0},  # 繁忙超时 15 秒，避免锁等待立即抛错
)


# 开启 SQLite 的外键检查与 WAL 模式
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


SERVER_DIR = Path(__file__).resolve().parent.parent


def _run_migrations() -> None:
    """同步执行 alembic upgrade head（在线迁移）。

    Alembic 是同步 API，放在线程中运行，避免阻塞事件循环。
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(SERVER_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.sync_database_url)
    command.upgrade(config, "head")


async def init_db():
    """初始化数据库。

    - 生产/测试默认走 Alembic 迁移，保证 v1 存量库可平滑升级；
    - `AUTO_MIGRATE=false` 时退化为 `create_all`（仅适合临时调试）。
    """
    if settings.AUTO_MIGRATE:
        await asyncio.to_thread(_run_migrations)
        logger.info("Database migrated to head via Alembic.")
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database initialized with create_all (AUTO_MIGRATE=false).")
