"""源注册表。

新增源只需实现 `SourcePlugin` 并在 `_BUILTIN` 里登记；
`register` 会校验 id 唯一，重复登记直接抛错而不是静默覆盖。
"""

from __future__ import annotations

from collections.abc import Iterable

from server.models import ContentType
from server.sources.base import (
    FetchContext,
    SourceBlocked,
    SourceError,
    SourceNotFound,
    SourcePlugin,
)

_REGISTRY: dict[str, SourcePlugin] = {}


def register(plugin: SourcePlugin) -> SourcePlugin:
    if plugin.id in _REGISTRY:
        raise ValueError(f"源 id 重复: {plugin.id}")
    _REGISTRY[plugin.id] = plugin
    return plugin


def get(source_id: str) -> SourcePlugin | None:
    return _REGISTRY.get(source_id)


def all_sources() -> list[SourcePlugin]:
    return list(_REGISTRY.values())


def resolve(enabled: Iterable[str] | None) -> list[SourcePlugin]:
    """按启用名单过滤。名单为空表示全启用。"""
    if not enabled:
        return all_sources()
    wanted = set(enabled)
    return [p for p in all_sources() if p.id in wanted]


def _load_builtin() -> None:
    from server.sources import bangumi, freejavbt, getchu, javbus, javdb  # noqa: F401

    for module in (bangumi, getchu, javbus, javdb, freejavbt):
        plugin = module.PLUGIN
        if plugin.id not in _REGISTRY:
            register(plugin)


_load_builtin()

__all__ = [
    "ContentType",
    "FetchContext",
    "SourceBlocked",
    "SourceError",
    "SourceNotFound",
    "SourcePlugin",
    "all_sources",
    "get",
    "register",
    "resolve",
]
