"""SQLite 存储层。

用显式的 schema 版本号做迁移，不引 ORM 与迁移框架：
- `SCHEMA_VERSION` 与 `_MIGRATIONS` 一一对应，启动时从当前版本逐条向上执行。
- 只允许追加迁移，不允许修改已发布的迁移。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from server.config import RuntimeConfig
from server.models import (
    ContentType,
    MediaMetadata,
    ScrapeRecord,
    ScrapeStatus,
    StorageProvider,
)

SCHEMA_VERSION = 3

_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS config (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scrape_records (
            id            TEXT PRIMARY KEY,
            path          TEXT NOT NULL,
            provider      TEXT NOT NULL DEFAULT 'local',
            number        TEXT,
            content_type  TEXT NOT NULL DEFAULT 'unknown',
            status        TEXT NOT NULL DEFAULT 'pending',
            metadata      TEXT,
            field_sources TEXT,
            error         TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_records_status ON scrape_records(status);
        CREATE INDEX IF NOT EXISTS idx_records_number ON scrape_records(number);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_records_path ON scrape_records(path);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS source_snapshots (
            source     TEXT NOT NULL,
            cache_key  TEXT NOT NULL,
            payload    TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (source, cache_key)
        );

        CREATE TABLE IF NOT EXISTS app_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            level      TEXT NOT NULL,
            logger     TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_logs_created ON app_logs(created_at);
        """,
    ),
    (
        3,
        """
        ALTER TABLE scrape_records ADD COLUMN season INTEGER;
        ALTER TABLE scrape_records ADD COLUMN episode INTEGER;
        ALTER TABLE scrape_records ADD COLUMN cd INTEGER;
        ALTER TABLE scrape_records ADD COLUMN episode_source TEXT NOT NULL DEFAULT '';
        """,
    ),
]

CONFIG_KEY = "runtime_config"


