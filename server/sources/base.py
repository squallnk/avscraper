"""刮削源插件协议。

约定：
- 一个源一个文件，自带 `descriptor` 描述与 `fetch` 实现。
- `fetch` 返回 `None` 表示**未命中**（不是错误）；抛异常表示**失败**。
  两者由调用方分开统计，排障时能一眼看出是"没这片"还是"站点挂了"。
- 源不做限速与重试，那是 `HttpClient` 的职责；源只关心解析。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from server.models import ContentType, MediaMetadata, SourceDescriptor


class SourceError(Exception):
    """源返回了无法解析或明确失败的结果。"""

    def __init__(self, message: str, *, reason: str = "unexpected") -> None:
        super().__init__(message)
        self.reason = reason


class SourceBlocked(SourceError):
    """被反爬/地域拦截/需要登录。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason="blocked")


class SourceNotFound(SourceError):
    """站点明确回答"没有这条"。"""

    def __init__(self, message: str = "站点无此条目") -> None:
        super().__init__(message, reason="not_found")


SOURCE_CACHE_VERSION = 2
r"""源的**解析逻辑**版本。改了任何源的解析就把它 +1。

快照缓存是按 `(源, 查询词)` 存的原始解析结果（见 `db.save_snapshot`）。
没有这个版本号时，解析改了以后旧快照还会被继续命中 —— 表现是
**"代码明明改了，重跑一遍却一点变化都没有"**，而且看不出是缓存干的。

真踩过：getchu 加上 サンプル画像 解析之后重跑，剧照还是空的 ——
因为缓存里存的还是"没有剧照"那一版。查了半天选择器。

v2：getchu 采集 サンプル画像。
"""


@dataclass
class FetchContext:
    """一次刮削请求的上下文。"""

    number: str | None = None
    content_type: ContentType = ContentType.UNKNOWN
    query: str | None = None
    """自由文本查询，里番（无番号）用这个。"""

    path: str | None = None
    cache_key: str = ""
    extra: dict[str, str] = field(default_factory=dict)


class SourcePlugin(ABC):
    """刮削源基类。"""

    descriptor: SourceDescriptor

    @property
    def id(self) -> str:
        return self.descriptor.id

    def cache_key(self, ctx: FetchContext) -> str:
        return ctx.number or ctx.query or ""

    @abstractmethod
    async def fetch(self, client: object, ctx: FetchContext) -> MediaMetadata | None:
        """抓取并解析。未命中返回 None。"""
