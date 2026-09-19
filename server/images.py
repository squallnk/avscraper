"""图片下载。

三条原则：

1. **候选逐个回退**。一个源给多张候选图（javdb 的缩略图有 30 张），第一张挂掉就试下一张，
   而不是整组失败。
2. **失败不阻断**。图片只是观感，元数据才是本体；任何图片失败都只记 warning。
3. **可关**。剧照体积大、张数多，默认关闭，由 `image_download` 配置决定要不要下。

文件名按 Emby / Jellyfin 的约定，以视频文件名为前缀：
```
{stem}-poster.jpg      海报
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

if TYPE_CHECKING:
    from collections.abc import Sequence

from server.config import ImageDownloadConfig
from server.dmm import hd_cover_urls
from server.imageinfo import image_size
from server.models import AggregatedMetadata, ContentType, MediaMetadata
from server.storage import StorageError

if TYPE_CHECKING:
    from server.app import AppContext

logger = logging.getLogger(__name__)

IMAGE_SUFFIX = ".jpg"

# 每类图片对应聚合结果里的哪个字段 —— 决定兜底 Referer 取谁的
_REFERER_FIELD = {
    "poster": "poster_url",
    "fanart": "fanart_urls",
    "extrafanart": "fanart_urls",
    "thumb": "thumb_urls",
}


@dataclass
class ImageTask:
    """一张要下的图：若干候选 URL + 落盘相对路径。"""

    kind: str
    candidates: list[str]
    relative_path: Path
    min_width_exempt: tuple[str, ...] = ()
    """这些候选不做 `fanart_min_width` 检查。

    只用于"封面兜底当背景图"：那个宽度检查是为了挡掉站点的 120x90 缩略图，
    但封面是我们自己挑中的代表图 —— 把它也挡掉，结果是同一批文件里有几张
    有背景图、有几张没有，而原因完全看不出来。
    """


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


def plan_images(
    metadata: MediaMetadata,
    config: ImageDownloadConfig,
    *,
    stem: str,
    content_type: ContentType = ContentType.UNKNOWN,
    poster_fallbacks: Sequence[str] = (),
) -> list[ImageTask]:
    """把元数据里的图片字段编成下载计划。纯函数，便于测试。"""
    tasks: list[ImageTask] = []

    if config.poster:
        # 有码/无码的番号能直接拼出 DMM 的官方包装图（2184x1468，比源站的 800x538
        # 大 7 倍像素）。放在候选**最前面**，404 就自动回退到源站海报 ——
        # 推不出 content id 或 DMM 没有这张图时，行为跟以前完全一样。
        candidates: list[str] = []
        if content_type in (ContentType.CENSORED, ContentType.UNCENSORED):
            candidates.extend(hd_cover_urls(metadata.number))
        if metadata.poster_url:
            candidates.append(metadata.poster_url)
        # 其它源给的海报垫在后面。**必须留退路**：海报只有一张候选时，
        # 一次网络抖动或者一个失效的图片地址，就整个文件没有封面 ——
        # 实跑遇到过一次（bangumi 那张图本身是好的，但那次下载失败了）。
        candidates.extend(poster_fallbacks)
        if candidates:
            tasks.append(
                ImageTask("poster", _dedupe(candidates), Path(f"{stem}-poster{IMAGE_SUFFIX}"))
            )

    fanart_candidates = _dedupe(metadata.fanart_urls)

    if config.fanart:
        # 背景图优先用大图：站点的"剧照"候选往往只是 120x90 的缩略图，
        # 当背景图用会被拉伸成一团模糊。把封面垫在候选末尾兜底。
        fanart_urls = [url for url in fanart_candidates if url != metadata.poster_url]
        if metadata.poster_url:
            fanart_urls.append(metadata.poster_url)
        if fanart_urls:
            tasks.append(
                ImageTask(
                    "fanart",
                    _dedupe(fanart_urls),
                    Path(f"{stem}-fanart{IMAGE_SUFFIX}"),
                    min_width_exempt=(metadata.poster_url,) if metadata.poster_url else (),
                )
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
        overwrite: bool | None = None,
    ) -> ImageReport:
        """`overwrite=None` 表示跟随配置；显式 True 用于"人工重刮"。

        为什么要能覆盖：删记录重刮的前提是"这条结果不对"，而错的那张封面
        已经躺在磁盘上了。不覆盖的话 NFO 换了新内容、封面还是旧的，
        看起来像修好了其实没有 —— 这种"修了一半"比明显没修更难发现。
        """
        report = ImageReport()
        config = self._ctx.config.images
        if overwrite is None:
            overwrite = config.overwrite

        if not (self._ctx.config.organize_enabled and not self._ctx.config.dry_run):
            report.skipped.append("写入未开启（dry_run 或 organize_enabled=false）")
            return report

        tasks = plan_images(
            metadata,
            config,
            stem=stem or metadata.number or "unknown",
            content_type=aggregated.content_type,
            poster_fallbacks=self._poster_fallbacks(aggregated, metadata),
        )
        if not tasks:
            report.skipped.append("没有要下载的图片（全部关闭或源没给图）")
            return report

        try:
            self._ctx.storage.mkdir(metadata_dir)
        except StorageError as exc:
            report.failed.append(f"元数据目录不可写: {exc}")
            return report

        semaphore = asyncio.Semaphore(config.concurrency)

        async def one(task: ImageTask) -> None:
            async with semaphore:
                await self._download_one(task, metadata_dir, report, aggregated, config, overwrite)

        await asyncio.gather(*(one(task) for task in tasks), return_exceptions=True)
        return report

    def _poster_fallbacks(self, aggregated: AggregatedMetadata, metadata: MediaMetadata) -> list[str]:
        """其它源给的海报，按路由顺序去重。

        海报是 Emby 列表里的封面，**没有它条目就没有脸** —— 所以宁可多留几张候选：
        当前那张偶尔下不下来（网络抖动），或者站点换过图导致原地址失效，
        有退路就不会整条没有封面。
        """
        seen = {metadata.poster_url}
        fallbacks: list[str] = []
        for result in aggregated.sources:
            url = result.metadata.poster_url if result.metadata else None
            if url and url not in seen:
                seen.add(url)
                fallbacks.append(url)
        return fallbacks

    def _referer_for_url(
        self, url: str, aggregated: AggregatedMetadata, field_name: str
    ) -> str | None:
        r"""图片地址通常校验同源 Referer，带上来源站主页。

        **先看图片自己的域名是不是某个源的主页域名** —— getchu 就是这样：

            https://www.getchu.com/brandnew/<id>/c<id>sample1.jpg
            不带 Referer          -> 403
            带 bgm.tv 的 Referer  -> 403
            带 getchu 的 Referer  -> 200

        而它的图片域名正好等于它的主页域名，所以按域名匹配就够。

        图片放在**别的**域名上时（javdb 的 c0.jdbstatic.com）匹配不到，
        退回"谁上报了这个字段"，取它的主页。
        """
        from urllib.parse import urlparse

        from server.sources import all_sources

        host = (urlparse(url).hostname or "").lower()
        if host:
            for plugin in all_sources():
                home = plugin.descriptor.homepage or ""
                if home and (urlparse(home).hostname or "").lower() == host:
                    return home
        return self._referer_for(aggregated, field_name)

    def _referer_for(self, aggregated: AggregatedMetadata, field_name: str) -> str | None:
        from server.sources import get as get_source

        origin = aggregated.field_sources.get(field_name)
        if not origin:
            return None
        # 列表字段的来源是逗号拼起来的（"javdb,freejavbt"）—— 以前直接拿整串去查，
        # 查不到就返回 None，等于背景图从来不带 Referer。取第一个认得的。
        for source_id in origin.split(","):
            plugin = get_source(source_id.strip())
            if plugin is not None:
                return plugin.descriptor.homepage or None
        return None

    async def _download_one(
        self,
        task: ImageTask,
        metadata_dir: Path,
        report: ImageReport,
        aggregated: AggregatedMetadata,
        config: ImageDownloadConfig,
        overwrite: bool,
    ) -> None:
        target = metadata_dir / task.relative_path
        if target.exists() and not overwrite:
            report.skipped.append(f"{task.kind}: 已存在")
            return

        field_name = _REFERER_FIELD.get(task.kind, "poster_url")
        rejected: list[str] = []
        for url in task.candidates:
            # Referer 要**逐候选**算：同一张图的候选可能来自不同站点
            # （getchu 的剧照 + 封面的兜底），一个站点一个规矩。
            referer = self._referer_for_url(url, aggregated, field_name)
            try:
                data = await self._ctx.http.get_bytes(url, source=task.kind, referer=referer)
            except Exception as exc:  # noqa: BLE001 - 试下一张候选
                logger.debug("图片候选失败 %s: %s", url, exc)
                continue
            if not data:
                continue

            if (
                task.kind == "fanart"
                and config.fanart_min_width > 0
                and url not in task.min_width_exempt
            ):
                size = image_size(data)
                if size is not None:
                    if size[0] < config.fanart_min_width:
                        rejected.append(f"{size[0]}x{size[1]}")
                        continue
                    if size[1] > size[0]:
                        # 竖版图当背景图会被拉伸或裁掉大半。getchu 的剧照是**混着**的：
                        # 同一部作品 sample1 是 715x800 竖版、sample3 是 800x450 横版 ——
                        # 只取第一张的话，背景图还是竖的，只是不再是封面的复制品而已。
                        rejected.append(f"{size[0]}x{size[1]} 竖版")
                        continue

            try:
                report.written.append(self._ctx.storage.write_bytes(target, data))
            except StorageError as exc:
                report.failed.append(f"{task.kind}: 写入失败 {exc}")
            return

        if rejected:
            report.skipped.append(
                f"{task.kind}: 候选都不合格（要 >= {config.fanart_min_width}px 且横版）："
                f"{', '.join(rejected)}"
            )
        else:
            report.failed.append(f"{task.kind}: {len(task.candidates)} 个候选全部失败")


__all__ = ["ImageDownloader", "ImageReport", "ImageTask", "plan_images"]
