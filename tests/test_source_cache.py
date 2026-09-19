r"""源快照缓存的失效。

**起因**：getchu 加上 サンプル画像 解析、Referer 也修好之后重跑，剧照还是空的 ——
因为缓存里存的还是"没有剧照"那一版解析结果，重跑永远命中旧快照。
表现是"代码明明改了，跑一遍却一点变化都没有"，而且完全看不出是缓存干的。

所以 `cache_key` 里带上 `SOURCE_CACHE_VERSION`：解析逻辑改了就把版本 +1，
旧快照自动失效。另留一个清缓存的口子，处理"站点改了页面而代码没动"。
"""

from __future__ import annotations

from server.aggregate import Aggregator
from server.db import Database
from server.models import ContentType
from server.sources import FetchContext
from server.sources.base import SOURCE_CACHE_VERSION


async def test_cache_key_carries_the_parser_version():
    seen: list[str] = []

    async def lookup(source: str, key: str):
        seen.append(key)
        return None

    async def store(source: str, key: str, payload) -> None:
        return None

    # 用真源（bangumi），不依赖别的测试模块注册的假源 —— 模块收集顺序不保证。
    # 它这次请求必然失败（client 是个空对象），但我们要断言的是"查了缓存"。
    aggregator = Aggregator(object(), enabled_sources=["bangumi"])
    await aggregator.run(
        FetchContext(query="某作品", content_type=ContentType.JANIME),
        route_override={"janime": ["bangumi"]},
        use_cache=True,
        cache_lookup=lookup,
        cache_store=store,
    )
    assert seen, "没有查缓存"
    assert all(k.startswith(f"v{SOURCE_CACHE_VERSION}:") for k in seen), seen


async def test_clear_snapshots_by_source_and_all(tmp_path):
    db = Database(tmp_path / "cache.db")
    await db.connect()
    try:
        await db.save_snapshot("getchu", "a", {"title": "x"})
        await db.save_snapshot("getchu", "b", {"title": "y"})
        await db.save_snapshot("javdb", "a", {"title": "z"})

        assert await db.clear_snapshots("getchu") == 2
        assert await db.get_snapshot("javdb", "a") == {"title": "z"}

        assert await db.clear_snapshots() == 1
        assert await db.get_snapshot("javdb", "a") is None
    finally:
        await db.close()
