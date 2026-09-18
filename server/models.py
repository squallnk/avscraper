"""领域数据模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ContentType(StrEnum):
    """内容类型。决定使用哪条刮削源路由。"""

    CENSORED = "censored"
    UNCENSORED = "uncensored"
    AMATEUR = "amateur"
    FC2 = "fc2"
    CHINESE = "chinese"
    WESTERN = "western"
    JANIME = "janime"
    UNKNOWN = "unknown"


CONTENT_TYPE_LABELS: dict[ContentType, str] = {
    ContentType.CENSORED: "日本有码",
    ContentType.UNCENSORED: "日本无码",
    ContentType.AMATEUR: "素人",
    ContentType.FC2: "FC2",
    ContentType.CHINESE: "国产",
    ContentType.WESTERN: "欧美",
    ContentType.JANIME: "里番",
    ContentType.UNKNOWN: "未知",
}


class StorageProvider(StrEnum):
    LOCAL = "local"
    CD2 = "cd2"


class StorageLocator(BaseModel):
    """存储定位。provider 决定用哪种方式读写。"""

    provider: StorageProvider = StorageProvider.LOCAL
    path: str
    """LOCAL 为挂载点上的绝对路径；CD2 为 CD2 虚拟路径（POSIX 分段，以 / 开头）。"""

    is_dir: bool = False


class MatchInfo(BaseModel):
    """番号/分类解析结果。"""

    number: str | None = None
    """规范化番号，如 `MIDV-123`。里番通常为空。"""

    content_type: ContentType = ContentType.UNKNOWN
    category_label: str = ""
    """分类的显示名，如 `日本有码`。"""

    season: int | None = None
    episode: int | None = None
    cd: int | None = None
    """分集标记（CD/Part）。与集数分开，见 `server/episode.py`。"""

    episode_source: str = ""
    """集号的来源：`标记` / `前後編` / `SxxEyy` / `分集兜底`。界面上直接展示，便于核对。"""

    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    """命中的规则说明，用于 WebUI 展示与排障。"""


class MediaMetadata(BaseModel):
    """一个源返回的元数据。字段与 NFO/Emby 对齐。"""

    number: str | None = None
    title: str | None = None
    original_title: str | None = None
    plot: str | None = None
    actors: list[str] = Field(default_factory=list)
    director: str | None = None
    studio: str | None = None
    publisher: str | None = None
    series: str | None = None
    tags: list[str] = Field(default_factory=list)
    release_date: str | None = None
    year: int | None = None
    runtime: int | None = None
    score: float | None = None
    poster_url: str | None = None
    thumb_urls: list[str] = Field(default_factory=list)
    fanart_urls: list[str] = Field(default_factory=list)
    trailer_url: str | None = None
    website: str | None = None


class SourceResult(BaseModel):
    """单个源的刮削结果（含失败信息）。"""

    source: str
    ok: bool
    metadata: MediaMetadata | None = None
    error: str | None = None
    failure_reason: str | None = None
    """`not_found` / `blocked` / `http_error` / `parse_error` / `timeout` / `unexpected`。"""

    elapsed_ms: int = 0
    cached: bool = False


class AggregatedMetadata(BaseModel):
    """多源聚合结果。"""

    number: str | None = None
    content_type: ContentType = ContentType.UNKNOWN
    metadata: MediaMetadata = Field(default_factory=MediaMetadata)
    field_sources: dict[str, str] = Field(default_factory=dict)
    """字段 -> 取值来源的源 id，用于排障与手动挑选。"""

    sources: list[SourceResult] = Field(default_factory=list)


class ScrapeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    SKIPPED = "skipped"


class ScrapeRecord(BaseModel):
    """一次刮削的结果记录。"""

    id: str
    path: str
    provider: StorageProvider = StorageProvider.LOCAL
    number: str | None = None
    content_type: ContentType = ContentType.UNKNOWN
    season: int | None = None
    episode: int | None = None
    cd: int | None = None
    episode_source: str = ""
    status: ScrapeStatus = ScrapeStatus.PENDING
    metadata: MediaMetadata | None = None
    field_sources: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AppLog(BaseModel):
    level: str
    logger: str
    message: str
    created_at: datetime | None = None


class SourceDescriptor(BaseModel):
    """源的自描述信息，供 WebUI 展示与优先级配置使用。"""

    id: str
    name: str
    homepage: str = ""
    supports: list[ContentType] = Field(default_factory=list)
    needs_proxy: bool = False
    needs_cookie: bool = False
    """站点有年龄确认/登录墙，需要用户在设置里填该站 Cookie 才可用。"""

    cookie_probe_query: str = ""
    """验证 Cookie 时用哪个关键词走一遍真实查询。留空表示该源不支持验证。"""
    note: str = ""
    enabled: bool = True


class RawSnapshot(BaseModel):
    """源返回的原始数据快照，用于缓存与离线排障。"""

    source: str
    cache_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime | None = None
