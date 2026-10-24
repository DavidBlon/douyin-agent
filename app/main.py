"""FastAPI 应用装配：lifespan、CORS、令牌鉴权中间件、路由挂载和静态资源。

具体路由在 app/api/ 按域拆分；replier/sharer 单例在 app/services.py。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .agent import runner
from .api import (
    analytics,
    chat,
    content,
    crm,
    debug,
    fans,
    live,
    records,
    settings,
    share,
    system,
)
from .auth import TokenAuthMiddleware, get_or_create_token
from .chat import chat as chat_service
from .config import DEBUG_DIR, MEDIA_DIR, WEB_DIST, ensure_dirs
from .liveview import init_liveview
from .operator import operator
from .planner import planner
from .publisher import publisher
from .services import replier, sharer


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    db.init_db()
    token = get_or_create_token()
    print(f"[auth] 本地 API 令牌：{token}", flush=True)
    print("[auth] 令牌保存在 data/local_token；也可用 .env 的 LOCAL_API_TOKEN 指定", flush=True)
    init_liveview(runner.browser, runner)
    await replier.apply_settings()
    await sharer.apply_settings()
    await chat_service.start()
    await publisher.start()
    await planner.start()
    await operator.start()
    yield
    try:
        await operator.stop()
    except Exception:
        pass
    try:
        await planner.stop()
    except Exception:
        pass
    try:
        await publisher.stop()
    except Exception:
        pass
    try:
        await chat_service.stop()
    except Exception:
        pass
    try:
        await replier.stop()
    except Exception:
        pass
    try:
        await sharer.stop()
    except Exception:
        pass
    try:
        await runner.browser.close()
    except Exception:
        pass


app = FastAPI(title="Douyin Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(TokenAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8787",
        "http://127.0.0.1:8787",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    system.router,
    settings.router,
    chat.router,
    share.router,
    fans.router,
    analytics.router,
    content.router,
    crm.router,
    records.router,
    debug.router,
    live.router,
):
    app.include_router(router)

ensure_dirs()
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")
app.mount("/debug", StaticFiles(directory=str(DEBUG_DIR)), name="debug")

if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:

    @app.get("/")
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "message": "前端还没有构建。开发时在 web/ 运行 npm run dev；生产时 npm run build 后重启。",
                "api": "/docs",
            }
        )
