"""pytest 全局引导：在任何应用模块导入前隔离测试数据库。

`companion.server.app.config.Settings` 是模块级单例。若某个测试模块先导入它，
之后再设置 `DATA_DIR` 环境变量将不再生效，导致测试共用 `companion/server/data/companion.db`，
出现「第一次跑通过、第二次跑失败」的脏数据问题。
"""

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="rikkahub_pytest_data_"))
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("AUTO_MIGRATE", "true")
