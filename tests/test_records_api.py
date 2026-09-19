"""删除刮削记录。

这是把一条错配结果从库里清掉的**唯一**办法 —— 否则它一直是 success，
扫描按设计会永远跳过它（见 `succeeded_paths`）。

这里用真的 `Database`（跑真实迁移）而不是假对象：删除条件最后会拼成一条 SQL，
拼错了用假对象是发现不了的。路径一律用本机分隔符拼 —— 目录前缀匹配走
`db._like_prefix`，它跟随平台（Windows 上用反斜杠），写死 `/m/...` 会一条都匹配不到。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.api.routes import PurgeRequest, purge_records
from server.db import Database
from server.models import ScrapeRecord, ScrapeStatus


def _record(path: str, status: ScrapeStatus) -> ScrapeRecord:
    return ScrapeRecord(id=path, path=path, status=status)


@pytest.fixture
async def env(tmp_path):
    root = tmp_path.joinpath("m")
    ok = str(root.joinpath("ok.mp4"))
    unsure = str(root.joinpath("wrong.mp4"))
    miss = str(root.joinpath("miss.mp4"))

    db = Database(tmp_path / "records.db")
    await db.connect()
    await db.upsert_record(_record(ok, ScrapeStatus.SUCCESS))
    await db.upsert_record(_record(unsure, ScrapeStatus.NEED_SELECTION))
    await db.upsert_record(_record(miss, ScrapeStatus.NOT_FOUND))

    ctx = SimpleNamespace(db=db)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ctx=ctx)))
    yield SimpleNamespace(db=db, request=request, root=root, ok=ok, unsure=unsure, miss=miss)
    await db.close()


async def test_purge_requires_confirm(env):
    with pytest.raises(HTTPException) as excinfo:
        await purge_records(PurgeRequest(statuses=["success"]), env.request)
    assert excinfo.value.status_code == 400
    assert len(await env.db.tracked_paths()) == 3


async def test_purge_requires_a_condition(env):
    """不带任何条件等于"清空整张表"，必须拒绝。"""
    with pytest.raises(HTTPException) as excinfo:
        await purge_records(PurgeRequest(confirm=True), env.request)
    assert excinfo.value.status_code == 422
    assert len(await env.db.tracked_paths()) == 3


async def test_purge_rejects_unknown_status(env):
    with pytest.raises(HTTPException) as excinfo:
        await purge_records(PurgeRequest(statuses=["无此状态"], confirm=True), env.request)
    assert excinfo.value.status_code == 422
    assert len(await env.db.tracked_paths()) == 3


async def test_purge_by_id_makes_the_file_scrapable_again(env):
    """删掉那条成功的记录后，这个路径不再被"跳过已知"跳过。"""
    assert env.ok in await env.db.succeeded_paths()

    result = await purge_records(PurgeRequest(ids=[env.ok], confirm=True), env.request)
    assert result["deleted"] == 1
    assert env.ok not in await env.db.succeeded_paths()
    assert len(await env.db.tracked_paths()) == 2


async def test_purge_by_status_clears_the_pending_ones(env):
    result = await purge_records(
        PurgeRequest(statuses=["need_selection", "not_found"], confirm=True), env.request
    )
    assert result["deleted"] == 2
    assert await env.db.tracked_paths() == {env.ok}


def test_rescan_forces_image_overwrite():
    """把链路钉住：人工重刮必须传 overwrite_images=True。

    光有下载器上的开关不够 —— 路由不传，它永远走配置里的 false，
    表现就是"NFO 换了、封面没换"。这里挡的就是这种断链。
    """
    import inspect

    from server.api import routes

    source = inspect.getsource(routes.rescan_record)
    assert "write_record_metadata" in source
    assert "overwrite_images=True" in source


async def test_purge_by_root_only_touches_that_directory(env, tmp_path):
    elsewhere = str(tmp_path.joinpath("else", "there.mp4"))
    await env.db.upsert_record(_record(elsewhere, ScrapeStatus.SUCCESS))

    result = await purge_records(PurgeRequest(root=str(env.root), confirm=True), env.request)
    assert result["deleted"] == 3
    assert await env.db.tracked_paths() == {elsewhere}