def _like_prefix(prefix: str) -> str:
    """把目录路径转成 LIKE 前缀模式。

    两个坑：
    1. 路径里可能有 LIKE 元字符（% _），必须转义，并配合 `ESCAPE '\\'`；
    2. 分隔符要跟随平台 —— Windows 上索引里存的是反斜杠，写死 `/` 会一条都匹配不到；
       而反斜杠本身就是转义字符，所以字面反斜杠要再写一遍。
    """
    normalized = prefix.rstrip("/\\")
    escaped = (
        normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    separator = os.sep
    if separator == "\\":
        separator = "\\\\"
    return escaped + separator


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """连接的持有者。单进程单连接 + WAL，够这个规模用了。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("数据库尚未连接，请先 await Database.connect()")
        return self._conn

    async def _migrate(self) -> None:
        cur = await self.conn.execute("PRAGMA user_version")
        row = await cur.fetchone()
        current = int(row[0]) if row else 0
        for version, sql in _MIGRATIONS:
            if version <= current:
                continue
            await self.conn.executescript(sql)
            await self.conn.execute(f"PRAGMA user_version={version}")
            await self.conn.commit()
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库 schema 版本 {current} 高于本程序支持的 {SCHEMA_VERSION}，请升级程序"
            )

    # ---------------- 运行期配置 ----------------

    async def get_config(self) -> RuntimeConfig:
        cur = await self.conn.execute("SELECT value FROM config WHERE key = ?", (CONFIG_KEY,))
        row = await cur.fetchone()
        return RuntimeConfig.from_json(row["value"] if row else None)

    async def save_config(self, config: RuntimeConfig) -> None:
        await self.conn.execute(
            """
            INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (CONFIG_KEY, config.to_json(), _now()),
        )
        await self.conn.commit()

    # ---------------- 刮削记录 ----------------

    async def upsert_record(self, record: ScrapeRecord) -> None:
        payload = record.metadata.model_dump(mode="json") if record.metadata else None
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO scrape_records
                (id, path, provider, number, content_type, season, episode, cd, episode_source,
                 status, metadata, field_sources, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                number = excluded.number,
                content_type = excluded.content_type,
                season = excluded.season,
                episode = excluded.episode,
                cd = excluded.cd,
                episode_source = excluded.episode_source,
                status = excluded.status,
                metadata = excluded.metadata,
                field_sources = excluded.field_sources,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                record.id,
                record.path,
                record.provider.value,
                record.number,
                record.content_type.value,
                record.season,
                record.episode,
                record.cd,
                record.episode_source,
                record.status.value,
                json.dumps(payload, ensure_ascii=False) if payload else None,
                json.dumps(record.field_sources, ensure_ascii=False),
                record.error,
                record.created_at.isoformat(timespec="seconds") if record.created_at else now,
                now,
            ),
        )
        await self.conn.commit()

    async def list_records(
        self, *, status: ScrapeStatus | None = None, limit: int = 200
    ) -> list[ScrapeRecord]:
        sql = "SELECT * FROM scrape_records"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status.value)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        cur = await self.conn.execute(sql, params)
        return [self._row_to_record(r) for r in await cur.fetchall()]

    async def get_record(self, record_id: str) -> ScrapeRecord | None:
        cur = await self.conn.execute("SELECT * FROM scrape_records WHERE id = ?", (record_id,))
        row = await cur.fetchone()
        return self._row_to_record(row) if row else None

    async def get_record_by_path(self, path: str) -> ScrapeRecord | None:
        cur = await self.conn.execute("SELECT * FROM scrape_records WHERE path = ?", (path,))
        row = await cur.fetchone()
        return self._row_to_record(row) if row else None

    async def delete_record_by_path(self, path: str) -> int:
        cur = await self.conn.execute("DELETE FROM scrape_records WHERE path = ?", (path,))
        await self.conn.commit()
        return cur.rowcount or 0

    async def delete_records_under(self, prefix: str) -> int:
        """按路径前缀删索引。不遍历磁盘。"""
        cur = await self.conn.execute(
            "DELETE FROM scrape_records WHERE path = ? OR path LIKE ? ESCAPE '\\'",
            (prefix.rstrip("/"), _like_prefix(prefix) + "%"),
        )
        await self.conn.commit()
        return cur.rowcount or 0

    async def rename_record(self, old_path: str, new_path: str) -> int:
        """改名。目标已被占用时删掉旧行，避免 UNIQUE 冲突。"""
        existing = await self.get_record_by_path(new_path)
        if existing is not None:
            await self.delete_record_by_path(old_path)
            return 0
        cur = await self.conn.execute(
            "UPDATE scrape_records SET path = ?, updated_at = ? WHERE path = ?",
            (new_path, _now(), old_path),
        )
        await self.conn.commit()
        return cur.rowcount or 0

    async def rewrite_prefix(self, old_prefix: str, new_prefix: str) -> int:
        """目录改名：把该前缀下的所有索引路径改写到新前缀。"""
        old = old_prefix.rstrip("/")
        new = new_prefix.rstrip("/")
        if old == new:
            return 0
        cur = await self.conn.execute(
            "SELECT id, path FROM scrape_records WHERE path = ? OR path LIKE ? ESCAPE '\\'",
            (old, _like_prefix(old) + "%"),
        )
        rows = await cur.fetchall()
        if not rows:
            return 0
        updated = 0
        now = _now()
        for row in rows:
            path = str(row["path"])
            rewritten = new + path[len(old) :] if path != old else new
            taken = await self.get_record_by_path(rewritten)
            if taken is not None:
                await self.conn.execute("DELETE FROM scrape_records WHERE id = ?", (row["id"],))
                continue
            await self.conn.execute(
                "UPDATE scrape_records SET path = ?, updated_at = ? WHERE id = ?",
                (rewritten, now, row["id"]),
            )
            updated += 1
        await self.conn.commit()
        return updated

    async def known_paths(self) -> set[str]:
        cur = await self.conn.execute("SELECT path FROM scrape_records")
        return {r["path"] for r in await cur.fetchall()}

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> ScrapeRecord:
        metadata = None
        if row["metadata"]:
            try:
                metadata = MediaMetadata.model_validate(json.loads(row["metadata"]))
            except (ValueError, TypeError):
                metadata = None
        return ScrapeRecord(
            id=row["id"],
            path=row["path"],
            provider=StorageProvider(row["provider"]),
            number=row["number"],
            content_type=ContentType(row["content_type"]),
            season=row["season"],
            episode=row["episode"],
            cd=row["cd"],
            episode_source=row["episode_source"] or "",
            status=ScrapeStatus(row["status"]),
            metadata=metadata,
            field_sources=json.loads(row["field_sources"] or "{}"),
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ---------------- 源快照缓存 ----------------

    async def save_snapshot(self, source: str, cache_key: str, payload: dict[str, Any]) -> None:
        await self.conn.execute(
            """
            INSERT INTO source_snapshots (source, cache_key, payload, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source, cache_key) DO UPDATE SET
                payload = excluded.payload, fetched_at = excluded.fetched_at
            """,
            (source, cache_key, json.dumps(payload, ensure_ascii=False), _now()),
        )
        await self.conn.commit()

    async def get_snapshot(self, source: str, cache_key: str) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT payload FROM source_snapshots WHERE source = ? AND cache_key = ?",
            (source, cache_key),
        )
        row = await cur.fetchone()
        if not row:
            return None
        try:
            return json.loads(row["payload"])
        except (ValueError, TypeError):
            return None

    # ---------------- 日志 ----------------

    async def add_logs(self, entries: Iterable[tuple[str, str, str]]) -> None:
        rows = [(lvl, name, msg, _now()) for lvl, name, msg in entries]
        if not rows:
            return
        await self.conn.executemany(
            "INSERT INTO app_logs (level, logger, message, created_at) VALUES (?, ?, ?, ?)", rows
        )
        await self.conn.commit()

    async def list_logs(self, limit: int = 300, level: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT level, logger, message, created_at FROM app_logs"
        params: list[Any] = []
        if level:
            sql += " WHERE level = ?"
            params.append(level)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cur = await self.conn.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]
