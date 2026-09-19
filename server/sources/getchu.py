"""getchu —— 里番第一源。

站点特征（全部用真实页面确认过）：

- 编码是 **EUC-JP**，不是 UTF-8。必须显式解码，交给 httpx 猜会得到乱码。
- **年龄确认墙可以用 `gc=gc` 参数过掉**。
  `/php/nsearch.phtml` 无论如何都是 403；
  `/php/search.phtml` 不加参数会 302 到 `/php/attestation.html`，
  而该页面上的「【すすむ】」链接本质就是回到 `search.phtml` 并带上 `&genre=...&gc=gc`。
  所以直接带 `gc=gc` 请求即可，**不需要 Cookie**。
- 商品详情页 `/soft.phtml?id=<数字 id>` 本来就是公开的。

里番没有番号（getchu 的商品是数字 id），所以它只能作为**路径关键词分类后的查询源**：
用文件名里的作品名去搜，或用 `getchu:<id>` 直接指定商品。

**注意**：搜索结果页里夹杂着推荐位（同样是 `soft.phtml?id=...`）。
所以必须先判"有没有命中"，并且只在结果列表 `ul.display` 里取 id ——
否则关键词查不到东西时会把推荐位当成匹配结果，产出完全错误的元数据。
"""

from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from server.models import ContentType, MediaMetadata, SourceDescriptor
from server.sources.base import FetchContext, SourceBlocked, SourceError, SourcePlugin
from server.sources.html import first_attr, first_text, soup_of

BASE = "https://www.getchu.com"
ENCODING = "euc-jp"

# 「すすむ」按钮背后的机制就是带上这个参数
AGE_ACK_PARAM = "gc=gc"

_ID_QUERY = re.compile(r"^getchu:(\d{3,8})$", re.IGNORECASE)
_DATE = re.compile(r"(\d{4})[/\-年](\d{1,2})[/\-月](\d{1,2})")

# 该标签必须写成「ラベル：」才算属性，所以导航里的「ブランド一覧」天然不会命中。
_LABEL_BLOCKLIST: set[str] = set()

# 单元格里常跟一个解释性链接，如「フロントウイング（このブランドの作品一覧）」「アドベンチャー [一覧]」。
_TRAILING_NOISE = re.compile(r"[（(][^（()）]*[）)]|\[[^\[\]]*\]")

NO_RESULT_MARKERS = ("該当する作品はありませんでした", "該当する商品はありません")


def clean_value(text: str) -> str:
    return re.sub(r"\s+", " ", _TRAILING_NOISE.sub("", text)).strip(" ・、,;；")


def _spec_pairs(soup: BeautifulSoup) -> dict[str, str]:
    """把 `<td>発売日：</td><td>2024/03/15</td>` 这种表格式属性扫成字典。

    站点把属性写在相邻单元格里，没有稳定的 class，所以按"以 `：` 结尾的单元格"
    作为标签、下一个非空单元格作为取值来扫。扫不到就是没有，不抛异常。
    """
    pairs: dict[str, str] = {}
    cells = soup.select("table td, table th")
    for index, cell in enumerate(cells):
        text = cell.get_text(" ", strip=True)
        if not text.endswith(("：", ":")) or len(text) > 24:
            continue
        label = text.rstrip("：:").strip()
        if not label or label in _LABEL_BLOCKLIST:
            continue
        for follower in cells[index + 1 : index + 3]:
            value = clean_value(follower.get_text(" ", strip=True))
            if value:
                pairs.setdefault(label, value)
                break
    return pairs


