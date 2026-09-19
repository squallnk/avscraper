"""getchu 解析回归。

三份固件都是**真实抓取**的页面（EUC-JP 已转存为 UTF-8），不是合成页面：
- `getchu_877668.html` 商品详情页
- `getchu_search_island.html` 有命中的搜索结果
- `getchu_search_empty.html` 无命中的搜索结果

这样站点小改版能被测出来。合成页面只能挡住"改坏代码"。
"""

import pytest
from conftest import load_fixture

from server.sources.base import SourceError
from server.sources.getchu import (
    AGE_ACK_PARAM,
    ENCODING,
    has_results,
    parse_product,
    parse_search,
    search_url,
)


def _fixture(name: str) -> str:
    return load_fixture(name)


@pytest.fixture(scope="module")
def product_html() -> str:
    return _fixture("getchu_877668.html")


@pytest.fixture(scope="module")
def search_html() -> str:
    return _fixture("getchu_search_island.html")


@pytest.fixture(scope="module")
def empty_html() -> str:
    return _fixture("getchu_search_empty.html")


@pytest.fixture(scope="module")
def item_html() -> str:
    r"""**改版后**的商品页（`/item/<id>/?gc=gc`）。

    老固件 `getchu_877668.html` 是 `/soft.phtml` 时代的页面。getchu 后来把详情页
    迁到了 `/item/<id>/`，而 `/soft.phtml?id=` 现在会 301 过去、**并且丢掉 query** ——
    没有 `gc=gc` 就落到年龄确认页。所以新人接手时很容易以为"getchu 挂了"。
    """
    return _fixture("getchu_item_882468_gc.html")


# ---------------------------------------------------------------- 编码


def test_encoding_is_euc_jp():
    """getchu 不是 UTF-8。写错了整页都是乱码，所以把编码固定下来做断言。"""
    assert ENCODING == "euc-jp"


# ---------------------------------------------------------------- 年龄确认墙


def test_search_url_carries_age_ack():
    """少了 gc=gc 会被 302 到年龄确认页，这条断言是防止有人"顺手清理"掉它。"""
    url = search_url("ISLAND")
    assert AGE_ACK_PARAM in url
    assert url.startswith("https://www.getchu.com/php/search.phtml?search_keyword=ISLAND")


def test_search_url_encodes_keyword_as_euc_jp():
    r"""关键词必须按 **EUC-JP** 编码，不是 UTF-8 —— 这条以前写反了。

    getchu 对关键词做编码自动判定：纯假名的 UTF-8 字节是合法 EUC-JP 序列，
    会被当成 EUC-JP 读成乱码 -> 0 条结果；含汉字的才不会。
    于是"有的作品查得到、有的查不到"，而且 0 条和"确实没有"长得一模一样。

        ```オナニー``` の EUC-JP 是 %A5%AA%A5%CA%A5%CB%A1%BC；
    UTF-8 的 %E3%82%AA... 正是会被误判的那种。
    """
    url = search_url("オナニー")
    assert "%A5%AA%A5%CA%A5%CB%A1%BC" in url
    assert "%E3%82%AA" not in url


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("サキュバス", "%A5%B5%A5%AD%A5%E5%A5%D0%A5%B9"),
        ("のっと・せくさろいど", "%A4%CE%A4%C3%A4%C8%A1%A6%A4%BB%A4%AF%A4%B5%A4%ED%A4%A4%A4%C9"),
    ],
)
def test_search_url_matches_the_verified_euc_jp_encodings(keyword, expected):
    """这两个关键词是**实测**过的：UTF-8 0 条、EUC-JP 分别 30 条 / 2 条。"""
    assert expected in search_url(keyword)


def test_search_url_optional_genre():
    assert "genre=anime" in search_url("x", genre="anime")
    assert "genre=" not in search_url("x")


# ---------------------------------------------------------------- 商品页


def test_parse_product_real_page(product_html):
    meta = parse_product(product_html, "877668")
    assert meta.title == "ISLAND"
    assert meta.studio == "フロントウイング"
    assert meta.release_date == "2016-04-28"
    assert meta.year == 2016
    assert meta.poster_url == "https://www.getchu.com/brandnew/877668/rc877668package.jpg"
    assert meta.website == "https://www.getchu.com/soft.phtml?id=877668"


def test_parse_product_collects_sample_images(product_html):
    r"""「サンプル画像」要取**原图**，不是 `_s` 缩略图。

    这是里番唯一能拿到真剧照的地方：javdb / freejavbt 给的"剧照"都是 120x90
    的缩略图（大图后缀 403），过不了 `fanart_min_width`，最后只能拿封面兜底 ——
    结果 `-fanart.jpg` 和 `-poster.jpg` 是同一张图。getchu 这边是真正的 CG。
    """
    meta = parse_product(product_html, "877668")
    assert len(meta.fanart_urls) >= 5
    assert all(url.startswith("https://www.getchu.com/brandnew/877668/") for url in meta.fanart_urls)
    # _s 那张只有 200px 宽，当背景图会被拉伸糊掉
    assert not [url for url in meta.fanart_urls if url.endswith("_s.jpg")]
    assert any(url.endswith("sample1.jpg") for url in meta.fanart_urls)


