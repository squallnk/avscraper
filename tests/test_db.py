"""路径查询的两种语义。

这两个问题长得像，答案却不一样，混成一个就会出 bug：

    这个路径有没有被记录过？   -> tracked_paths()     CD2 事件去重
    这个路径是不是已经做完了？ -> succeeded_paths()   批量扫描跳过

起因：``known_paths()`` 只返回全部路径，于是 not_found / need_selection / failed
的记录也成了"已知"，重扫会永远跳过它们 —— 源后来能搜到了、或者手动重刮修好了，
也轮不到它们再试一次。改成只看 success 之后，webhook 的幂等用例立刻挂了——
因为它要的正是"记录过"。
"""

from __future__ import annotations

import pytest

from server.db import Database
from server.models import ScrapeRecord, ScrapeStatus


def _record(path: str, status: ScrapeStatus) -> ScrapeRecord:
    return ScrapeRecord(id=path[-12:], path=path, status=status)


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


async def test_only_success_counts_as_done(db):
    await db.upsert_record(_record("/m/ok.mp4", ScrapeStatus.SUCCESS))
    await db.upsert_record(_record("/m/miss.mp4", ScrapeStatus.NOT_FOUND))
    await db.upsert_record(_record("/m/unsure.mp4", ScrapeStatus.NEED_SELECTION))
    await db.upsert_record(_record("/m/boom.mp4", ScrapeStatus.FAILED))

    assert await db.succeeded_paths() == {"/m/ok.mp4"}


async def test_tracked_covers_every_status(db):
    """事件去重看的是"有没有记录"，失败的也算 —— 否则同一条 CD2 事件
    重复到达时会反复刮同一个文件。"""
    for status in (
        ScrapeStatus.SUCCESS,
        ScrapeStatus.NOT_FOUND,
        ScrapeStatus.NEED_SELECTION,
        ScrapeStatus.FAILED,
    ):
        await db.upsert_record(_record(f"/m/{status.value}.mp4", status))

    assert len(await db.tracked_paths()) == 4


async def test_rescrape_after_failure_is_reachable(db):
    """回归：失败过的文件必须能通过重扫再试一次。

    之前的实现里它是"已知"，扫描会永远跳过 —— 用户唯一的出路是手动 SQL。
    """
    path = "/m/[251128][nur]ドSなペット.chs.mp4"
    await db.upsert_record(_record(path, ScrapeStatus.NEED_SELECTION))
    assert path not in await db.succeeded_paths()

    await db.upsert_record(_record(path, ScrapeStatus.SUCCESS))
    assert path in await db.succeeded_paths()
