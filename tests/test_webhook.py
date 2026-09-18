"""CD2 webhook 的解析、防抖与落地。

这份测试用真实的 SQLite 与 LocalStorage，只是把 AppContext 换成一个轻量替身，
所以测的是"事件 -> 索引"的真实链路，不是 mock。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.config import RuntimeConfig
from server.db import Database
from server.models import ScrapeStatus
from server.storage import LocalStorage, RootGuard
from server.webhook import Debouncer, WebhookEvent, WebhookProcessor, parse_payload


class _Ctx:
    """只带 WebhookProcessor 需要的那几个属性。"""

    def __init__(self, config: RuntimeConfig, storage: LocalStorage, db: Database) -> None:
        self.config = config
        self.storage = storage
        self.db = db


# ---------------------------------------------------------------- 载荷解析


def test_parse_payload_accepts_bool_and_string_is_dir():
    events, warnings = parse_payload(
        {
            "data": [
                {"action": "create", "is_dir": False, "source_file": "/a.mp4"},
                {"action": "CREATE", "is_dir": "true", "source_file": "/b"},
                {"action": "rename", "is_dir": False, "source_file": "/c.mp4", "destination_file": "/d.mp4"},
            ],
            "unknown_field": 1,
        }
    )
    assert not warnings
    assert [e.action for e in events] == ["create", "create", "rename"]
    assert events[1].is_dir is True
    assert events[2].virtual_path == "/d.mp4"


def test_parse_payload_skips_unknown_action_with_warning():
    events, warnings = parse_payload({"data": [{"action": "chmod", "source_file": "/a"}]})
    assert events == []
    assert any("chmod" in w for w in warnings)


def test_parse_payload_rejects_bad_shapes():
    assert parse_payload("nope")[0] == []
    assert parse_payload({})[1] == ["载荷缺少 data 数组"]
    events, warnings = parse_payload({"data": [{"action": "create"}]})
    assert events == []
    assert any("source_file" in w for w in warnings)


def test_parse_payload_requires_destination_on_rename():
    events, warnings = parse_payload(
        {"data": [{"action": "rename", "source_file": "/a.mp4"}]}
    )
    assert events == []
    assert any("destination_file" in w for w in warnings)


# ---------------------------------------------------------------- 防抖


def test_debouncer_releases_after_delay():
    d = Debouncer(20.0)
    d.add("/mnt/a", now=100.0)
    assert d.due(now=110.0) == []
    assert d.pending == 1
    assert d.due(now=121.0) == ["/mnt/a"]
    assert d.pending == 0


def test_debouncer_keeps_latest_deadline_for_same_path():
    d = Debouncer(10.0)
    d.add("/mnt/a", now=100.0)
    d.add("/mnt/a", now=105.0)
    assert d.due(now=111.0) == []
    assert d.due(now=116.0) == ["/mnt/a"]


def test_debouncer_evicts_oldest_when_over_capacity():
    d = Debouncer(10.0, capacity=2)
    d.add("/a", now=1.0)
    d.add("/b", now=2.0)
    d.add("/c", now=3.0)
    assert d.pending == 2


# ---------------------------------------------------------------- 落地


@pytest.fixture()
async def env(tmp_path):
    media = tmp_path / "media"
    (media / "Test").mkdir(parents=True)
    (media / "Test" / "MIDV-123.mp4").write_bytes(b"x")
    (media / "Test" / "cover.jpg").write_bytes(b"y")
    (media / "Sub").mkdir()
    (media / "Sub" / "ABP-456.mkv").write_bytes(b"z")

    config = RuntimeConfig(
        cd2_mappings=[["/115open/Test", str(media / "Test")], ["/115open", str(media)]],
        webhook_enabled=True,
        webhook_debounce_seconds=0.0,
        webhook_auto_scrape=False,
    )
    db = Database(tmp_path / "test.db")
    await db.connect()
    storage = LocalStorage(RootGuard([media]), allow_writes=False)
    ctx = _Ctx(config, storage, db)
    processor = WebhookProcessor(ctx)  # type: ignore[arg-type]
    try:
        yield processor, db, media
    finally:
        await db.close()


async def test_file_create_registers_video(env):
    processor, db, media = env
    outcome = await processor.handle(
        [WebhookEvent(action="create", is_dir=False, source_file="/115open/Test/MIDV-123.mp4")]
    )
    assert outcome.registered == [str(media / "Test" / "MIDV-123.mp4")]
    records = await db.list_records()
    assert [r.status for r in records] == [ScrapeStatus.PENDING]


async def test_file_create_ignores_non_video(env):
    processor, db, _ = env
    outcome = await processor.handle(
        [WebhookEvent(action="create", is_dir=False, source_file="/115open/Test/cover.jpg")]
    )
    assert outcome.registered == []
    assert await db.list_records() == []


async def test_unmapped_virtual_path_is_skipped(env):
    processor, _, _ = env
    outcome = await processor.handle(
        [WebhookEvent(action="create", is_dir=False, source_file="/unknown/x.mp4")]
    )
    assert outcome.registered == []
    assert any("未映射" in s for s in outcome.skipped)


async def test_path_outside_allowed_roots_is_skipped(env):
    """映射到允许根目录之外时必须拒绝，不能因为配置写错就写到别处。"""
    processor, _, _ = env
    processor._ctx.config.cd2_mappings = [["/115open", "/etc"]]
    outcome = await processor.handle(
        [WebhookEvent(action="create", is_dir=False, source_file="/115open/passwd.mp4")]
    )
    assert outcome.registered == []
    assert outcome.skipped


async def test_file_create_is_idempotent(env):
    processor, db, _ = env
    event = WebhookEvent(action="create", is_dir=False, source_file="/115open/Test/MIDV-123.mp4")
    await processor.handle([event])
    outcome = await processor.handle([event])
    assert outcome.registered == []
    assert len(await db.list_records()) == 1


async def test_file_delete_removes_index(env):
    processor, db, _ = env
    await processor.handle(
        [WebhookEvent(action="create", is_dir=False, source_file="/115open/Test/MIDV-123.mp4")]
    )
    outcome = await processor.handle(
        [WebhookEvent(action="delete", is_dir=False, source_file="/115open/Test/MIDV-123.mp4")]
    )
    assert outcome.removed == 1
    assert await db.list_records() == []


async def test_file_rename_updates_path(env):
    processor, db, media = env
    await processor.handle(
        [WebhookEvent(action="create", is_dir=False, source_file="/115open/Test/MIDV-123.mp4")]
    )
    outcome = await processor.handle(
        [
            WebhookEvent(
                action="rename",
                is_dir=False,
                source_file="/115open/Test/MIDV-123.mp4",
                destination_file="/115open/Test/MIDV-123-UC.mp4",
            )
        ]
    )
    assert outcome.renamed == 1
    records = await db.list_records()
    assert records[0].path == str(media / "Test" / "MIDV-123-UC.mp4")


async def test_directory_create_defers_then_scans_only_that_subtree(env):
    processor, db, media = env
    outcome = await processor.handle(
        [WebhookEvent(action="create", is_dir=True, source_file="/115open/Test")]
    )
    assert outcome.deferred == 1
    assert processor.debouncer.pending == 1

    # 这个用例的防抖时长是 0，所以第一次 flush 就该扫掉；
    # 「到期前不扫」的行为由上面的 Debouncer 单测覆盖。
    scanned = await processor.flush_due()
    assert processor.debouncer.pending == 0
    assert len(scanned.registered) == 1

    registered = {r.path for r in await db.list_records()}
    assert registered == {str(media / "Test" / "MIDV-123.mp4")}


async def test_directory_delete_removes_by_prefix_without_touching_disk(env):
    processor, db, media = env
    await processor.flush_due()  # 无待处理，直接跳过
    await db.upsert_record(_record(media / "Test" / "MIDV-123.mp4"))
    await db.upsert_record(_record(media / "Sub" / "ABP-456.mkv"))
    outcome = await processor.handle(
        [WebhookEvent(action="delete", is_dir=True, source_file="/115open/Test")]
    )
    assert outcome.removed == 1
    remaining = {r.path for r in await db.list_records()}
    assert remaining == {str(media / "Sub" / "ABP-456.mkv")}
    assert (media / "Test" / "MIDV-123.mp4").exists()


async def test_subtree_scan_respects_limit(env):
    processor, db, _ = env
    processor._ctx.config.webhook_max_subtree_files = 1
    processor._ctx.config.webhook_debounce_seconds = 0.0
    processor.reconfigure()
    await processor.handle([WebhookEvent(action="create", is_dir=True, source_file="/115open")])
    outcome = await processor.flush_due()
    assert len(outcome.registered) == 1
    assert any("上限" in w for w in outcome.warnings)


def _record(path: Path):
    from server.models import ScrapeRecord, ScrapeStatus

    return ScrapeRecord(id=str(path), path=str(path), status=ScrapeStatus.PENDING)
