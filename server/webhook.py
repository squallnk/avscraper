"""CloudDrive2 文件变更 webhook。

CD2 推来的 `source_file` / `destination_file` 是 **CD2 虚拟路径**（POSIX 分段字符串），
不是宿主挂载路径；Windows 上同样是这套 VFS 字符串，因此一律用 posix 语义处理，
不交给 `pathlib.Path` 解析。

设计要点（照抄这个领域已被验证的做法）：

- **HTTP 立即返回 204**，处理放后台任务。否则 FUSE 子树扫描会把 CD2 自己堵住。
- **目录事件要防抖**：目录 create 往往早于 CD2 把子树列出来，
  所以等 `webhook_debounce_seconds` 再扫，且**只扫事件路径那个子树**，不做整库刷新。
- **幂等**：路径 UNIQUE + "已登记则跳过"，让文件级事件与目录展开重叠时不会重复入库。
- **不做整库遍历**：目录 delete 按路径前缀删索引，不碰磁盘。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.classify import is_video_file
from server.storage import Cd2PathError, Cd2PathMapper, OutsideAllowedRoots, StorageError

if TYPE_CHECKING:
    from server.app import AppContext

logger = logging.getLogger(__name__)

CREATE_ACTIONS = {"create", "created", "add", "added"}
DELETE_ACTIONS = {"delete", "deleted", "remove", "removed"}
RENAME_ACTIONS = {"rename", "renamed", "move", "moved"}


@dataclass
class WebhookEvent:
    action: str
    is_dir: bool
    source_file: str
    destination_file: str | None = None

    @property
    def virtual_path(self) -> str:
        """事件之后该文件"现在在哪"。rename 取目标，其余取源。"""
        if self.action == "rename" and self.destination_file:
            return self.destination_file
        return self.source_file


def _as_bool(value: Any, default: bool = False) -> bool:
    """CD2 的 `is_dir` 可能是 JSON 布尔，也可能是字符串 "true"/"false"。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def parse_payload(payload: Any) -> tuple[list[WebhookEvent], list[str]]:
    """解析 CD2 的 webhook 载荷。返回 (事件列表, 警告列表)。

    未知顶层字段忽略；未知 action 记一条警告后跳过，而不是整批失败。
    """
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return [], ["载荷不是 JSON 对象"]

    items = payload.get("data")
    if not isinstance(items, list):
        return [], ["载荷缺少 data 数组"]

    events: list[WebhookEvent] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            warnings.append(f"data[{index}] 不是对象，已跳过")
            continue
        raw_action = str(item.get("action", "")).strip().lower()
        if raw_action in CREATE_ACTIONS:
            action = "create"
        elif raw_action in DELETE_ACTIONS:
            action = "delete"
        elif raw_action in RENAME_ACTIONS:
            action = "rename"
        else:
            warnings.append(f"data[{index}] 未知 action: {raw_action!r}，已跳过")
            continue

        source = str(item.get("source_file", "") or "").strip()
        dest = str(item.get("destination_file", "") or "").strip() or None
        if not source:
            warnings.append(f"data[{index}] 缺少 source_file，已跳过")
            continue
        if action == "rename" and not dest:
            warnings.append(f"data[{index}] rename 缺少 destination_file，已跳过")
            continue

        events.append(
            WebhookEvent(
                action=action,
                is_dir=_as_bool(item.get("is_dir")),
                source_file=source,
                destination_file=dest,
            )
        )
    return events, warnings


class Debouncer:
    """目录事件防抖。同一路径重复加入只保留一次，取最晚的截止时间。"""

    def __init__(self, delay_seconds: float, *, capacity: int = 500) -> None:
        self._delay = max(0.0, delay_seconds)
        self._capacity = capacity
        self._deadlines: dict[str, float] = {}

    def add(self, path: str, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        self._deadlines[path] = moment + self._delay
        if len(self._deadlines) > self._capacity:
            oldest = min(self._deadlines, key=lambda key: self._deadlines[key])
            self._deadlines.pop(oldest, None)

    def due(self, *, now: float | None = None) -> list[str]:
        moment = time.monotonic() if now is None else now
        ready = [path for path, deadline in self._deadlines.items() if deadline <= moment]
        for path in ready:
            self._deadlines.pop(path, None)
        return ready

    @property
    def pending(self) -> int:
        return len(self._deadlines)

    def clear(self) -> None:
        self._deadlines.clear()


@dataclass
class WebhookOutcome:
    registered: list[str] = field(default_factory=list)
    removed: int = 0
    renamed: int = 0
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    deferred: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "registered": len(self.registered),
            "removed": self.removed,
            "renamed": self.renamed,
            "skipped": self.skipped[:20],
            "warnings": self.warnings[:20],
            "deferred": self.deferred,
        }


