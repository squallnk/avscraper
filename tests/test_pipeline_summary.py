"""扫描任务的收尾统计。

起因：一条被判成「需人工确认」的记录，在任务摘要里被算成"失败" ——
"完成：成功 0，失败 1"读起来像运行出错了，其实只是有一条要你拍板。
两种状态的处置方式完全不同，混在一个数字里会把人引到错误的方向。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from server import pipeline
from server.config import RuntimeConfig
from server.models import ContentType, ScrapeRecord, ScrapeStatus


class _Db:
    async def succeeded_paths(self) -> set[str]:
        return set()


class _Storage:
    def iter_videos(self, root, recursive=True):  # noqa: ANN001, ANN202, ARG002
        return [Path("/media/a.mp4"), Path("/media/b.mp4"), Path("/media/c.mp4")]


def _record(path: str, status: ScrapeStatus) -> ScrapeRecord:
    return ScrapeRecord(
        id=path, path=path, content_type=ContentType.JANIME, status=status
    )


@pytest.mark.parametrize(
    ("status", "counter"),
    [
        (ScrapeStatus.SUCCESS, "succeeded"),
        (ScrapeStatus.NEED_SELECTION, "need_selection"),
        (ScrapeStatus.NOT_FOUND, "not_found"),
        (ScrapeStatus.SKIPPED, "skipped"),
        (ScrapeStatus.FAILED, "failed"),
    ],
)
async def test_each_status_lands_in_its_own_counter(monkeypatch, status, counter):
    async def fake_scrape_one(ctx, path, **kwargs):  # noqa: ANN001, ANN202, ARG001
        return _record(str(path), status)

    monkeypatch.setattr(pipeline, "scrape_one", fake_scrape_one)

    ctx = SimpleNamespace(
        config=RuntimeConfig(),
        db=_Db(),
        storage=_Storage(),
        settings=SimpleNamespace(allowed_roots=[Path("/media")]),
    )
    task = SimpleNamespace(payload={}, total=0, current=0, message="", result={})

    await pipeline.run_scan_and_scrape(ctx, task)  # type: ignore[arg-type]

    assert task.result[counter] == 3
    assert sum(task.result.values()) == 3
    for key, value in task.result.items():
        if key != counter:
            assert value == 0, f"{key} 不该也被计入"


async def test_summary_text_names_every_bucket(monkeypatch):
    """摘要要能一眼看出"有东西等你处理"，而不是只给一个"失败"。"""

    async def fake_scrape_one(ctx, path, **kwargs):  # noqa: ANN001, ANN202, ARG001
        return _record(str(path), ScrapeStatus.NEED_SELECTION)

    monkeypatch.setattr(pipeline, "scrape_one", fake_scrape_one)

    ctx = SimpleNamespace(
        config=RuntimeConfig(),
        db=_Db(),
        storage=_Storage(),
        settings=SimpleNamespace(allowed_roots=[Path("/media")]),
    )
    task = SimpleNamespace(payload={}, total=0, current=0, message="", result={})
    await pipeline.run_scan_and_scrape(ctx, task)  # type: ignore[arg-type]

    assert "待确认 3" in task.message
    assert "失败 0" in task.message
