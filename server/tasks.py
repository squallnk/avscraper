"""任务队列与 worker。

刻意做得很小：一个 `asyncio.Queue`，固定数量的 worker，任务状态落 DB。
不做任务图、不做链式依赖 —— 这个阶段用不上，加了就是 bug 来源。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Task:
    kind: str
    payload: dict[str, Any]
    id: str = ""
    state: str = "pending"
    message: str = ""
    current: int = 0
    total: int = 0
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[Task], Awaitable[None]]


class TaskRunner:
    """单进程任务执行器。"""

    def __init__(self, *, concurrency: int = 2) -> None:
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._handlers: dict[str, Handler] = {}
        self._concurrency = max(1, concurrency)
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._history: list[Task] = []

    def register(self, kind: str, handler: Handler) -> None:
        if kind in self._handlers:
            raise ValueError(f"任务类型重复注册: {kind}")
        self._handlers[kind] = handler

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for index in range(self._concurrency):
            self._workers.append(asyncio.create_task(self._worker(index), name=f"avs-worker-{index}"))

    async def stop(self) -> None:
        """先停循环再收尾，否则停在队列上的 worker 会让进程退不干净。"""
        if not self._running:
            return
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def submit(self, kind: str, payload: dict[str, Any], *, task_id: str = "") -> Task:
        if kind not in self._handlers:
            raise ValueError(f"未注册的任务类型: {kind}")
        task = Task(kind=kind, payload=payload, id=task_id or _next_id())
        self._history.append(task)
        self._history[:] = self._history[-200:]
        await self._queue.put(task)
        return task

    def history(self, limit: int = 100) -> list[Task]:
        return list(reversed(self._history[-limit:]))

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    async def _worker(self, index: int) -> None:
        while self._running:
            try:
                task = await self._queue.get()
            except asyncio.CancelledError:
                break
            handler = self._handlers.get(task.kind)
            if handler is None:
                task.state = "failed"
                task.error = f"未注册的任务类型: {task.kind}"
                self._queue.task_done()
                continue
            task.state = "running"
            try:
                await handler(task)
                if task.state == "running":
                    task.state = "success"
            except asyncio.CancelledError:
                task.state = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001 - worker 不能因为单个任务崩掉
                task.state = "failed"
                task.error = f"{type(exc).__name__}: {exc}"
                logger.exception("任务 %s 失败", task.id)
            finally:
                self._queue.task_done()


_counter = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"t{_counter:06d}"
