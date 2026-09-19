"""javdb —— 综合索引，覆盖有码 / 无码 / 欧美 / FC2 / 动漫。

站点特征：
- 搜索带 `locale=zh`；
- 导航**没有国产分区**；
- 中文 `current-title` 与隐藏 `origin-title`（来自零售目录）分别对应标题与原标题；
- 封面在 `jdbstatic.com`；
- **不解析 plot**；
- 默认需要代理，部分网络会命中版权地域拦截页。

动漫（里番）条目走「動漫」分区，可作为里番的兜底源。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from server.models import ContentType, MediaMetadata, SourceDescriptor
from server.sources.base import FetchContext, SourceBlocked, SourceError, SourcePlugin
from server.sources.html import absolute, first_attr, first_text, soup_of

BASE = "https://javdb.com"
_DATE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_DETAIL_LINK = re.compile(r"^/v/[A-Za-z0-9]+$")

# javdb 把**里番 / 動漫分区**的条目挡在登录墙后面：未登录时详情页返回的是登录页
# （HTTP 200，长度约 26KB，<title>登入 | JavDB…），不是 403。
# 有码/无码条目不需要登录 —— 实测 MIDV-123 匿名可读。
# 所以必须显式识别登录页，否则会被误报成"选择器失效"。
_LOGIN_MARKERS = ("<title>登入", "登入 | JavDB", "登入|JavDB")


def _looks_like_login(html: str) -> bool:
    head = html[:6000]
    return any(marker in head for marker in _LOGIN_MARKERS)


def parse_search(html: str) -> str | None:
    """从搜索结果页取第一个详情页链接。"""
    soup: BeautifulSoup = soup_of(html)
    for anchor in soup.select(".movie-list .item a, .movie-list a.box, a[href^='/v/']"):
        href = anchor.get("href")
        if isinstance(href, str) and _DETAIL_LINK.match(href):
            return href
    return None


def _runtime_minutes(text: str) -> int | None:
    matched = re.search(r"(\d{1,3})\s*(?:分|min)", text)
    return int(matched.group(1)) if matched else None


# javdb 的评分是 5 分制：详情页的「評分」星星固定 5 颗，
# 而且评论表单里就是 `video_review[score]` 的 1~5 五个单选（很差~極好）。
# Emby 的 `<rating>` 是 10 分制，直接写 4.11 会被显示成 4.1/10 —— 一部好评片看着像烂片。
# 所以这里统一换算到 10 分制，跟 bangumi（本身就是 10 分制）对齐。
JAVDB_SCORE_MAX = 5.0
EMBY_SCORE_MAX = 10.0


def _score_value(text: str) -> float | None:
    """評分格子的文案形如「4.11分, 由129人評價」，返回 10 分制。"""
    matched = re.search(r"(\d+(?:\.\d+)?)\s*分", text)
    if not matched:
        return None
    return round(float(matched.group(1)) * EMBY_SCORE_MAX / JAVDB_SCORE_MAX, 2)


def parse_detail(html: str, number: str) -> MediaMetadata:
    """解析详情页。

    javdb 的属性都在 `.movie-panel-info .panel-block` 里，标签是繁體中文：
    番號 / 日期 / 時長 / 導演 / 片商 / 評分 / 類別 / 演員。
    **評分不在 `.score` 里**（那个选择器在本站不存在），必须从面板取值。
    """
    if _looks_like_login(html):
        raise SourceBlocked(
            "javdb 要求登录才能访问该条目（里番/動漫分区有登录墙）。"
            "请在「设置 → 站点 Cookie」里填入 javdb 的 Cookie。"
        )

    soup: BeautifulSoup = soup_of(html)
    if soup.select_one(".age-verify, .modal-title") and not soup.select_one(".movie-panel-info"):
        raise SourceBlocked("javdb 返回年龄确认或拦截页")

    title = first_text(
        soup, [".title strong.current-title", "h2.title strong", ".movie-detail .title"]
    )
    if not title:
        raise SourceError("javdb 详情页没有标题，选择器可能已失效", reason="parse_error")
    original = first_text(soup, ["input[name='origin-title']", ".origin-title"])

    cover = first_attr(
        soup, [".column-video-cover img", "img.video-cover", ".video-cover img"], "src"
    )

    info: dict[str, str] = {}
    for block in soup.select(".movie-panel-info .panel-block"):
        label_node = block.select_one("strong")
        value_node = block.select_one(".value")
        if label_node is None or value_node is None:
            continue
        info[label_node.get_text(strip=True).rstrip(":：")] = value_node.get_text(" ", strip=True)

    release_date = None
    year = None
    matched = _DATE.search(info.get("日期", "") or info.get("時間", ""))
    if matched:
        release_date = f"{matched.group(1)}-{int(matched.group(2)):02d}-{int(matched.group(3)):02d}"
        year = int(matched.group(1))

    # javdb 用 `actor-female` 标出女演员；男演员没有这个类。
    # 优先只取女演员 —— 否则会把男优混进演员表。
    female = [
        a.get_text(strip=True)
        for a in soup.select(".movie-panel-info .actor-female")
        if a.get_text(strip=True)
    ]
    actors = female or [
        a.get_text(strip=True)
        for a in soup.select(".movie-panel-info .panel-block .value a[href*='/actors/']")
        if a.get_text(strip=True)
    ]

    tags = [
        a.get_text(strip=True)
        for a in soup.select(".movie-panel-info a[href*='/tags']")
        if a.get_text(strip=True)
    ]

    # 剧照：tile-images 里混着封面（/covers/），要排掉，否则封面会出现在剧照里
    fanarts: list[str] = []
    for img in soup.select(".preview-images img, .tile-images img"):
        src = img.get("src") or img.get("data-src")
        if not isinstance(src, str) or "/covers/" in src:
            continue
        url = absolute(src, BASE)
        if url and url not in fanarts:
            fanarts.append(url)

    return MediaMetadata(
        number=number,
        title=title,
        original_title=original,
        actors=actors,
        director=info.get("導演") or info.get("导演"),
        studio=info.get("片商") or info.get("製作商"),
        publisher=info.get("發行商") or info.get("發行"),
        series=info.get("系列"),
        tags=tags,
        release_date=release_date,
        year=year,
        runtime=_runtime_minutes(info.get("時長", "")),
        score=_score_value(info.get("評分", "")),
        poster_url=absolute(cover, BASE),
        thumb_urls=fanarts[:1],
        fanart_urls=fanarts,
        website=f"{BASE}/search?q={number}&f=all",
    )


class JavdbSource(SourcePlugin):
    descriptor = SourceDescriptor(
        id="javdb",
        name="JavDB",
        homepage="https://javdb.com",
        supports=[
            ContentType.CENSORED,
            ContentType.UNCENSORED,
            ContentType.FC2,
            ContentType.WESTERN,
            ContentType.JANIME,
        ],
        needs_proxy=True,
        needs_cookie=True,
        cookie_probe_query="ドSなペット",
        note="综合索引。有码/无码条目匿名可读；**里番/動漫分区需要登录**，要填 Cookie。不解析 plot。",
    )

    async def fetch(self, client: object, ctx: FetchContext) -> MediaMetadata | None:
        query = ctx.number or ctx.query
        if not query:
            return None
        get = client.get
        search = await get(
            f"{BASE}/search?q={query}&f=all&locale=zh",
            source=self.descriptor.id,
        )
        if search.status == 404 or not search.text:
            return None
        href = parse_search(search.text)
        if href is None:
            return None
        detail = await get(f"{BASE}{href}", source=self.descriptor.id)
        if not detail.text:
            return None
        return parse_detail(detail.text, ctx.number or query)


PLUGIN = JavdbSource()
