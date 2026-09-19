"""把日志落到 app_logs 表，给 WebUI 的「日志」页看。

没有这个之前，`/api/logs` **永远是空数组** —— `Database.add_logs` 一个调用方都没有，
「日志」页是死页面：跑完一批刮削，点进去什么都没有，只能去翻 `docker logs`。

为什么走内存队列而不是直接写：日志是在**事件循环线程里**产生的
（`logging` 的 emit 是同步的），在里面 await 数据库会把正在跑的刮削卡住，
甚至在持锁时死锁。所以 emit 只往 deque 里塞一行，后台任务每秒成批落库。
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.db import Database

logger = logging.getLogger(__name__)

# 队列上限：落库卡住时最多攒这么多行，不能无限吃内存。
MAX_PENDING = 2000

# 表里保留最近多少行。日志是"看最近发生了什么"，不是审计账本。
KEEP_ROWS = 5000

# 只收自己包的日志，外加所有 WARNING 以上。
#
# 不这么过滤的话，uvicorn 会给**每个 API 请求**写一条 access 日志、httpx 会给每个
# 出站请求写一条 —— 日志页会被这些淹没，真正要看的刮削记录反而翻不到。
OWN_PACKAGE = "server."

# 单条消息上限：异常堆栈可能很长，别让一行把表撑爆。
MAX_MESSAGE = 2000

PENDING: deque[tuple[str, str, str]] = deque(maxlen=MAX_PENDING)

# 只用来把异常堆栈渲染成文本。注意 **Handler 上没有 formatException**，
# 那个方法在 Formatter 上 —— 写成 self.formatException() 会在 logger.exception()
# 时抛 AttributeError，还被 handleError 吞掉：日志里只剩一句"失败了"。
_EXC_FORMATTER = logging.Formatter()


class DatabaseLogHandler(logging.Handler):
    """把日志塞进内存队列。emit 必须**不阻塞**。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not self._wanted(record):
                return
            message = record.getMessage()
            if record.exc_info:
                # logger.exception() 的信息全在堆栈里，不带上的话只剩一句"失败了"
                message += "\n" + _EXC_FORMATTER.formatException(record.exc_info)
            PENDING.append((record.levelname, record.name, message[:MAX_MESSAGE]))
        except Exception:  # noqa: BLE001 - 日志处理器自己绝不能把主流程搞崩
            self.handleError(record)

    @staticmethod
    def _wanted(record: logging.LogRecord) -> bool:
        return record.name.startswith(OWN_PACKAGE) or record.levelno >= logging.WARNING


def install() -> bool:
    """挂到 root logger 上（幂等）。返回是否是这次挂上的。"""
    root = logging.getLogger()
    if any(isinstance(handler, DatabaseLogHandler) for handler in root.handlers):
        return False
    handler = DatabaseLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    return True


async def flush_once(db: Database) -> int:
    """把队列里的行全部落库，返回落库条数。"""
    entries: list[tuple[str, str, str]] = []
    while PENDING:
        try:
            entries.append(PENDING.popleft())
        except IndexError:  # 极端并发下被别的消费者拿走了
            break
    if not entries:
        return 0
    await db.add_logs(entries)
    return len(entries)


async def flush_loop(db: Database, *, interval: float = 1.0) -> None:
    """后台循环：攒够一秒就落库，顺带定期裁掉过老的日志。"""
    ticks = 0
    while True:
        await asyncio.sleep(interval)
        try:
            await flush_once(db)
            ticks += 1
            if ticks % 60 == 0:
                await db.prune_logs(KEEP_ROWS)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 后台循环不能因为单次失败退出
            logger.exception("日志落库失败")


def pending_count() -> int:
    """队列里还没落库的行数。"""
    return len(PENDING)


__all__ = [
    "KEEP_ROWS",
    "DatabaseLogHandler",
    "flush_loop",
    "flush_once",
    "install",
    "pending_count",
]
