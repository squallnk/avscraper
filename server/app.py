"""进程组合根：把配置、DB、存储、HTTP、聚合器、任务队列接起来。

启动顺序有依赖，不能颠倒（DB 先于一切读配置，HTTP 先于聚合器）：
    Settings -> Database -> RuntimeConfig -> RootGuard/LocalStorage
             -> HttpClient -> Aggregator -> TaskRunner -> API
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.aggregate import Aggregator
from server.config import RuntimeConfig, Settings
from server.db import Database
from server.models import ScrapeStatus
from server.ratelimit import Breaker, RateLimiter, SerialQueue
from server.sources.http import HttpClient
from server.storage import LocalStorage, RootGuard
from server.tasks import Task, TaskRunner

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """运行期共享状态。所有模块从 `app.state.ctx` 取。"""

    settings: Settings
    db: Database
    config: RuntimeConfig
    storage: LocalStorage
    http: HttpClient
    aggregator: Aggregator
    runner: TaskRunner
    file_limiter: RateLimiter
    file_queue: SerialQueue
    breaker: Breaker
    scan_roots: list[Path] = field(default_factory=list)
    webhook: Any = None
    _flush_task: Any = None

    async def reload_config(self) -> RuntimeConfig:
        """从 DB 重读运行期配置，并把变化推给各组件。"""
        self.config = await self.db.get_config()
        self.storage.set_writes_enabled(
            self.config.organize_enabled and not self.config.dry_run
        )
        self.http.configure(
            rate=self.config.http_rate,
            burst=self.config.http_burst,
            timeout=self.config.request_timeout,
            max_retries=self.config.max_retries,
            proxy=self.config.proxy,
        )
        self.breaker.configure(self.config.cooldown_seconds, self.config.failure_threshold)
        self.http.set_source_cookies(self.config.source_cookies)
        self.aggregator = Aggregator(self.http, enabled_sources=self.config.enabled_sources)
        if self.webhook is not None:
            self.webhook.reconfigure()
        return self.config

    async def save_config(self, config: RuntimeConfig) -> RuntimeConfig:
        await self.db.save_config(config)
        return await self.reload_config()


async def build_context(settings: Settings) -> AppContext:
    db = Database(settings.database_path)
    await db.connect()

    config = await db.get_config()

    roots = list(settings.allowed_roots)
    guard = RootGuard(roots) if roots else RootGuard([])
    storage = LocalStorage(guard, allow_writes=config.organize_enabled and not config.dry_run)

    breaker = Breaker(config.cooldown_seconds, config.failure_threshold)
    http = HttpClient(
        proxy=config.proxy,
        timeout=config.request_timeout,
        rate=config.http_rate,
        burst=config.http_burst,
        max_retries=config.max_retries,
        breaker=breaker,
    )

    await http.start()
    http.set_source_cookies(config.source_cookies)

    aggregator = Aggregator(http, enabled_sources=config.enabled_sources)
    file_limiter = RateLimiter(config.file_op_rate, config.file_op_burst)
    file_queue = SerialQueue(file_limiter)
    runner = TaskRunner(concurrency=2)

    # 启动顺序：EventBus（无）-> DB -> 配置 -> 存储 -> HTTP -> 聚合 -> 队列 -> webhook。
    # webhook 依赖 storage 与 db，必须最后建。
    from server.webhook import WebhookProcessor

    ctx = AppContext(
        settings=settings,
        db=db,
        config=config,
        storage=storage,
        http=http,
        aggregator=aggregator,
        runner=runner,
        file_limiter=file_limiter,
        file_queue=file_queue,
        breaker=breaker,
    )
    ctx.webhook = WebhookProcessor(ctx)
    await start_webhook_loop(ctx)

    logger.info(
        "启动完成：数据目录=%s，允许根目录=%s，dry_run=%s，organize=%s，webhook=%s",
        settings.data_dir,
        [str(r) for r in roots] or "（未配置，禁止任何写入）",
        config.dry_run,
        config.organize_enabled,
        config.webhook_enabled,
    )
    return ctx


async def start_webhook_loop(ctx: AppContext) -> None:
    """后台每 2 秒把到期的目录事件扫掉。"""

    async def loop() -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                if ctx.webhook is not None and ctx.webhook.debouncer.pending:
                    await ctx.webhook.flush_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 后台循环不能因为单次失败退出
                logger.exception("webhook 防抖刷新失败")

    ctx._flush_task = asyncio.create_task(loop(), name="avs-webhook-flush")


async def shutdown_context(ctx: AppContext) -> None:
    if ctx._flush_task is not None:
        ctx._flush_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ctx._flush_task
    if ctx.webhook is not None:
        await ctx.webhook.drain()
    await ctx.runner.stop()
    await ctx.http.close()
    await ctx.db.close()


def register_handlers(ctx: AppContext) -> None:
    """登记任务处理器。任务类型只有一个：扫描并刮削。"""

    async def handle_scan_and_scrape(task: Task) -> None:
        from server.pipeline import run_scan_and_scrape

        await run_scan_and_scrape(ctx, task)

    ctx.runner.register("scan_and_scrape", handle_scan_and_scrape)


__all__ = [
    "AppContext",
    "ScrapeStatus",
    "build_context",
    "register_handlers",
    "shutdown_context",
]