def test_parse_product_works_on_the_new_layout(item_html):
    """改版后的页面照样要能解析 —— 至少标题、厂牌、封面、剧照都得出来。"""
    meta = parse_product(item_html, "882468")
    assert meta.title == "D.S. -Dal Segno-"
    assert meta.studio == "CIRCUS"
    assert meta.poster_url == "https://www.getchu.com/brandnew/882468/rc882468package.jpg"


def test_new_layout_collects_sample_images(item_html):
    r"""这条是「里番横版剧照」的命门。

    getchu 的 サンプル画像 是里番唯一的高清横版剧照来源（实测 710x400），
    而 javdb / freejavbt 给的"剧照"都是 120x90 —— 过不了 `fanart_min_width`，
    最后只能拿竖版封面兜底，`-fanart.jpg` 和 `-poster.jpg` 变成同一张。

    改版后 `.item-Samplecard` 还在，并且 `<a href>` 指的仍是**原图**
    （缩略图是 `_s.jpg`，只有 200px 宽，当背景图会被拉伸糊掉）。
    """
    meta = parse_product(item_html, "882468")
    assert len(meta.fanart_urls) >= 10
    assert all(u.startswith("https://www.getchu.com/brandnew/882468/") for u in meta.fanart_urls)
    assert not [u for u in meta.fanart_urls if u.endswith("_s.jpg")]
    assert meta.fanart_urls[0].endswith("c882468sample1.jpg")


def test_protocol_relative_sample_urls_are_normalized():
    """页面里 `//www.getchu.com/...` 这种协议相对写法也要变成完整 URL。

    老代码只判 `startswith("/")`，会把 `//host/path` 拼成
    `https://www.getchu.com//www.getchu.com/...` —— 一个必然 404 的地址。
    """
    from server.sources.getchu import _sample_images
    from server.sources.html import soup_of

    html = (
        '<div class="item-Samplecard"><a href="//www.getchu.com/brandnew/1/c1sample1.jpg">'
        '<img src="/brandnew/1/c1sample1_s.jpg"></a></div>'
    )
    urls = _sample_images(soup_of(html), "1")
    assert urls == ["https://www.getchu.com/brandnew/1/c1sample1.jpg"]


def test_parse_product_strips_trailing_noise(product_html):
    """「（このブランドの作品一覧）」「[一覧]」这类解释性链接不能混进字段值。"""
    meta = parse_product(product_html, "877668")
    assert "（" not in (meta.studio or "")
    assert all("[" not in tag for tag in meta.tags)


def test_parse_product_raises_on_unknown_layout():
    with pytest.raises(SourceError) as excinfo:
        parse_product("<html><body><div>陌生结构</div></body></html>", "1")
    assert excinfo.value.reason == "parse_error"


# ---------------------------------------------------------------- 搜索页


def test_search_page_has_results(search_html):
    assert has_results(search_html) is True


def test_parse_search_uses_result_list_only(search_html):
    """结果页里夹着推荐位，也是 soft.phtml?id= 形态。

    884468/882468 这类推荐位不能进结果。只在 ul.display 里取 id 就是为了这个。
    """
    ids = parse_search(search_html)
    assert ids
    assert ids[0] == "1210508"
    assert len(ids) == len(set(ids))


def test_euc_jp_keyword_search_finds_the_real_products():
    r"""编码修好之后，搜索能真正找到作品 —— 用两份**同关键词、不同编码**的真实响应钉住。

    ``gc_euc_2``（EUC-JP）里有 2 条，正是我们要的那两话；
    ``gc_utf8_2``（UTF-8）里 0 条，而且关键词回显是乱码。
    这两份合起来就是这个 bug 的完整证据，以后谁想改回 ``quote()`` 都会立刻红。
    """
    euc = _fixture("gc_euc_2.html")
    assert has_results(euc)
    assert parse_search(euc) == ["1331771", "1320853"]

    utf8 = _fixture("gc_utf8_2.html")
    assert has_results(utf8) is False
    assert parse_search(utf8) == []


def test_empty_search_page_detected(empty_html):
    """关键词查不到东西时必须报"没有"，否则会把推荐位当成匹配结果。"""
    assert has_results(empty_html) is False
    assert parse_search(empty_html) == []


def test_parse_search_on_unrelated_html_returns_empty():
    assert parse_search("<html><body>nothing</body></html>") == []
