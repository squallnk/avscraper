"""扫描 -> 分类 -> 刮削 -> （可选）落盘。

安全约定：
- 扫描只读；
- 写 NFO/图片/POSTER 只在 `metadata_dir` 下，且必须 `organize_enabled=True` 且 `dry_run=False`；
- 移动视频只在 `organize_enabled=True` 时发生，且一定经 `SerialQueue` 限速；
- 任何一步失败都只影响当前文件，绝不中断整批。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from server.classify import classify, is_video_file
from server.matching import episode_conflicts, query_matches_metadata
from server.models import (
    AggregatedMetadata,
    ContentType,
    MediaMetadata,
    ScrapeRecord,
    ScrapeStatus,
    StorageProvider,
)
from server.nfo import build_episode_nfo, build_movie_nfo, build_tvshow_nfo
from server.organize import Organizer
from server.sources import FetchContext
from server.storage import StorageError

if TYPE_CHECKING:
    from server.app import AppContext
    from server.tasks import Task

logger = logging.getLogger(__name__)

MIN_SIZE_MB_DEFAULT = 50


async def scrape_one(
    ctx: AppContext,
    path: Path,
    *,
    query_override: str | None = None,
    source_pin: str | None = None,
    force_success: bool = False,
) -> ScrapeRecord:
    """刮削单个文件并写记录。不落盘。

    后三个参数是给"人工重刮"用的：自动跑的时候一个都不用。
    - `query_override`：换一个关键词重搜（清洗规则猜错时的出口）；
    - `source_pin`：只查某一个源；
    - `force_success`：跳过匹配校验 —— 人在界面上看着预览结果点确认，
      比机器猜得准。这时会在 `error` 里留一条"人工确认"的痕迹，便于回溯。
    """
    match = classify(path)

    # 没有番号时（里番、国产等）查询只能用作品名。
    # **必须清洗**：直接拿 path.stem 会把日期、制作组、集数、副标题、语言后缀
    # 一起丢给站点，一个都搜不到。
    query = None
    if query_override:
        query = query_override
    elif not match.number:
        from server.cleaner import clean_query_name

        query = clean_query_name(path.name) or path.stem

    ctx_info = FetchContext(
        number=match.number,
        content_type=match.content_type,
        query=query,
        path=str(path),
    )

    record = ScrapeRecord(
        id=_record_id(path),
        path=str(path),
        provider=StorageProvider.LOCAL,
        number=match.number,
        content_type=match.content_type,
        season=match.season,
        episode=match.episode,
        cd=match.cd,
        episode_source=match.episode_source,
    )

    if match.content_type is ContentType.UNKNOWN and not match.number:
        record.status = ScrapeStatus.SKIPPED
        record.error = "无法识别番号或内容类型：" + "; ".join(match.evidence)
        await ctx.db.upsert_record(record)
        return record

    route_override = dict(ctx.config.route_override)
    if source_pin:
        # 只查这一个源：把该内容类型的路由整个换掉。
        # 字段优先级随后作用在这条单元素路由上，不会把别的源又拉回来。
        route_override[match.content_type.value] = [source_pin]

    aggregated: AggregatedMetadata = await ctx.aggregator.run(
        ctx_info,
        field_priority=ctx.config.field_priority,
        route_override=route_override,
        use_cache=True,
        cache_lookup=ctx.db.get_snapshot,
        cache_store=ctx.db.save_snapshot,
    )

    record.metadata = aggregated.metadata
    record.field_sources = aggregated.field_sources
    has_any = any(
        [
            aggregated.metadata.title,
            aggregated.metadata.actors,
            aggregated.metadata.poster_url,
        ]
    )

    grabbed = (aggregated.metadata.title or "")[:60]

    # 分集兜底（CD1/CD2 那类）出来的集号本身就不牢靠 —— 它常常是同一集被切成
    # 两半，作者拿 CD2 当第二集用。拿它去比标题里的卷号只会误判，所以不参与比对。
    episode_for_check = None if match.episode_source == "分集兜底" else match.episode
    conflict = episode_conflicts(episode_for_check, aggregated.metadata)

    if not has_any:
        record.status = ScrapeStatus.NOT_FOUND
        failures = [f"{r.source}: {r.failure_reason}" for r in aggregated.sources]
        record.error = "所有源均未命中（" + ", ".join(failures) + "）"
    elif query and not force_success and not query_matches_metadata(query, aggregated.metadata):
        # 站点搜索是模糊的：库里没有这部作品时会返回"最像的"一条，
        # 通常是毫不相干的片子。这种结果不能当成功写进媒体库。
        record.status = ScrapeStatus.NEED_SELECTION
        record.error = f"抓到的结果与查询不符，需要人工确认。查询「{query}」，抓回标题「{grabbed}」"
    elif not force_success and conflict is not None:
        # 查询词确实是标题的子串，但集/卷号对不上 —— 抓的是同一部作品的另一卷。
        # 这种错配比"完全不相干"更隐蔽：标题看着像对的，封面和简介却是别人的。
        record.status = ScrapeStatus.NEED_SELECTION
        record.error = (
            f"抓到的结果不是这一卷，需要人工确认。文件是第 {match.episode} 集，"
            f"抓回标题是第 {conflict} 集：「{grabbed}」"
        )
    else:
        record.status = ScrapeStatus.SUCCESS
        if force_success and query and (
            not query_matches_metadata(query, aggregated.metadata) or conflict is not None
        ):
            record.error = f"人工确认后采用：查询「{query}」，抓回标题「{grabbed}」"

    await ctx.db.upsert_record(record)
    return record


def _record_id(path: Path) -> str:
    import hashlib

    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]


def _is_dedicated_folder(video_path: Path) -> bool:
    """视频所在目录是不是"这一部作品独占"。

    这个判据决定**用哪种 NFO 结构**，不是随便挑的：

    - **独占目录**（目录里只有这一个视频）→ 这是"一部作品/剧集"结构：
      写 `tvshow.nfo`（剧集级）+ `{视频名}.nfo`（episodedetails）。
    - **扁平目录**（一堆不同作品混放，例如"按月归档"的月份文件夹）→
      每部作品都是独立的，写电影结构 `{视频名}.nfo`（movie）。

    写反了都会出问题：扁平目录里写 tvshow.nfo 会被反复覆盖；
    而把一集写成 movie，Emby 在有剧集结构时又读不到。
    """
    try:
        siblings = [f for f in video_path.parent.iterdir() if is_video_file(f.name)]
    except OSError:
        return False
    return len(siblings) <= 1


async def write_metadata(
    ctx: AppContext,
    *,
    metadata: MediaMetadata,
    aggregated: AggregatedMetadata,
    metadata_dir: Path,
    video_path: Path | None = None,
    season: int | None = None,
    episode: int | None = None,
    overwrite_images: bool | None = None,
) -> list[Path]:
    """写 NFO 与图片到 metadata_dir。返回写出的文件列表。

    **文件名主干取视频文件名，不取番号** —— 里番没有番号，若用 `number or "unknown"`
    会让同一目录下所有文件都写成 `unknown.nfo`，互相覆盖。

    写入未开启（`dry_run` 或 `organize_enabled=false`）时直接返回空列表，不碰磁盘。
    """
    if not (ctx.config.organize_enabled and not ctx.config.dry_run):
        return []

    written: list[Path] = []
    stem = video_path.stem if video_path is not None else (metadata.number or "unknown")

    try:
        ctx.storage.mkdir(metadata_dir)
    except StorageError as exc:
        logger.warning("元数据目录不可写: %s", exc)
        return []

    try:
        as_episode = (
            aggregated.content_type is ContentType.JANIME
            and video_path is not None
            and _is_dedicated_folder(video_path)
            and episode is not None
        )
        if as_episode:
            written.append(
                ctx.storage.write_text(metadata_dir / "tvshow.nfo", build_tvshow_nfo(aggregated))
            )
            written.append(
                ctx.storage.write_text(
                    metadata_dir / f"{stem}.nfo",
                    build_episode_nfo(aggregated, season=season or 1, episode=episode),
                )
            )
        else:
            written.append(
                ctx.storage.write_text(metadata_dir / f"{stem}.nfo", build_movie_nfo(aggregated))
            )
    except StorageError as exc:
        logger.warning("写 NFO 失败: %s", exc)

    from server.images import ImageDownloader

    report = await ImageDownloader(ctx).run(
        metadata=metadata,
        aggregated=aggregated,
        metadata_dir=metadata_dir,
        stem=stem,
        overwrite=overwrite_images,
    )
    written.extend(report.written)
    if report.failed:
        logger.warning("部分图片下载失败: %s", report.failed)
    return written


async def write_record_metadata(
    ctx: AppContext,
    *,
    record: ScrapeRecord,
    video_path: Path,
    metadata_dir: Path,
    overwrite_images: bool | None = None,
) -> list[Path]:
    """把一条记录落盘成 NFO + 图片。

    批量任务和"人工重刮后写入"走的是同一条路，
    免得两边各写一套、最后只有一边修了 bug。

    `overwrite_images` 留空表示跟随配置；人工重刮传 True ——
    那一次的前提就是"上一条结果不对"，旧封面必须被换掉。
    """
    if record.metadata is None:
        return []
    return await write_metadata(
        ctx,
        metadata=record.metadata,
        aggregated=AggregatedMetadata(
            number=record.number,
            content_type=record.content_type,
            metadata=record.metadata,
            field_sources=record.field_sources,
        ),
        metadata_dir=metadata_dir,
        video_path=video_path,
        season=record.season,
        episode=record.episode,
        overwrite_images=overwrite_images,
    )


async def run_scan_and_scrape(ctx: AppContext, task: Task) -> None:
    """任务处理器：扫描允许根目录下的视频并逐个刮削。"""
    roots = [Path(p) for p in task.payload.get("roots", [])] or ctx.settings.allowed_roots
    recursive = bool(task.payload.get("recursive", True))
    limit = int(task.payload.get("limit", 0))

    files: list[Path] = []
    for root in roots:
        files.extend(ctx.storage.iter_videos(root, recursive=recursive))

    succeeded = await ctx.db.succeeded_paths()
    skip_known = bool(task.payload.get("skip_known", True))
    if skip_known:
        files = [f for f in files if str(f) not in succeeded]
    if limit > 0:
        files = files[:limit]

    task.total = len(files)
    task.message = f"待刮削 {len(files)} 个文件"
    succeeded = 0
    failed = 0
    skipped = 0
    # 「需要人工确认」和「哪都没有」都不是失败 —— 之前它们和真失败一起算进
    # "失败"，于是"成功 0，失败 1"读起来像运行出错了，其实只是有一条要你拍板。
    pending = 0
    missing = 0

    for index, path in enumerate(files, start=1):
        task.current = index
        task.message = path.name
        try:
            record = await scrape_one(ctx, path)
        except Exception as exc:  # noqa: BLE001 - 单文件失败不中断整批
            logger.exception("刮削失败: %s", path)
            failed += 1
            task.result.setdefault("errors", []).append(f"{path.name}: {exc}")
            continue

        if record.status is ScrapeStatus.SUCCESS:
            succeeded += 1
            if record.metadata and task.payload.get("write_metadata"):
                # 默认写在视频旁边 —— `.metadata/` 子目录 Emby 不认，等于白写
                await write_record_metadata(
                    ctx,
                    record=record,
                    video_path=path,
                    metadata_dir=Path(
                        task.payload.get("metadata_dir")
                        or ctx.config.metadata_dir
                        or path.parent
                    ),
                )
        elif record.status is ScrapeStatus.NEED_SELECTION:
            pending += 1
        elif record.status is ScrapeStatus.NOT_FOUND:
            missing += 1
        elif record.status is ScrapeStatus.SKIPPED:
            skipped += 1
        else:
            failed += 1

    task.result.update(
        {
            "succeeded": succeeded,
            "need_selection": pending,
            "not_found": missing,
            "failed": failed,
            "skipped": skipped,
        }
    )
    task.message = (
        f"完成：成功 {succeeded}，待确认 {pending}，未命中 {missing}，"
        f"失败 {failed}，跳过 {skipped}"
    )


def make_organizer(ctx: AppContext) -> Organizer:
    return Organizer(
        ctx.storage,
        directory_template=ctx.config.directory_template,
        filename_template=ctx.config.filename_template,
    )


__all__ = [
    "MIN_SIZE_MB_DEFAULT",
    "is_video_file",
    "make_organizer",
    "run_scan_and_scrape",
    "scrape_one",
    "write_metadata",
    "write_record_metadata",
]
