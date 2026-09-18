"""字段级多源聚合。

规则（照抄这个领域被两个独立项目验证过的做法）：
1. **内容类型路由**是"这个类型该请求哪些站点"的真值，不在表里的站不会被请求；
2. **字段优先级**把路由里的一部分提前，**字段黑名单**把一部分删掉；
3. 标量字段满足即短路，不再请求后面的站；
4. 列表类字段（演员、标签、图片）在全部请求结束后按字段链拼接，不按返回先后排序；
5. 每个字段记录来源，WebUI 可直接展示"这个标题来自哪个站"，出问题不用猜。
"""

from __future__ import annotations

import time
from typing import Any

from server.models import (
    AggregatedMetadata,
    ContentType,
    MediaMetadata,
    SourceResult,
)
from server.sources import FetchContext
from server.sources import get as get_source
from server.sources import resolve as resolve_sources
from server.sources.base import SourceError

# 未命中任何路由的字段按此顺序取值
DEFAULT_ROUTES: dict[ContentType, list[str]] = {
    ContentType.CENSORED: ["javbus", "javdb", "freejavbt"],
    ContentType.UNCENSORED: ["javdb", "javbus", "freejavbt"],
    ContentType.AMATEUR: ["javdb", "javbus", "freejavbt"],
    ContentType.FC2: ["javdb", "freejavbt"],
    ContentType.CHINESE: ["javdb", "freejavbt"],
    ContentType.WESTERN: ["javdb", "freejavbt"],
    # 里番没有番号，getchu 是第一源（商品是数字 id，靠路径关键词分类命中）
    ContentType.JANIME: ["getchu", "javdb", "freejavbt"],
    ContentType.UNKNOWN: ["javdb", "javbus", "freejavbt"],
}

SCALAR_FIELDS: tuple[str, ...] = (
    "title",
    "original_title",
    "plot",
    "director",
    "studio",
    "publisher",
    "series",
    "release_date",
    "year",
    "runtime",
    "score",
    "poster_url",
    "trailer_url",
    "website",
)

LIST_FIELDS: tuple[str, ...] = ("actors", "tags", "thumb_urls", "fanart_urls")

ALL_FIELDS: tuple[str, ...] = (*SCALAR_FIELDS, *LIST_FIELDS)


def build_chains(
    content_type: ContentType,
    *,
    field_priority: dict[str, list[str]] | None = None,
    field_blacklist: dict[str, list[str]] | None = None,
    route_override: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """把路由与优先级编译成「字段 -> 站点链」。"""
    route = (route_override or {}).get(content_type.value) or DEFAULT_ROUTES.get(
        content_type, DEFAULT_ROUTES[ContentType.UNKNOWN]
    )
    blacklist = field_blacklist or {}
    priority = field_priority or {}

    chains: dict[str, list[str]] = {}
    for field in ALL_FIELDS:
        blocked = set(blacklist.get(field, []))
        chain = [sid for sid in route if sid not in blocked]
        preferred = [sid for sid in priority.get(field, []) if sid in route and sid not in blocked]
        rest = [sid for sid in chain if sid not in preferred]
        chains[field] = [*preferred, *rest]
    return chains


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


class Aggregator:
    """按字段链请求站点并合并结果。"""

    def __init__(self, http: Any, *, enabled_sources: list[str] | None = None) -> None:
        self._http = http
        self._enabled = enabled_sources or []

    def _candidates(self, chains: dict[str, list[str]]) -> list[str]:
        """本次实际会请求的站点，按首次出现顺序去重。"""
        order: list[str] = []
        for field in ALL_FIELDS:
            for sid in chains.get(field, []):
                if sid not in order:
                    order.append(sid)
        available = {p.id for p in resolve_sources(self._enabled)}
        return [sid for sid in order if sid in available]

    async def run(
        self,
        ctx: FetchContext,
        *,
        field_priority: dict[str, list[str]] | None = None,
        field_blacklist: dict[str, list[str]] | None = None,
        route_override: dict[str, list[str]] | None = None,
        use_cache: bool = False,
        cache_lookup: Any = None,
        cache_store: Any = None,
    ) -> AggregatedMetadata:
        chains = build_chains(
            ctx.content_type,
            field_priority=field_priority,
            field_blacklist=field_blacklist,
            route_override=route_override,
        )
        candidates = self._candidates(chains)

        merged = MediaMetadata(number=ctx.number)
        field_sources: dict[str, str] = {}
        results: list[SourceResult] = []
        fetched: dict[str, MediaMetadata | None] = {}

        def satisfied() -> bool:
            return all(not _is_blank(getattr(merged, f, None)) for f in SCALAR_FIELDS)

        for source_id in candidates:
            if satisfied():
                break
            plugin = get_source(source_id)
            if plugin is None:
                continue

            cache_key = plugin.cache_key(ctx)
            started = time.monotonic()
            cached = False
            metadata: MediaMetadata | None = None
            error: str | None = None
            reason: str | None = None

            if use_cache and cache_lookup is not None and cache_key:
                snapshot = await cache_lookup(source_id, cache_key)
                if snapshot:
                    try:
                        metadata = MediaMetadata.model_validate(snapshot)
                        cached = True
                    except (ValueError, TypeError):
                        metadata = None

            if metadata is None:
                try:
                    metadata = await plugin.fetch(self._http, ctx)
                except SourceError as exc:
                    error = str(exc)
                    reason = exc.reason
                except Exception as exc:  # noqa: BLE001 - 单源异常不能影响其它源
                    error = f"{type(exc).__name__}: {exc}"
                    reason = "unexpected"

            elapsed = int((time.monotonic() - started) * 1000)
            fetched[source_id] = metadata

            if metadata is not None and cache_store is not None and cache_key and not cached:
                try:
                    await cache_store(source_id, cache_key, metadata.model_dump(mode="json"))
                except Exception:  # noqa: BLE001 - 缓存写失败不影响本次结果
                    pass

            results.append(
                SourceResult(
                    source=source_id,
                    ok=metadata is not None,
                    metadata=metadata,
                    error=error,
                    failure_reason=reason or (None if metadata else "not_found"),
                    elapsed_ms=elapsed,
                    cached=cached,
                )
            )

        # ---- 标量：按字段链取第一个非空 ----
        for field in SCALAR_FIELDS:
            for source_id in chains.get(field, []):
                candidate = fetched.get(source_id)
                if candidate is None:
                    continue
                value = getattr(candidate, field, None)
                if _is_blank(value):
                    continue
                setattr(merged, field, value)
                field_sources[field] = source_id
                break

        # ---- 列表：按字段链拼接去重 ----
        for field in LIST_FIELDS:
            collected: list[str] = []
            sources_for_field: list[str] = []
            for source_id in chains.get(field, []):
                candidate = fetched.get(source_id)
                if candidate is None:
                    continue
                values = getattr(candidate, field, None) or []
                added = False
                for value in values:
                    if value and value not in collected:
                        collected.append(value)
                        added = True
                if added:
                    sources_for_field.append(source_id)
            setattr(merged, field, collected)
            if sources_for_field:
                field_sources[field] = ",".join(sources_for_field)

        if _is_blank(merged.website) and ctx.number:
            merged.website = None

        return AggregatedMetadata(
            number=ctx.number,
            content_type=ctx.content_type,
            metadata=merged,
            field_sources=field_sources,
            sources=results,
        )
