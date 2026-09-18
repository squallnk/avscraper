import pytest

from server.aggregate import Aggregator, build_chains
from server.models import ContentType, MediaMetadata, SourceDescriptor
from server.sources import FetchContext, register
from server.sources.base import SourceBlocked, SourcePlugin


class _Fake(SourcePlugin):
    def __init__(self, sid: str, payload: dict | None, *, raises: Exception | None = None, supports=None):
        self.descriptor = SourceDescriptor(
            id=sid, name=sid, supports=supports or [ContentType.CENSORED]
        )
        self._payload = payload
        self._raises = raises

    async def fetch(self, client, ctx):
        if self._raises:
            raise self._raises
        if self._payload is None:
            return None
        return MediaMetadata(**self._payload)


@pytest.fixture(scope="module", autouse=True)
def register_fakes():
    register(_Fake("fake_a", {"title": "A 标题", "actors": ["甲"]}))
    register(_Fake("fake_b", {"title": "B 标题", "plot": "B 简介", "actors": ["乙"], "tags": ["t1"]}))
    register(_Fake("fake_empty", None))
    register(_Fake("fake_broken", None, raises=SourceBlocked("被拦截")))
    yield


def test_build_chains_respects_blacklist_and_priority():
    chains = build_chains(
        ContentType.CENSORED,
        field_priority={"title": ["fake_b"]},
        field_blacklist={"plot": ["fake_a"]},
        route_override={"censored": ["fake_a", "fake_b"]},
    )
    assert chains["title"][0] == "fake_b"
    assert "fake_a" not in chains["plot"]


def test_build_chains_ignores_priority_entries_outside_route():
    chains = build_chains(
        ContentType.CENSORED,
        field_priority={"title": ["not_in_route"]},
        route_override={"censored": ["fake_a"]},
    )
    assert chains["title"] == ["fake_a"]


async def test_aggregate_takes_first_non_empty_per_field():
    aggregator = Aggregator(object(), enabled_sources=["fake_a", "fake_b"])
    result = await aggregator.run(
        FetchContext(number="X-1", content_type=ContentType.CENSORED),
        route_override={"censored": ["fake_a", "fake_b"]},
    )
    assert result.metadata.title == "A 标题"
    assert result.metadata.plot == "B 简介"
    assert result.field_sources["title"] == "fake_a"
    assert result.field_sources["plot"] == "fake_b"


async def test_aggregate_merges_list_fields_in_chain_order():
    aggregator = Aggregator(object(), enabled_sources=["fake_a", "fake_b"])
    result = await aggregator.run(
        FetchContext(number="X-1", content_type=ContentType.CENSORED),
        route_override={"censored": ["fake_a", "fake_b"]},
    )
    assert result.metadata.actors == ["甲", "乙"]


async def test_aggregate_records_not_found_and_failure_separately():
    aggregator = Aggregator(object(), enabled_sources=["fake_empty", "fake_broken"])
    result = await aggregator.run(
        FetchContext(number="X-1", content_type=ContentType.CENSORED),
        route_override={"censored": ["fake_empty", "fake_broken"]},
    )
    reasons = {r.source: r.failure_reason for r in result.sources}
    assert reasons["fake_empty"] == "not_found"
    assert reasons["fake_broken"] == "blocked"


async def test_one_failing_source_does_not_stop_the_others():
    aggregator = Aggregator(object(), enabled_sources=["fake_broken", "fake_a"])
    result = await aggregator.run(
        FetchContext(number="X-1", content_type=ContentType.CENSORED),
        route_override={"censored": ["fake_broken", "fake_a"]},
    )
    assert result.metadata.title == "A 标题"


async def test_cache_hit_skips_fetching():
    calls: list[str] = []

    class _Counting(_Fake):
        async def fetch(self, client, ctx):
            calls.append(self.id)
            return await super().fetch(client, ctx)

    register(_Counting("fake_count", {"title": "来自缓存"}))

    async def lookup(source, key):
        return {"title": "来自缓存"} if source == "fake_count" else None

    aggregator = Aggregator(object(), enabled_sources=["fake_count"])
    result = await aggregator.run(
        FetchContext(number="X-1", content_type=ContentType.CENSORED),
        route_override={"censored": ["fake_count"]},
        use_cache=True,
        cache_lookup=lookup,
    )
    assert calls == []
    assert result.metadata.title == "来自缓存"
    assert result.sources[0].cached is True
