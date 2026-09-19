"""HTTP 路由。

约定：
- 所有写操作（改配置、启动任务）都要显式 `confirm=true`，避免误触；
- `/api/classify` 只做解析不联网，用来在 WebUI 上先确认分类对不对；
- 错误统一返回 `{"detail": "..."}`，不泄漏栈。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from server import build_info
from server.classify import classify
from server.config import ImageDownloadConfig, Settings
from server.models import CONTENT_TYPE_LABELS, ScrapeStatus, StorageProvider
from server.organize import TemplateError, render
from server.sources import all_sources
from server.sources import get as source_by_id
from server.storage import OutsideAllowedRoots, StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def get_ctx(request: Request) -> Any:
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪")
    return ctx


# ---------------------------------------------------------------- 系统


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    return {
        "status": "ok",
        **build_info(),
        "data_dir": str(ctx.settings.data_dir),
        "allowed_roots": [str(r) for r in ctx.storage.guard.roots],
        "writes_enabled": ctx.storage.writes_enabled,
        "dry_run": ctx.config.dry_run,
        "organize_enabled": ctx.config.organize_enabled,
        "queue_depth": ctx.runner.depth,
        "sources": len(all_sources()),
    }


@router.get("/settings")
async def read_settings(request: Request) -> Settings:
    return get_ctx(request).settings


# ---------------------------------------------------------------- 配置


class ConfigPatch(BaseModel):
    """运行期配置的部分更新。只给要改的字段。"""

    dry_run: bool | None = None
    organize_enabled: bool | None = None
    file_op_rate: float | None = Field(default=None, gt=0, le=60)
    file_op_burst: int | None = Field(default=None, ge=1, le=100)
    http_rate: float | None = Field(default=None, gt=0, le=60)
    http_burst: int | None = Field(default=None, ge=1, le=100)
    cooldown_seconds: float | None = Field(default=None, ge=1, le=3600)
    failure_threshold: int | None = Field(default=None, ge=1, le=20)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    request_timeout: float | None = Field(default=None, ge=1, le=180)
    proxy: str | None = None
    enabled_sources: list[str] | None = None
    field_priority: dict[str, list[str]] | None = None
    route_override: dict[str, list[str]] | None = None
    directory_template: str | None = None
    filename_template: str | None = None
    metadata_dir: str | None = None
    cd2_mappings: list[list[str]] | None = None
    webhook_enabled: bool | None = None
    webhook_token: str | None = None
    webhook_debounce_seconds: float | None = Field(default=None, ge=0, le=600)
    webhook_max_subtree_files: int | None = Field(default=None, ge=1, le=100000)
    webhook_auto_scrape: bool | None = None
    source_cookies: dict[str, str] | None = None
    user_agent: str | None = None
    images: ImageDownloadConfig | None = None


@router.get("/config")
async def read_config(request: Request) -> dict[str, Any]:
    """密钥字段一律掩码回显，明文只留在服务端。"""
    return get_ctx(request).config.redacted()


@router.put("/config")
async def write_config(patch: ConfigPatch, request: Request) -> dict[str, Any]:
    """部分更新。密钥字段传掩码表示"不改"，传空串表示"清除"。"""
    ctx = get_ctx(request)
    try:
        updated = ctx.config.with_patch(patch.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await ctx.save_config(updated)
    return ctx.config.redacted()


@router.get("/sources/{source_id}/cookie")
async def read_source_cookie(source_id: str, request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    plugin = source_by_id(source_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="未知数据源")
    value = ctx.config.source_cookies.get(source_id, "")
    return {
        "source": source_id,
        "needs_cookie": plugin.descriptor.needs_cookie,
        "configured": bool(value),
        "length": len(value),
    }


@router.get("/sources/{source_id}/cookie/verify")
async def verify_source_cookie(source_id: str, request: Request) -> dict[str, Any]:
    """用该源真实跑一次查询，判断 Cookie 到底有没有生效。

    这是排障用的：Cookie 填了但抓不到时，需要区分
    「Cookie 无效」和「站点本来就没这个条目」。
    """
    ctx = get_ctx(request)
    plugin = source_by_id(source_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="未知数据源")

    probe = plugin.descriptor.cookie_probe_query
    if not probe:
        return {"supported": False, "detail": "该源不支持 Cookie 验证"}

    from server.models import ContentType
    from server.sources.base import FetchContext, SourceBlocked

    context = FetchContext(query=probe, content_type=ContentType.JANIME)
    try:
        metadata = await plugin.fetch(ctx.http, context)
    except SourceBlocked as exc:
        return {"supported": True, "ok": False, "reason": "blocked", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 验证接口就是把失败报出来
        return {
            "supported": True,
            "ok": False,
            "reason": type(exc).__name__,
            "detail": str(exc)[:300],
        }

    return {
        "supported": True,
        "ok": metadata is not None,
        "reason": "" if metadata else "not_found",
        "detail": "Cookie 有效" if metadata else "请求成功但没有结果，可能是关键词不匹配",
    }


class CookieRequest(BaseModel):
    value: str


@router.put("/sources/{source_id}/cookie")
async def write_source_cookie(
    source_id: str, payload: CookieRequest, request: Request
) -> dict[str, Any]:
    """单独设某个源的 Cookie。传空串等于清除。"""
    ctx = get_ctx(request)
    plugin = source_by_id(source_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="未知数据源")
    if not plugin.descriptor.needs_cookie and payload.value:
        # 不是错误，只是提醒：这个源本来不需要 Cookie
        logger.info("源 %s 未声明需要 Cookie，仍然按请求设置", source_id)
    updated = ctx.config.with_patch({"source_cookies": {source_id: payload.value}})
    await ctx.save_config(updated)
    return {"source": source_id, "configured": bool(ctx.config.source_cookies.get(source_id))}


# ---------------------------------------------------------------- CloudDrive2 webhook


@router.post("/webhooks/clouddrive", status_code=204)
async def clouddrive_webhook(
    payload: dict,
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    """接收 CD2 的文件变更通知。

    契约：
    - 鉴权 `Authorization: Bearer <webhook_token>`（token 为空时不校验，仅供本机调试）；
    - **立即返回 204**，解析与扫描放后台，避免 FUSE 子树扫描堵住 CD2；
    - 载荷里的路径是 **CD2 虚拟路径**，不是宿主路径。
    """
    ctx = get_ctx(request)
    if not ctx.config.webhook_enabled:
        raise HTTPException(status_code=403, detail="webhook 未启用（webhook_enabled=false）")

    token = ctx.config.webhook_token
    if token:
        provided = (authorization or "").removeprefix("Bearer ").strip()
        if provided != token:
            raise HTTPException(status_code=401, detail="webhook 令牌不正确")

    from server.webhook import parse_payload

    events, warnings = parse_payload(payload)
    for warning in warnings:
        logger.warning("CD2 webhook: %s", warning)
    if events:
        processor = getattr(request.app.state, "webhook", None)
        if processor is None:
            raise HTTPException(status_code=503, detail="webhook 处理器未初始化")
        processor.schedule(events)
    return Response(status_code=204)


@router.get("/webhooks/status")
async def webhook_status(request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    processor = getattr(request.app.state, "webhook", None)
    return {
        "enabled": ctx.config.webhook_enabled,
        "token_required": bool(ctx.config.webhook_token),
        "debounce_seconds": ctx.config.webhook_debounce_seconds,
        "auto_scrape": ctx.config.webhook_auto_scrape,
        "mappings": ctx.config.cd2_mappings,
        "pending_directories": processor.debouncer.pending if processor else 0,
    }


# ---------------------------------------------------------------- 解析


class ClassifyRequest(BaseModel):
    path: str


@router.post("/classify")
async def classify_path(payload: ClassifyRequest) -> dict[str, Any]:
    result = classify(payload.path)
    return {
        "number": result.number,
        "content_type": result.content_type.value,
        "content_type_label": CONTENT_TYPE_LABELS.get(result.content_type, ""),
        "season": result.season,
        "episode": result.episode,
        "cd": result.cd,
        "episode_source": result.episode_source,
        "confidence": result.confidence,
        "evidence": result.evidence,
    }


class RenderRequest(BaseModel):
    template: str
    data: dict[str, str] = Field(default_factory=dict)


@router.post("/render-template")
async def render_template(payload: RenderRequest) -> dict[str, str]:
    try:
        return {"result": render(payload.template, payload.data)}
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------- 源


@router.get("/sources")
async def list_sources(request: Request) -> list[dict[str, Any]]:
    ctx = get_ctx(request)
    enabled = set(ctx.config.enabled_sources) if ctx.config.enabled_sources else None
    return [
        {
            **plugin.descriptor.model_dump(mode="json"),
            "active": enabled is None or plugin.id in enabled,
        }
        for plugin in all_sources()
    ]


@router.get("/sources/probe")
async def probe_sources(request: Request) -> list[dict[str, Any]]:
    """逐个源探测连通性。用于首次部署时确认容器能出去。"""
    import socket

    ctx = get_ctx(request)
    report: list[dict[str, Any]] = []
    for plugin in all_sources():
        homepage = plugin.descriptor.homepage
        if not homepage:
            continue
        host = homepage.split("//", 1)[-1].split("/", 1)[0]
        try:
            resolved: str = socket.gethostbyname(host)
        except OSError as exc:
            resolved = f"解析失败: {exc}"
        entry: dict[str, Any] = {"source": plugin.id, "host": host, "resolved": resolved}
        try:
            result = await ctx.http.get(homepage, source=plugin.id)
            entry.update(
                {"ok": result.status == 200, "status": result.status,
                 "elapsed_ms": result.elapsed_ms, "bytes": len(result.content)}
            )
        except Exception as exc:  # noqa: BLE001 - 探测就是把失败报出来
            entry.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        report.append(entry)
    return report


@router.get("/sources/health")
async def sources_health(request: Request) -> dict[str, Any]:
    return get_ctx(request).http.status()


class ClearCacheRequest(BaseModel):
    """清源快照缓存。`source` 留空表示全清。"""

    source: str | None = None
    confirm: bool = False


@router.post("/sources/cache/clear")
async def clear_source_cache(payload: ClearCacheRequest, request: Request) -> dict[str, Any]:
    """清掉源快照缓存。

    正常不用手动清 —— 改了源解析记得把 `SOURCE_CACHE_VERSION` +1，旧快照会自动失效。
    这个口子留给另一种情况：**站点把页面的内容改了而代码没动**
    （换了封面、补了剧照），不清缓存就永远拿旧的。
    """
    ctx = get_ctx(request)
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true 才会清缓存")
    if payload.source and source_by_id(payload.source) is None:
        raise HTTPException(status_code=404, detail="未知数据源")
    deleted = await ctx.db.clear_snapshots(payload.source)
    logger.info("清源快照缓存 %d 条（source=%s）", deleted, payload.source or "全部")
    return {"deleted": deleted}


# ---------------------------------------------------------------- 任务


class ScanRequest(BaseModel):
    roots: list[str] = Field(default_factory=list)
    recursive: bool = True
    limit: int = 0
    skip_known: bool = True
    write_metadata: bool = False
    overwrite_images: bool = False
    """已有同名图片时重新下载。默认 false（跳过），因为重下费流量。"""
    metadata_dir: str | None = None
    confirm: bool = False


@router.post("/tasks/scan")
async def start_scan(payload: ScanRequest, request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true 才会启动任务")
    roots = payload.roots or [str(r) for r in ctx.settings.allowed_roots]
    if not roots:
        raise HTTPException(status_code=400, detail="未配置任何允许根目录")
    for root in roots:
        try:
            ctx.storage.guard.check(root)
        except OutsideAllowedRoots as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    task = await ctx.runner.submit(
        "scan_and_scrape",
        {
            "roots": roots,
            "recursive": payload.recursive,
            "limit": payload.limit,
            "skip_known": payload.skip_known,
            "write_metadata": payload.write_metadata,
            "overwrite_images": payload.overwrite_images,
            "metadata_dir": payload.metadata_dir,
        },
    )
    return {"task_id": task.id, "state": task.state}


@router.get("/tasks")
async def list_tasks(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    ctx = get_ctx(request)
    return [
        {
            "id": t.id,
            "kind": t.kind,
            "state": t.state,
            "message": t.message,
            "current": t.current,
            "total": t.total,
            "error": t.error,
            "result": t.result,
        }
        for t in ctx.runner.history(limit)
    ]


# ---------------------------------------------------------------- 记录


@router.get("/records")
async def list_records(
    request: Request, status: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    ctx = get_ctx(request)
    parsed = None
    if status:
        try:
            parsed = ScrapeStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"未知状态: {status}") from exc
    records = await ctx.db.list_records(status=parsed, limit=limit)
    return [r.model_dump(mode="json") for r in records]


@router.get("/records/{record_id}")
async def get_record(record_id: str, request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    record = await ctx.db.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return record.model_dump(mode="json")


class RescanRequest(BaseModel):
    """人工重刮。默认只预览，不碰磁盘。"""

    query: str | None = None
    """换一个关键词重搜。留空表示沿用原查询。"""

    source: str | None = None
    """只查这一个源。留空表示按正常路由。"""

    force: bool = False
    """跳过自动匹配校验 —— 你看着预览结果点确认，比机器猜得准。"""

    write: bool = False
    """是否立即写 NFO 与图片。"""

    metadata_dir: str | None = None
    confirm: bool = False
    """`write=true` 时必须显式确认，与其它写操作一致。"""


@router.post("/records/{record_id}/rescan")
async def rescan_record(
    record_id: str, payload: RescanRequest, request: Request
) -> dict[str, Any]:
    """按新的查询词/指定源重刮一条记录，可先预览再写。

    这是 `need_selection` 的出口：自动匹配说不准时，不该直接写进媒体库，
    也不该就此卡住 —— 由人换词、指定源，看着预览结果拍板。
    """
    from pathlib import Path as _Path

    from server.pipeline import scrape_one, write_record_metadata

    ctx = get_ctx(request)
    record = await ctx.db.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    if payload.source:
        plugin = source_by_id(payload.source)
        if plugin is None:
            raise HTTPException(status_code=404, detail="未知数据源")
        enabled = ctx.config.enabled_sources
        if enabled and payload.source not in enabled:
            raise HTTPException(
                status_code=422,
                detail=f"源 {payload.source} 已在设置里被禁用，启用后再试",
            )

    if payload.write and not payload.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true 才会写入文件")

    path = _Path(record.path)
    try:
        fresh = await scrape_one(
            ctx,
            path,
            query_override=(payload.query or "").strip() or None,
            source_pin=payload.source,
            force_success=payload.force,
        )
    except Exception as exc:  # noqa: BLE001 - 把失败原文报给界面，便于排查
        raise HTTPException(status_code=502, detail=f"重刮失败: {exc}") from exc

    written: list[str] = []
    if payload.write and fresh.status is ScrapeStatus.SUCCESS:
        target = _Path(payload.metadata_dir or ctx.config.metadata_dir or path.parent)
        try:
            written = [str(p) for p in await write_record_metadata(
                ctx,
                record=fresh,
                video_path=path,
                metadata_dir=target,
                # 人工重刮的前提就是"上一条结果不对" —— 旧封面必须被换掉，
                # 否则 NFO 是新的、图还是旧的，看起来像修好了其实没有。
                overwrite_images=True,
            )]
        except Exception as exc:  # noqa: BLE001 - 同上
            raise HTTPException(status_code=502, detail=f"写元数据失败: {exc}") from exc

    return {
        "record": fresh.model_dump(mode="json"),
        "written": written,
        "writes_enabled": ctx.storage.writes_enabled,
    }


class PurgeRequest(BaseModel):
    """删除刮削记录。条件之间是 AND，**至少要给一个**。"""

    ids: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    root: str | None = None
    """按目录前缀删（只删该目录及其子目录下的记录）。"""

    confirm: bool = False


@router.post("/records/purge")
async def purge_records(payload: PurgeRequest, request: Request) -> dict[str, Any]:
    """删记录，让这些文件下次扫描时重新变回"没刮过"。

    删记录是**唯一**能把一条错配（或不需要的）结果从库里清掉的办法 ——
    否则它一直是 `success`，扫描按设计会永远跳过它。

    只动数据库，不碰磁盘：已经写出去的 NFO 与图片不会因此消失。
    """
    ctx = get_ctx(request)
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true 才会删除记录")

    parsed: list[str] = []
    for value in payload.statuses:
        try:
            parsed.append(ScrapeStatus(value).value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"未知状态: {value}") from exc

    try:
        deleted = await ctx.db.delete_records(
            ids=payload.ids, statuses=parsed, root=payload.root
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info("删除刮削记录 %d 条（ids=%d statuses=%s root=%s）",
                deleted, len(payload.ids), parsed, payload.root)
    return {"deleted": deleted}


# ---------------------------------------------------------------- 日志


@router.get("/logs")
async def list_logs(request: Request, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
    return await get_ctx(request).db.list_logs(limit=limit, level=level)


# ---------------------------------------------------------------- 文件浏览（只读）


@router.get("/browse")
async def browse(request: Request, path: str) -> dict[str, Any]:
    ctx = get_ctx(request)
    try:
        entries = ctx.storage.list_dir(Path(path))
    except StorageError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "path": path,
        "provider": StorageProvider.LOCAL.value,
        "entries": [
            {
                "name": entry.name,
                "path": str(entry),
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else 0,
            }
            for entry in entries
        ],
    }
