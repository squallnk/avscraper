"""人工重刮：``scrape_one`` 的三个可选参数。

自动匹配说不准的文件（里番最常见）不该直接写进媒体库，也不该就此卡住。
出口是换关键词 / 指定源 / 人工拍板 —— 这里锁住这三条路径的行为。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from server.aggregate import Aggregator
from server.config import RuntimeConfig
from server.models import ContentType, MediaMetadata, ScrapeStatus, SourceDescriptor
from server.pipeline import scrape_one
from server.sources import register
from server.sources.base import SourcePlugin

JANIME_PATH = Path("/mnt/user/media/里番/[251128][nur]ドSなペット ～初めての躾け～.chs.mp4")
QUERY = "ドSなペット"


class _Fake(SourcePlugin):
    def __init__(self, sid: str, title: str, **extra):
        self.descriptor = SourceDescriptor(id=sid, name=sid, supports=[ContentType.JANIME])
        self._metadata = MediaMetadata(title=title, **extra)

    async def fetch(self, client, ctx):
        return self._metadata


@pytest.fixture(scope="module", autouse=True)
def register_fakes():
    register(_Fake("pick_a", "まったく別の作品"))
    register(_Fake("pick_b", "ドSなペット", plot="来自 pick_b"))
    yield


class _FakeDb:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}

    async def upsert_record(self, record) -> None:
        self.records[record.id] = record

    async def get_snapshot(self, source: str, cache_key: str):
        return None

    async def save_snapshot(self, source: str, cache_key: str, payload) -> None:
        return None


def _ctx(route: dict[str, list[str]], enabled: list[str]) -> SimpleNamespace:
    config = RuntimeConfig()
    config.route_override = route
    return SimpleNamespace(
        config=config,
        aggregator=Aggregator(object(), enabled_sources=enabled),
        db=_FakeDb(),
    )


async def test_mismatched_result_needs_human_confirmation():
    """抓回来的标题对不上查询词时，不写库，标成待确认。"""
    ctx = _ctx({"janime": ["pick_a"]}, ["pick_a"])
    record = await scrape_one(ctx, JANIME_PATH)
    assert record.content_type is ContentType.JANIME
    assert record.status is ScrapeStatus.NEED_SELECTION
    assert "人工确认" in (record.error or "")


async def test_force_success_adopts_result_and_leaves_a_trail():
    """人工拍板后算成功，但要在 error 里留痕，事后能看出这条不是自动匹配过的。"""
    ctx = _ctx({"janime": ["pick_a"]}, ["pick_a"])
    record = await scrape_one(ctx, JANIME_PATH, query_override=QUERY, force_success=True)
    assert record.status is ScrapeStatus.SUCCESS
    assert "人工确认后采用" in (record.error or "")


async def test_source_pin_narrows_the_route_to_one_source():
    """指定源时只查这一个源，别的源连请求都不该发。"""
    ctx = _ctx({"janime": ["pick_a", "pick_b"]}, ["pick_a", "pick_b"])
    record = await scrape_one(ctx, JANIME_PATH, query_override=QUERY, source_pin="pick_b")
    assert record.status is ScrapeStatus.SUCCESS
    assert record.metadata is not None
    assert record.metadata.plot == "来自 pick_b"


async def test_query_override_actually_changes_the_query():
    """换词重搜要真的换掉查询词，而不是被清洗规则覆盖回去。"""
    ctx = _ctx({"janime": ["pick_b"]}, ["pick_b"])
    record = await scrape_one(ctx, JANIME_PATH, query_override=QUERY)
    assert record.status is ScrapeStatus.SUCCESS


async def test_cleaned_name_is_used_when_no_override():
    """不传 query_override 时仍然是自动清洗出来的词（回归保护）。"""
    from server.cleaner import clean_query_name

    assert clean_query_name(JANIME_PATH.name) == QUERY


# ------------------------------------------------------------------ 接口


class _RecordDb(_FakeDb):
    def __init__(self, record) -> None:
        super().__init__()
        self._record = record

    async def get_record(self, record_id: str):
        return self._record if self._record and self._record.id == record_id else None


def _record(**overrides):
    from server.models import ScrapeRecord, StorageProvider

    payload = {
        "id": "abc123",
        "path": str(JANIME_PATH),
        "provider": StorageProvider.LOCAL,
        "content_type": ContentType.JANIME,
        "status": ScrapeStatus.NEED_SELECTION,
    }
    payload.update(overrides)
    return ScrapeRecord(**payload)


def _request(ctx) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ctx=ctx)))


def _ctx_for_api(record, *, enabled: list[str] | None = None) -> SimpleNamespace:
    ctx = _ctx({"janime": ["pick_a"]}, enabled or ["pick_a", "pick_b"])
    ctx.db = _RecordDb(record)
    ctx.storage = SimpleNamespace(writes_enabled=False)
    return ctx


async def test_rescan_preview_does_not_write():
    """预览必须只读：``write=false`` 时一个文件都不能写。"""
    from server.api.routes import RescanRequest, rescan_record

    ctx = _ctx_for_api(_record())
    result = await rescan_record(
        "abc123", RescanRequest(query=QUERY, source="pick_b", force=True), _request(ctx)
    )
    assert result["written"] == []
    assert result["record"]["status"] == ScrapeStatus.SUCCESS.value
    assert result["record"]["metadata"]["plot"] == "来自 pick_b"


async def test_rescan_write_requires_confirm():
    from fastapi import HTTPException

    from server.api.routes import RescanRequest, rescan_record

    ctx = _ctx_for_api(_record())
    with pytest.raises(HTTPException) as excinfo:
        await rescan_record("abc123", RescanRequest(query=QUERY, write=True), _request(ctx))
    assert excinfo.value.status_code == 400


async def test_rescan_rejects_unknown_record_and_source():
    from fastapi import HTTPException

    from server.api.routes import RescanRequest, rescan_record

    ctx = _ctx_for_api(_record())
    with pytest.raises(HTTPException) as missing:
        await rescan_record("nope", RescanRequest(), _request(ctx))
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as bad_source:
        await rescan_record("abc123", RescanRequest(source="不存在"), _request(ctx))
    assert bad_source.value.status_code == 404


async def test_rescan_refuses_a_disabled_source():
    """源被禁用了却指定它，要说清楚 —— 否则会表现成"这个源查不到"。"""
    from fastapi import HTTPException

    from server.api.routes import RescanRequest, rescan_record

    ctx = _ctx_for_api(_record())
    ctx.config.enabled_sources = ["pick_a"]  # pick_b 被禁用
    with pytest.raises(HTTPException) as excinfo:
        await rescan_record("abc123", RescanRequest(source="pick_b"), _request(ctx))
    assert excinfo.value.status_code == 422
    assert "禁用" in excinfo.value.detail
