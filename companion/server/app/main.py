from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from companion.server.app.config import settings
from companion.server.app.database import init_db
from companion.server.app.routers import (
    assistants,
    conversations,
    memories,
    chat,
    import_export,
    devices,
    app_update
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动初始化数据库
    await init_db()
    yield

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan
)

# 允许跨域 (支持 Web 前端与调试)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
api_prefix = "/api"
app.include_router(assistants.router, prefix=api_prefix)
app.include_router(conversations.router, prefix=api_prefix)
app.include_router(memories.router, prefix=api_prefix)
app.include_router(chat.router, prefix=api_prefix)
app.include_router(import_export.router, prefix=api_prefix)
app.include_router(devices.router, prefix=api_prefix)
app.include_router(app_update.router, prefix=api_prefix)

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION
    }

# 静态前端资源托管 (当 companion/web/dist 存在时自动挂载)
web_dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
if web_dist.exists():
    app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("companion.server.app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
