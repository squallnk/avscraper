"""FastAPI 入口。

只监听回环地址（除显式配置）；写接口需要 `confirm=true`；
默认配置下 `dry_run=True` 且 `organize_enabled=False`，进程不会修改任何文件。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

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
    mount_frontend(app)
    return app


def mount_frontend(app: FastAPI, dist: Path | None = None) -> None:
    r"""构建产物存在就由后端托管，省掉一个容器与一层反代。

    前端是 **history 模式**路由（``createWebHistory``），所以 ``/records`` 这类路径在
    服务端没有对应文件 —— 必须回 ``index.html`` 交给前端路由。

    只挂 ``StaticFiles(html=True)`` 是不够的：它只在**目录根**返回 index.html，
    直接打开或刷新 ``http://host:9300/records`` 会拿到 404，页面一片空白 ——
    而用户恰恰就是这么进的。
    """
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    root = (dist or Path(__file__).resolve().parents[1] / "web" / "dist").resolve()
    index = root / "index.html"
    if not index.is_file():
        return

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        # API 的 404 必须保持 JSON 形状：前端按 {"detail": ...} 读错误，
        # 被这里吞成 index.html 的话，前端会拿 HTML 当 JSON 解析然后报个看不懂的错。
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        try:
            candidate = (root / full_path).resolve()
        except (OSError, ValueError):
            candidate = root
        # 真存在的文件照常给出去（favicon 之类），但**不能越出 dist** ——
        # 这个路径来自 URL，不校验就是目录穿越。
        if candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()


def run() -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run("server.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
