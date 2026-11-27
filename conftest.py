"""pytest 全局引导：在任何应用模块导入前隔离测试数据库。

`companion.server.app.config.Settings` 是模块级单例。若某个测试模块先导入它，
之后再设置 `DATA_DIR` 环境变量将不再生效，导致测试共用 `companion/server/data/companion.db`，
出现「第一次跑通过、第二次跑失败」的脏数据问题。
"""

import atexit
import os
import shutil
import tempfile

# 强制隔离：即使外部 shell 导出了 DATA_DIR，也不能让测试写到开发者真实库
# （素材行清理会 DELETE asset_packages）。退出时尽力删除临时目录。
_data_dir = tempfile.mkdtemp(prefix="rikkahub_pytest_data_")
os.environ["DATA_DIR"] = _data_dir
atexit.register(shutil.rmtree, _data_dir, True)
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("AUTO_MIGRATE", "true")


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """测试结束后先关闭数据库连接，再删临时目录。

    只靠 `atexit` 不够：进程退出时 aiosqlite 的工作线程仍持有
    `companion.db-wal` / `-shm`，Windows 会拒绝删除且 `ignore_errors=True`
    静默失败，于是每次测试都在 %TEMP% 留几 MB。这里在会话结束时显式
    dispose 引擎，`atexit` 仍作为兜底。
    """
    import asyncio

    try:
        from companion.server.app import database

        asyncio.run(database.engine.dispose())
    except Exception:  # pragma: no cover - 清理失败不能影响测试结果
        pass
    shutil.rmtree(_data_dir, ignore_errors=True)
