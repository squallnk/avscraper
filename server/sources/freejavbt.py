"""freejavbt —— BT 向索引，覆盖最宽，元数据质量一般，适合垫后。

站点特征（用真实页面确认）：

- 详情页是 `https://www.freejavbt.com/<locale>/<番号>`，搜索表单是 `GET /<locale>/search?wd=<关键词>`。
- **分类页和详情页在同一层路径下**（`/zh/censored` vs `/zh/MIDV-123`），
  所以取详情链接时必须靠「含连字符的两段」加黑名单把分类页排掉。
- **搜索结果页里第一张卡片是 Telegram 推广**，不是影片。
- 图片是懒加载：真地址在 `data-src`，`src` 是占位 gif。
- **页面把结构化信息写进了 `<meta name="description">`**，比扒 DOM 可靠得多：
  `影片番号为X，影片名是Y，发佈日期为D，主演女优是A、B，影片时长N分钟，由…，主题为T1、T2。`

**已知局限**：该站的「主演女优」字段不分性别，会把男演员一起列进来
（javdb 有用 `.actor-female` 区分，这里没有对应标记）。
因为它是**最后兜底源**（javdb / javbus 都失败才会走到这里），暂不做性别过滤，
但要知道垫底时演员表可能混入男演员。
"""

from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from server.models import ContentType, MediaMetadata, SourceDescriptor
from server.sources.base import FetchContext, SourceError, SourcePlugin
from server.sources.html import first_attr, first_text, soup_of

BASE = "https://www.freejavbt.com"
_LOCALE = "zh"

# 详情页 slug 形如 `MIDV-123` / `FCN-123`。分类页 slug 形如 `censored` / `uncensored`，
# 也在同一层路径下，所以必须靠"含连字符的两段"加上黑名单把它们排掉 ——
# 否则 `/zh/censored` 会被当成影片详情页。
_DETAIL_SLUG_RE = re.compile(r"^[A-Za-z0-9]{2,12}-[A-Za-z0-9]{1,12}$")
_SLUG_BLOCKLIST = {
    "zh", "en", "ja", "zh_tw", "ko", "de", "es", "fr",
    "censored", "uncensored", "western", "fc2", "search", "filter",
    "tag", "tags", "genre", "genres", "actor", "actors", "studio", "studios",
    "series", "page", "category", "categories", "top", "new", "hot",
    "rank", "rankings", "play", "list",
}
_DETAIL_RE = re.compile(r"^https?://www\.freejavbt\.com/" + _LOCALE + r"/([^/?#]+)/?$")

# meta description 里的结构化片段
_DESC_PATTERNS: dict[str, str] = {
    "number": r"番号为([A-Za-z0-9][A-Za-z0-9-]*)",
    "title": r"影片名是(.+?)，",
    "release_date": r"发佈日期为(\d{4}-\d{2}-\d{2})",
    "actors": r"主演女优是(.+?)，",
    "runtime": r"影片时长(\d+)分钟",
    "tags": r"主题为(.+?)。",
}

_LAZY_PLACEHOLDER = "loading_"


def search_url(keyword: str) -> str:
    return f"{BASE}/{_LOCALE}/search?wd={quote(keyword)}"


def parse_description(description: str) -> dict[str, str]:
    """把 meta description 拆成字段。缺哪个就少哪个，不抛异常。"""
    result: dict[str, str] = {}
    for field, pattern in _DESC_PATTERNS.items():
        matched = re.search(pattern, description)
        if matched:
            result[field] = matched.group(1).strip()
    return result


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[、,，/]", value) if part.strip()]


def parse_search(html: str) -> str | None:
    """从搜索结果页取第一个详情页链接。

    第一张卡片是 Telegram 推广，靠 slug 规则自然被跳过。
    """
    soup: BeautifulSoup = soup_of(html)
    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        matched = _DETAIL_RE.match(href)
        if matched is None:
            continue
        slug = matched.group(1)
        if slug.lower() in _SLUG_BLOCKLIST or not _DETAIL_SLUG_RE.match(slug):
            continue
        return href
    return None


def parse_detail(html: str, number: str) -> MediaMetadata:
    soup: BeautifulSoup = soup_of(html)

    description = ""
    meta = soup.select_one("meta[name='description']")
    if meta is not None:
        description = str(meta.get("content") or "")
    parsed = parse_description(description)

    title = parsed.get("title") or first_text(soup, ["h1", "h2"])
    if not title:
        raise SourceError("freejavbt 详情页没有标题，选择器可能已失效", reason="parse_error")

    release_date = parsed.get("release_date")
    year = int(release_date[:4]) if release_date else None
    runtime = int(parsed["runtime"]) if parsed.get("runtime", "").isdigit() else None

    poster = first_attr(soup, ["meta[property='og:image']"], "content")
    if poster is not None:
        poster = poster.strip() or None

    # 本片自己的剧照来自 jdbstatic 的 /samples/；懒加载真地址在 data-src。
    fanarts: list[str] = []
    for img in soup.select("img[data-src]"):
        src = str(img.get("data-src") or "")
        if "/samples/" not in src or _LAZY_PLACEHOLDER in src:
            continue
        if src not in fanarts:
            fanarts.append(src)

    return MediaMetadata(
        number=parsed.get("number") or number,
        title=title,
        original_title=None,
        actors=_split_list(parsed.get("actors")),
        tags=_split_list(parsed.get("tags")),
        release_date=release_date,
        year=year,
        runtime=runtime,
        poster_url=poster,
        thumb_urls=fanarts[:1],
        fanart_urls=fanarts,
        website=f"{BASE}/{_LOCALE}/{number}",
    )


class FreejavbtSource(SourcePlugin):
    descriptor = SourceDescriptor(
        id="freejavbt",
        name="FreeJAVBT",
        homepage="https://www.freejavbt.com",
        supports=[
            ContentType.CENSORED,
            ContentType.UNCENSORED,
            ContentType.WESTERN,
            ContentType.FC2,
            ContentType.JANIME,
        ],
        needs_proxy=True,
        note="覆盖最宽的 BT 索引，含成人動畫分区。元数据取自 meta description；只作兜底，演员表不分性别。",
    )

    async def fetch(self, client: object, ctx: FetchContext) -> MediaMetadata | None:
        query = ctx.number or ctx.query
        if not query:
            return None
        get = client.get
        search = await get(search_url(query), source=self.descriptor.id)
        if search.status == 404 or not search.text:
            return None
        href = parse_search(search.text)
        if href is None:
            return None
        detail = await get(href, source=self.descriptor.id)
        if detail.status != 200 or not detail.text:
            return None
        return parse_detail(detail.text, ctx.number or query)


PLUGIN = FreejavbtSource()
