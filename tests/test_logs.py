r"""日志落库 —— 「日志」页的数据从哪来。

**起因**：用户在 WebUI 点开「日志」，永远是空的。查下来 `Database.add_logs`
一个调用方都没有 —— 表建好了、接口写好了、页面画好了，中间那根线没接。
日志只在 stdout（`docker logs`）里，页面看着像坏了。

这里测的就是那根线：handler 抓什么、不抓什么，怎么落库，怎么防止表无限长。
"""

from __future__ import annotations

import logging

import pytest

from server import logs
from server.app import build_context, shutdown_context
from server.config import Settings
from server.db import Database


@pytest.fixture(autouse=True)
def clean_queue():
    logs.PENDING.clear()
    yield
    logs.PENDING.clear()


def _emit(name: str, level: int, message: str, args: tuple = (), exc_info=None) -> None:
    record = logging.LogRecord(name, level, __file__, 10, message, args, exc_info)
    logs.DatabaseLogHandler().emit(record)


def test_our_own_loggers_are_recorded():
    _emit("server.pipeline", logging.INFO, "成功 %s", ("a.mp4",))
    assert list(logs.PENDING) == [("INFO", "server.pipeline", "成功 a.mp4")]


def test_third_party_info_is_ignored():
    r"""不挡的话，uvicorn 会为**每个 API 请求**写一条 access 日志、httpx 为每个出站请求写一条。

    日志页会被它们淹没，真正要看的刮削记录反而翻不到。
    """
    _emit("uvicorn.access", logging.INFO, "127.0.0.1 - GET /api/logs")
    _emit("httpx", logging.INFO, "HTTP Request: GET https://x.test/")
    assert list(logs.PENDING) == []


def test_third_party_warnings_still_get_through():
    """别人的 WARNING 是真的有事，不能一起挡掉。"""
    _emit("uvicorn.error", logging.WARNING, "socket closed")
    assert [entry[0] for entry in logs.PENDING] == ["WARNING"]


def test_traceback_is_kept():
    r"""`logger.exception()` 的信息全在堆栈里 —— 只留一句"失败了"，等于没记。"""
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        _emit("server.pipeline", logging.ERROR, "刮削失败: a.mp4", exc_info=sys.exc_info())
    assert len(logs.PENDING) == 1
    message = logs.PENDING[0][2]
    assert "刮削失败: a.mp4" in message
    assert "ValueError: boom" in message


async def test_flush_writes_to_the_database(tmp_path):
    db = Database(tmp_path / "logs.db")
    await db.connect()
    try:
        _emit("server.app", logging.INFO, "启动完成")
        _emit("server.sources.getchu", logging.WARNING, "被限流")
        assert await logs.flush_once(db) == 2
        assert logs.pending_count() == 0  # 落过库的不留在队列里

        rows = await db.list_logs()
        assert {row["message"] for row in rows} == {"启动完成", "被限流"}
        assert {row["logger"] for row in rows} == {"server.app", "server.sources.getchu"}
    finally:
        await db.close()


async def test_prune_keeps_only_the_newest(tmp_path):
    """日志表没有别的清理途径，不管就会一直长。"""
    db = Database(tmp_path / "prune.db")
    await db.connect()
    try:
        await db.add_logs([("INFO", "server.app", f"第 {index} 行") for index in range(50)])
        assert await db.prune_logs(10) == 40
        rows = await db.list_logs(limit=100)
        assert len(rows) == 10
        assert rows[0]["message"] == "第 49 行"  # 新的留下，旧的删掉
    finally:
        await db.close()


async def test_the_wiring_holds_end_to_end(tmp_path):
    r"""真的起一遍进程组合根：日志 -> 队列 -> 后台任务 -> 表。

    这条是这次真正的回归点。前面几个用例都只测零件，
    **零件全对但线没接**正是原来的状态。
    """
    settings = Settings(data_dir=tmp_path, allowed_roots=[])
    ctx = await build_context(settings)
    try:
        assert ctx._log_task is not None
        logging.getLogger("server.smoke").warning("冒烟 %s", 9300)
        await logs.flush_once(ctx.db)
        rows = await ctx.db.list_logs()
        assert any("冒烟 9300" in row["message"] for row in rows), rows
    finally:
        await shutdown_context(ctx)


async def test_install_is_idempotent():
    root = logging.getLogger()
    # install() 是**进程级**的；别的用例起过进程组合根，这里先摘干净
    for handler in list(root.handlers):
        if isinstance(handler, logs.DatabaseLogHandler):
            root.removeHandler(handler)
    before = len(root.handlers)
    try:
        assert logs.install() is True
        assert logs.install() is False  # 重复挂会出现两份一模一样的日志
        assert len(root.handlers) == before + 1
    finally:
        for handler in list(root.handlers):
            if isinstance(handler, logs.DatabaseLogHandler):
                root.removeHandler(handler)
