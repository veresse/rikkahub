from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from companion.server.app.config import settings
from companion.server.app.database import init_db
from companion.server.app.routers import (
    actions,
    assistants,
    auth,
    chat,
    conversations,
    devices,
    import_export,
    memories,
    app_update,
    ws,
)
from companion.server.app.security.dependencies import require_app_token
from companion.server.app.services.application_shutdown import (
    GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    LIFESPAN_SHUTDOWN_TIMEOUT_SECONDS,
    bounded_shutdown_application,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动时执行 Alembic 迁移到 head
    await init_db()
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