class WebhookProcessor:
    """把 CD2 事件落到本地索引与（可选的）刮削任务上。"""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._debouncer = Debouncer(ctx.config.webhook_debounce_seconds)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()

    @property
    def debouncer(self) -> Debouncer:
        return self._debouncer

    def reconfigure(self) -> None:
        self._debouncer = Debouncer(self._ctx.config.webhook_debounce_seconds)

    # ---------------- 路径映射 ----------------

    def mapper(self) -> Cd2PathMapper:
        pairs: list[tuple[str, Path]] = []
        for item in self._ctx.config.cd2_mappings:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((str(item[0]), Path(str(item[1]))))
        return Cd2PathMapper(pairs)

    def resolve(self, virtual_path: str) -> Path | None:
        """虚拟路径 -> 本地路径。没有映射或越界时返回 None 并记原因。"""
        try:
            local = self.mapper().to_local(virtual_path)
        except Cd2PathError:
            return None
        try:
            return self._ctx.storage.guard.check(local)
        except (OutsideAllowedRoots, StorageError):
            return None

    # ---------------- 处理 ----------------

    async def handle(self, events: list[WebhookEvent]) -> WebhookOutcome:
        outcome = WebhookOutcome()
        for event in events:
            try:
                await self._handle_one(event, outcome)
            except Exception as exc:  # noqa: BLE001 - 单个事件失败不能影响其它的
                logger.exception("处理 CD2 事件失败: %s", event)
                outcome.warnings.append(f"{event.source_file}: {type(exc).__name__}: {exc}")
        return outcome

    async def _handle_one(self, event: WebhookEvent, outcome: WebhookOutcome) -> None:
        if event.is_dir:
            await self._handle_dir(event, outcome)
            return
        await self._handle_file(event, outcome)

    async def _handle_file(self, event: WebhookEvent, outcome: WebhookOutcome) -> None:
        local = self.resolve(event.virtual_path)
        if local is None:
            outcome.skipped.append(f"未映射: {event.virtual_path}")
            return

        if event.action == "delete":
            outcome.removed += await self._ctx.db.delete_record_by_path(str(local))
            return

        if event.action == "rename":
            source_local = self.resolve(event.source_file)
            if source_local is None:
                outcome.skipped.append(f"未映射: {event.source_file}")
                return
            changed = await self._ctx.db.rename_record(str(source_local), str(local))
            outcome.renamed += changed
            if changed:
                return
            # 源不在索引里，按目标登记

        if not is_video_file(local.name):
            outcome.skipped.append(f"非视频文件: {event.virtual_path}")
            return

        tracked = await self._ctx.db.tracked_paths()
        if str(local) in tracked:
            return

        if self._ctx.config.webhook_auto_scrape:
            from server.pipeline import scrape_one

            await scrape_one(self._ctx, local)
        else:
            from server.models import ScrapeRecord, ScrapeStatus

            record = ScrapeRecord(
                id=_record_id(local),
                path=str(local),
                status=ScrapeStatus.PENDING,
            )
            await self._ctx.db.upsert_record(record)
        outcome.registered.append(str(local))

    async def _handle_dir(self, event: WebhookEvent, outcome: WebhookOutcome) -> None:
        if event.action == "delete":
            local = self.resolve(event.source_file)
            if local is None:
                outcome.skipped.append(f"未映射: {event.source_file}")
                return
            outcome.removed += await self._ctx.db.delete_records_under(str(local))
            return

        if event.action == "rename":
            source_local = self.resolve(event.source_file)
            target_local = self.resolve(event.virtual_path)
            if source_local is None or target_local is None:
                outcome.skipped.append(f"未映射: {event.source_file} -> {event.virtual_path}")
                return
            outcome.renamed += await self._ctx.db.rewrite_prefix(str(source_local), str(target_local))
            return

        # 目录 create：只登记待扫描，交给防抖后再扫这个子树
        target = self.resolve(event.source_file)
        if target is None:
            outcome.skipped.append(f"未映射: {event.source_file}")
            return
        self._debouncer.add(str(target))
        outcome.deferred += 1

    async def flush_due(self) -> WebhookOutcome:
        """把到期的目录事件扫掉。只扫事件路径那个子树。"""
        outcome = WebhookOutcome()
        due = self._debouncer.due()
        if not due:
            return outcome
        limit = max(1, self._ctx.config.webhook_max_subtree_files)
        for path in due:
            try:
                await self._scan_subtree(Path(path), outcome, limit=limit)
            except Exception as exc:  # noqa: BLE001
                logger.exception("扫描目录失败: %s", path)
                outcome.warnings.append(f"{path}: {type(exc).__name__}: {exc}")
        return outcome

    async def _scan_subtree(self, root: Path, outcome: WebhookOutcome, *, limit: int) -> None:
        tracked = await self._ctx.db.tracked_paths()
        scanned = 0
        for path in self._ctx.storage.iter_videos(root, recursive=True):
            scanned += 1
            if scanned > limit:
                outcome.warnings.append(f"目录 {root} 超过扫描上限 {limit}，已截断")
                break
            if str(path) in tracked:
                continue
            outcome.registered.append(str(path))
            if self._ctx.config.webhook_auto_scrape:
                from server.pipeline import scrape_one

                await scrape_one(self._ctx, path)
            else:
                from server.models import ScrapeRecord, ScrapeStatus

                await self._ctx.db.upsert_record(
                    ScrapeRecord(id=_record_id(path), path=str(path), status=ScrapeStatus.PENDING)
                )

    # ---------------- 后台调度 ----------------

    def schedule(self, events: list[WebhookEvent]) -> None:
        """HTTP 立刻返回，处理丢后台，并且持有引用防止被 GC。"""
        self._track(asyncio.create_task(self._run(events)))

    def schedule_flush(self) -> None:
        self._track(asyncio.create_task(self.flush_due()))

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, events: list[WebhookEvent]) -> None:
        async with self._lock:
            outcome = await self.handle(events)
        logger.info("CD2 webhook 处理完成: %s", outcome.as_dict())

    async def drain(self) -> None:
        """等待所有后台处理结束。停机与测试用。"""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _record_id(path: Path) -> str:
    import hashlib

    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]
