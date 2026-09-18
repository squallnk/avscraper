"""FastAPI 入口。

只监听回环地址（除显式配置）；写接口需要 `confirm=true`；
默认配置下 `dry_run=True` 且 `organize_enabled=False`，进程不会修改任何文件。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.api import router
from server.app import build_context, register_handlers, shutdown_context
from server.config import Settings

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=LOG_FORMAT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    configure_logging(settings.log_level)
    ctx = await build_context(settings)
    register_handlers(ctx)
    await ctx.runner.start()
    app.state.ctx = ctx
    app.state.webhook = ctx.webhook
    try:
        yield
    finally:
        await shutdown_context(ctx)


def create_app() -> FastAPI:
    app = FastAPI(
        title="avscraper",
        description="番号 / 里番元数据刮削器",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.include_router(router)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """构建产物存在就由后端托管，省掉一个容器与一层反代。"""
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    dist = Path(__file__).resolve().parents[1] / "web" / "dist"
    if not (dist / "index.html").is_file():
        return
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")


app = create_app()


def run() -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run("server.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
