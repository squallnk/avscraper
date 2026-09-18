"""javbus —— 日本有码 / 无码综合索引。

站点特征（见 Amane 的 `content-routes` 评估）：
- 有码首页会混素人号，搜索回退带 `parent=ce`；
- 无码主要靠 `/{number}` 直达；
- 详情页 `extrafanart` 热链 `pics.dmm.co.jp`，图片 `/pics/` 校验同源 Referer；
- **不解析 plot**（站点本身没有）。

选择器可能随站点改版失效。解析失败返回 `SourceError`，
`tests/fixtures` 里放一份真实页面即可离线回归。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from server.models import ContentType, MediaMetadata, SourceDescriptor
from server.sources.base import FetchContext, SourceBlocked, SourceError, SourcePlugin
from server.sources.html import (
    absolute,
    all_texts,
    first_attr,
    first_text,
    labeled_value,
    soup_of,
)

BASE = "https://www.javbus.com"
_DATE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_RUNTIME = re.compile(r"(\d{1,3})\s*(?:分|min)", re.IGNORECASE)


def parse_detail(html: str, number: str) -> MediaMetadata:
    """解析详情页。纯函数，便于离线测试。"""
    soup: BeautifulSoup = soup_of(html)
    if soup.select_one("#ageVerify, .alert-danger") and not soup.select_one("a.bigImage"):
        raise SourceBlocked("javbus 返回年龄验证或拦截页")

    title = first_text(soup, ["h3", ".container h3", "title"])
    if not title:
        raise SourceError("javbus 详情页没有标题，选择器可能已失效", reason="parse_error")

    cover = first_attr(soup, ["a.bigImage img", ".screencap img", "img.bigImage"], "src")
    release = labeled_value(soup, "發行日期") or labeled_value(soup, "发行日期")
    runtime_text = labeled_value(soup, "長度") or labeled_value(soup, "长度")
    studio = labeled_value(soup, "製作商") or labeled_value(soup, "制作商")
    publisher = labeled_value(soup, "發行商") or labeled_value(soup, "发行商")
    series = labeled_value(soup, "系列")
    director = labeled_value(soup, "導演") or labeled_value(soup, "导演")

    actors = all_texts(soup, [".star-name a", "a[href*='/star/']", ".avatar-box .name"])
    tags = all_texts(soup, [".genre label a", ".genre a", "span.genre label"])
    fanarts = [
        absolute(src, BASE)
        for src in [
            img.get("src")
            for img in soup.select(".sample-box img, #sample-waterfall img")
        ]
        if src
    ]

    year = None
    release_date = None
    if release:
        matched = _DATE.search(release)
        if matched:
            release_date = f"{matched.group(1)}-{int(matched.group(2)):02d}-{int(matched.group(3)):02d}"
            year = int(matched.group(1))

    runtime = None
    if runtime_text:
        matched = _RUNTIME.search(runtime_text)
        if matched:
            runtime = int(matched.group(1))

    # javbus 标题形如 "MIDV-123 作品名"，把开头的番号摘掉得到干净标题。
    # 不能用 `^.*?-` 去截：番号本身带连字符，会把前缀和首字母一起吃掉。
    clean_title = re.sub(
        rf"^{re.escape(number)}[\s\-_]*", "", title, count=1, flags=re.IGNORECASE
    ).strip() or title

    return MediaMetadata(
        number=number,
        title=clean_title or title,
        original_title=title if clean_title != title else None,
        actors=actors,
        director=director,
        studio=studio,
        publisher=publisher,
        series=series,
        tags=tags,
        release_date=release_date,
        year=year,
        runtime=runtime,
        poster_url=absolute(cover, BASE),
        thumb_urls=fanarts[:1],
        fanart_urls=fanarts,
        website=f"{BASE}/{number}",
    )


class JavbusSource(SourcePlugin):
    descriptor = SourceDescriptor(
        id="javbus",
        name="JavBus",
        homepage="https://www.javbus.com",
        supports=[ContentType.CENSORED, ContentType.UNCENSORED],
        needs_proxy=True,
        note="有码/无码索引。不解析 plot；图片热链需要同源 Referer。",
    )

    async def fetch(self, client: object, ctx: FetchContext) -> MediaMetadata | None:
        if not ctx.number:
            return None
        get = client.get
        result = await get(f"{BASE}/{ctx.number}", source=self.descriptor.id)
        if result.status == 404:
            return None
        if not result.text:
            raise SourceError("javbus 返回空响应", reason="parse_error")
        return parse_detail(result.text, ctx.number)


PLUGIN = JavbusSource()
