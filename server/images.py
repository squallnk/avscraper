"""图片下载。

三条原则：

1. **候选逐个回退**。一个源给多张候选图（javdb 的缩略图有 30 张），第一张挂掉就试下一张，
   而不是整组失败。
2. **失败不阻断**。图片只是观感，元数据才是本体；任何图片失败都只记 warning。
3. **可关**。剧照体积大、张数多，默认关闭，由 `image_download` 配置决定要不要下。

文件名按 Emby / Jellyfin 的约定，以视频文件名为前缀：
```
{stem}-poster.jpg      海报
{stem}-thumb.jpg       缩略图
{stem}-fanart.jpg      背景图
extrafanart/{stem}-01.jpg   剧照
```
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from server.config import ImageDownloadConfig
from server.models import AggregatedMetadata, MediaMetadata
from server.storage import StorageError

if TYPE_CHECKING:
    from server.app import AppContext

logger = logging.getLogger(__name__)

IMAGE_SUFFIX = ".jpg"


@dataclass
class ImageTask:
    """一张要下的图：若干候选 URL + 落盘相对路径。"""

    kind: str
    candidates: list[str]
    relative_path: Path


@dataclass
class ImageReport:
    written: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "written": [p.name for p in self.written],
            "skipped": self.skipped,
            "failed": self.failed,
        }


def _dedupe(urls: list[str]) -> list[str]:
    seen: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.append(url)
    return seen


def plan_images(metadata: MediaMetadata, config: ImageDownloadConfig, *, stem: str) -> list[ImageTask]:
    """把元数据里的图片字段编成下载计划。纯函数，便于测试。"""
    tasks: list[ImageTask] = []

    if config.poster and metadata.poster_url:
        tasks.append(
            ImageTask("poster", _dedupe([metadata.poster_url]), Path(f"{stem}-poster{IMAGE_SUFFIX}"))
        )

    if config.thumb and metadata.thumb_urls:
        tasks.append(
            ImageTask("thumb", _dedupe(metadata.thumb_urls), Path(f"{stem}-thumb{IMAGE_SUFFIX}"))
        )

    fanart_candidates = _dedupe(metadata.fanart_urls)

    if config.fanart and fanart_candidates:
        tasks.append(
            ImageTask("fanart", list(fanart_candidates), Path(f"{stem}-fanart{IMAGE_SUFFIX}"))
        )

    if config.extrafanart and fanart_candidates:
        # 背景图已经用了第一张时，剧照从第二张开始，避免同一张图占两个槽位
        start = 1 if (config.fanart and len(fanart_candidates) > 1) else 0
        picked = fanart_candidates[start : start + config.extrafanart_limit]
        for index, url in enumerate(picked, start=1):
            tasks.append(
                ImageTask(
                    "extrafanart",
                    [url],
                    Path("extrafanart") / f"{stem}-{index:02d}{IMAGE_SUFFIX}",
                )
            )

    return tasks


class ImageDownloader:
    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx

    async def run(
        self,
        *,
        metadata: MediaMetadata,
        aggregated: AggregatedMetadata,
        metadata_dir: Path,
        stem: str | None = None,
    ) -> ImageReport:
        report = ImageReport()
        config = self._ctx.config.images

        if not (self._ctx.config.organize_enabled and not self._ctx.config.dry_run):
            report.skipped.append("写入未开启（dry_run 或 organize_enabled=false）")
            return report

        tasks = plan_images(metadata, config, stem=stem or metadata.number or "unknown")
        if not tasks:
            report.skipped.append("没有要下载的图片（全部关闭或源没给图）")
            return report

        try:
            self._ctx.storage.mkdir(metadata_dir)
        except StorageError as exc:
            report.failed.append(f"元数据目录不可写: {exc}")
            return report

        referer = self._referer_for(aggregated, "poster_url")
        semaphore = asyncio.Semaphore(config.concurrency)

        async def one(task: ImageTask) -> None:
            async with semaphore:
                await self._download_one(task, metadata_dir, report, referer, config)

        await asyncio.gather(*(one(task) for task in tasks), return_exceptions=True)
        return report

    def _referer_for(self, aggregated: AggregatedMetadata, field_name: str) -> str | None:
        """图片地址通常校验同源 Referer（javbus 的 pics.dmm.co.jp 就是），带上来源站主页。"""
        from server.sources import get as get_source

        origin = aggregated.field_sources.get(field_name)
        if not origin:
            return None
        plugin = get_source(origin)
        if plugin is None:
            return None
        return plugin.descriptor.homepage or None

    async def _download_one(
        self,
        task: ImageTask,
        metadata_dir: Path,
        report: ImageReport,
        referer: str | None,
        config: ImageDownloadConfig,
    ) -> None:
        target = metadata_dir / task.relative_path
        if target.exists() and not config.overwrite:
            report.skipped.append(f"{task.kind}: 已存在")
            return

        for url in task.candidates:
            try:
                data = await self._ctx.http.get_bytes(url, source=task.kind, referer=referer)
            except Exception as exc:  # noqa: BLE001 - 试下一张候选
                logger.debug("图片候选失败 %s: %s", url, exc)
                continue
            if not data:
                continue
            try:
                report.written.append(self._ctx.storage.write_bytes(target, data))
            except StorageError as exc:
                report.failed.append(f"{task.kind}: 写入失败 {exc}")
            return

        report.failed.append(f"{task.kind}: {len(task.candidates)} 个候选全部失败")


__all__ = ["ImageDownloader", "ImageReport", "ImageTask", "plan_images"]
