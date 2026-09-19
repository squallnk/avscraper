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


def test_search_url_encodes_keyword():
    assert "%E3%82%AA%E3%83%8A%E3%83%8B%E3%83%BC" in search_url("オナニー")


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


def test_empty_search_page_detected(empty_html):
    """关键词查不到东西时必须报"没有"，否则会把推荐位当成匹配结果。"""
    assert has_results(empty_html) is False
    assert parse_search(empty_html) == []


def test_parse_search_on_unrelated_html_returns_empty():
    assert parse_search("<html><body>nothing</body></html>") == []