def _sample_images(soup: BeautifulSoup, product_id: str) -> list[str]:
    r"""商品页的「サンプル画像」—— 里番唯一能拿到真剧照的地方。

    页面上缩略图是 `c877668sample1_s.jpg`，而外层 `<a href>` 指的是原图
    `c877668sample1.jpg`。**必须取原图**：`_s` 那张只有 200px 宽，当背景图会被拉伸糊掉。

    为什么在意：javdb / freejavbt 给的"剧照"其实都是 120x90 的缩略图
    （大图那个后缀 403），过不了 `fanart_min_width`，最后只能拿封面兜底 ——
    结果 `-fanart.jpg` 和 `-poster.jpg` 是同一张图。getchu 这边是真正的 CG。
    """
    urls: list[str] = []
    for link in soup.select(".item-Samplecard a[href]"):
        href = str(link.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("//"):  # 协议相对：页面里两种写法都有
            href = "https:" + href
        elif href.startswith("/"):
            href = BASE + href
        if href.startswith(("http://", "https://")) and href not in urls:
            urls.append(href)
    if urls:
        return urls

    # 兜底：有些页面没有 item-Samplecard 结构，但正文脚本里直接列了原图地址
    pattern = re.compile(
        re.escape(f"{BASE}/brandnew/{product_id}/") + r"\w+sample\d+\.jpg"
    )
    for found in pattern.findall(str(soup)):
        if found not in urls:
            urls.append(found)
    return urls


def parse_product(html: str, product_id: str) -> MediaMetadata:
    """解析商品详情页。纯函数，配合 `tests/fixtures/getchu_*.html` 做回归。"""
    soup = soup_of(html)
    title = first_text(soup, ["h2#soft-title", "#soft-title", "h1"])
    if not title:
        raise SourceError("getchu 详情页没有标题，选择器可能已失效", reason="parse_error")
    title = clean_value(title) or title

    cover = f"{BASE}/brandnew/{product_id}/rc{product_id}package.jpg"
    if not _has_image(soup, cover):
        found = first_attr(soup, ["a#package_image img", "img#package_image"], "src")
        cover = f"{BASE}{found}" if found and found.startswith("/") else (found or "")

    spec = _spec_pairs(soup)
    release_date = None
    year = None
    matched = _DATE.search(spec.get("発売日", ""))
    if matched:
        release_date = f"{matched.group(1)}-{int(matched.group(2)):02d}-{int(matched.group(3)):02d}"
        year = int(matched.group(1))

    # ジャンル 是作品分类文案，サブジャンル 是游戏性分类；两者才是有效标签。
    # 不能拿页面里的任意链接当标签 —— 会混进发售日、导航之类的噪声。
    genres = [
        value
        for key, value in spec.items()
        if key in ("ジャンル", "サブジャンル", "カテゴリ") and value
    ]

    return MediaMetadata(
        number=None,
        title=title,
        original_title=title,
        plot=spec.get("商品紹介") or spec.get("ストーリー"),
        actors=[value for key, value in spec.items() if key in ("声優", "出演")],
        director=spec.get("監督"),
        studio=spec.get("ブランド") or spec.get("メーカー"),
        publisher=spec.get("ブランド"),
        series=spec.get("シリーズ"),
        tags=genres,
        release_date=release_date,
        year=year,
        website=f"{BASE}/soft.phtml?id={product_id}",
        poster_url=cover or None,
        fanart_urls=_sample_images(soup, product_id),
    )


def has_results(html: str) -> bool:
    """搜索页是否真有命中。

    必须先判这个：结果页里夹着推荐位，也是 `soft.phtml?id=...` 形态，
    关键词查不到东西时只看 id 会把推荐位当成命中。
    """
    if any(marker in html for marker in NO_RESULT_MARKERS):
        return False
    soup = soup_of(html)
    return bool(soup.select("ul.display li"))


def parse_search(html: str) -> list[str]:
    """从搜索结果页取商品 id，按出现顺序。

    只在结果列表 `ul.display` 里取，不扫全页 —— 避免把推荐位/导航当成结果。
    """
    soup = soup_of(html)
    ids: list[str] = []
    for anchor in soup.select("ul.display a[href*='soft.phtml?id=']"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        matched = re.search(r"soft\.phtml\?id=(\d+)", href)
        if matched and matched.group(1) not in ids:
            ids.append(matched.group(1))
    return ids


def _has_image(soup: BeautifulSoup, url: str) -> bool:
    for img in soup.find_all("img"):
        src = img.get("src")
        if isinstance(src, str) and url.endswith(src.lstrip("/")):
            return True
    return False


def _looks_like_attestation(html: str) -> bool:
    return "年齢認証" in html or "attestation.html" in html


def search_url(keyword: str, *, genre: str = "") -> str:
    """带 `gc=gc` 的搜索地址。少了它就会被弹到年龄确认页。"""
    parts = [f"{BASE}/php/search.phtml?search_keyword={quote(keyword)}"]
    if genre:
        parts.append(f"genre={quote(genre)}")
    parts.append(AGE_ACK_PARAM)
    return "&".join(parts)


class GetchuSource(SourcePlugin):
    descriptor = SourceDescriptor(
        id="getchu",
        name="Getchu",
        homepage="https://www.getchu.com",
        supports=[ContentType.JANIME],
        needs_proxy=True,
        needs_cookie=False,
        note="里番第一源。EUC-JP 编码；年龄确认墙用 gc=gc 参数过掉，不需要 Cookie。",
    )

    async def fetch(self, client: object, ctx: FetchContext) -> MediaMetadata | None:
        get = client.get
        query = (ctx.query or ctx.number or "").strip()
        if not query:
            return None

        direct = _ID_QUERY.match(query)
        if direct:
            return await self._by_id(client, direct.group(1))

        keyword = re.sub(r"\s+", " ", query)[:120]
        result = await get(search_url(keyword), source=self.descriptor.id)
        if result.status == 403:
            raise SourceBlocked(
                "getchu 搜索返回 403。可能是 gc=gc 参数失效或出口 IP 被限流，请检查代理配置。"
            )
        if result.status == 404 or not result.content:
            return None

        html = result.decode(ENCODING)
        if _looks_like_attestation(html):
            raise SourceBlocked("getchu 弹回年龄确认页，gc=gc 参数可能已失效。")
        if not has_results(html):
            return None

        ids = parse_search(html)
        if not ids:
            return None
        return await self._by_id(client, ids[0])

    async def _by_id(self, client: object, product_id: str) -> MediaMetadata | None:
        r"""按商品 id 取详情页，**先试新地址** `/item/<id>/?gc=gc`。

        那正是站点自己年龄确认页上「【すすむ】」链接指向的地方 —— 直接带参数过去，
        不用跟着 301 走。

        老地址 `/soft.phtml?id=<id>` 现在会 301 到新地址，而**重定向会丢掉 query**：
        没有 `gc=gc` 就落到年龄确认页。它之所以还能用，是因为同一个 httpx 客户端里
        先跑的**搜索**请求带了 `gc=gc`、站点种下了年龄 cookie，后续请求自动带上。

        那是个隐性依赖：第一次搜索要是被限流，详情页会跟着全废 ——
        而且报出来的是"选择器失效"，查错方向完全不对。所以老地址只留作兜底。
        """
        get = client.get
        last_error: SourceError | None = None
        for url in (
            f"{BASE}/item/{product_id}/?{AGE_ACK_PARAM}",
            f"{BASE}/soft.phtml?id={product_id}&{AGE_ACK_PARAM}",
        ):
            result = await get(url, source=self.descriptor.id)
            if result.status != 200 or not result.content:
                continue
            html = result.decode(ENCODING)
            if _looks_like_attestation(html):
                continue
            try:
                return parse_product(html, product_id)
            except SourceError as exc:
                last_error = exc
        if last_error is not None:
            # 两条路都拿到了页面但都解析不了 —— 这是"站点改版了"，要报出来
            raise last_error
        return None


PLUGIN = GetchuSource()
