from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from companion.server.app.config import settings
from companion.server.app.database import AsyncSessionLocal, init_db
from companion.server.app.routers import (
    actions,
    assistants,
    assets,
    auth,
    chat,
    conversations,
    devices,
    import_export,
    memories,
    app_update,
    settings as settings_router,
    ws,
)
from companion.server.app.security.dependencies import require_app_token
from companion.server.app.services.application_shutdown import (
    GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    LIFESPAN_SHUTDOWN_TIMEOUT_SECONDS,
    bounded_shutdown_application,
)
from companion.server.app.services.upload_limits import ContentLengthLimitMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动时执行 Alembic 迁移到 head
    await init_db()
    # 迁移后加载 DB 里的设置覆盖（API Key 等），再开放请求处理。
    await prepare_settings_runtime()
    # 同进程重新运行 lifespan（测试/嵌入式）时复位停机标志，否则所有握手都会被
    # 上一次停机的 `_shutting_down` 拒绝。
    ws.device_gateway.open_for_connections()
    ws.app_gateway.open_for_connections()
    yield
    # 兜底：正常情况下 `CompanionServer` 已经清空两个网关，这里近似空操作；但
    # 直接 `uvicorn ...:app` 或第一次应用层停机超时时仍会走到，所以同样必须有界。
    await bounded_shutdown_application(
        LIFESPAN_SHUTDOWN_TIMEOUT_SECONDS, source="lifespan shutdown"
    )


async def prepare_settings_runtime() -> None:
    """加载 DB 覆盖并按其重建语音运行时。

    `ws.py` 导入时已按环境变量建过运行时；DB 覆盖加载后必须重建，否则 4D
    保存的语音 Key/开关要等进程重启才生效。启动与测试共用本函数。
    """
    from companion.server.app.services.app_settings import AppSettingsService

    async with AsyncSessionLocal() as session:
        await AppSettingsService.load_and_apply(session)
    ws.rebuild_voice_runtime()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# 仅在显式配置白名单时启用 CORS；默认无浏览器客户端（管理网页已移除）。
if settings.CORS_ALLOW_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOW_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# 上传体积早退：必须在路由读取 multipart 之前按 Content-Length 拒绝。
app.add_middleware(
    ContentLengthLimitMiddleware,
    rules={
        "/api/backup/preview": (
            lambda: settings.MAX_UPLOAD_SIZE_BYTES,
            "file_too_large",
        ),
        "/api/assets/preview": (
            lambda: settings.ASSET_MAX_ARCHIVE_BYTES,
            "archive_too_large",
        ),
    },
)

# 注册 API 路由
api_prefix = "/api"
app_protected = [Depends(require_app_token)]

app.include_router(auth.router, prefix=api_prefix)  # /login 公开，其余端点自行鉴权
app.include_router(assistants.router, prefix=api_prefix, dependencies=app_protected)
app.include_router(conversations.router, prefix=api_prefix, dependencies=app_protected)
app.include_router(memories.router, prefix=api_prefix, dependencies=app_protected)
app.include_router(chat.router, prefix=api_prefix, dependencies=app_protected)
app.include_router(import_export.router, prefix=api_prefix, dependencies=app_protected)
app.include_router(actions.router, prefix=api_prefix, dependencies=app_protected)
app.include_router(app_update.router, prefix=api_prefix, dependencies=app_protected)
app.include_router(settings_router.router, prefix=api_prefix, dependencies=app_protected)
app.include_router(assets.router, prefix=api_prefix, dependencies=app_protected)
# devices 内部混有公开配对端点，鉴权按端点声明
app.include_router(devices.router, prefix=api_prefix)

# WebSocket：/device/ws、/app/ws（握手阶段自行鉴权，不使用 HTTP 依赖）
app.include_router(ws.router)


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


def build_config() -> uvicorn.Config:
    """与部署入口共用一份配置，便于测试停机相关参数。"""
    return uvicorn.Config(
        "companion.server.app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
        ws_ping_interval=settings.WS_PING_INTERVAL_SECONDS,
        ws_ping_timeout=settings.WS_PING_TIMEOUT_SECONDS,
        ws_max_size=settings.WS_TRANSPORT_MAX_MESSAGE_BYTES,
    )


if __name__ == "__main__":
    from companion.server.app.server import run

    run(build_config())
