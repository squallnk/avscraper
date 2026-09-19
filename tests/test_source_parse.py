"""刮削源的解析回归。

固件全部是**真实抓取**的页面（放在 `tests/fixtures/`），不是合成 HTML：

| 固件 | 来源 |
|---|---|
| `javbus_detail.html` | javbus 番号详情页 |
| `javdb_search.html` | javdb 搜索结果页 |
| `javdb_detail.html` | javdb 详情页（由搜索页取到的链接二次抓取） |
| `freejavbt_search.html` | freejavbt 搜索结果页 |
| `freejavbt_detail.html` | freejavbt 详情页 |
| `freejavbt_home.html` | freejavbt 首页 |
| `getchu_*.html` | getchu 商品页与搜索结果页，见 test_getchu.py |

**为什么必须用真实固件**：javdb 的评分不在 `.score` 里而在「評分」面板格里；
javbus 的标题必须按番号前缀剥掉而不是按连字符截断；
javdb 的演员链接混着男演员、剧照列表混着封面。
这些都是合成 HTML 测不出来的 —— 只有真实页面会暴露。
"""

import pytest
from conftest import load_fixture

from server.sources.base import SourceBlocked, SourceError
from server.sources.freejavbt import (
    parse_description as freejavbt_description,
)
from server.sources.freejavbt import (
    parse_detail as freejavbt_detail,
)
from server.sources.freejavbt import (
    parse_search as freejavbt_search,
)
from server.sources.freejavbt import (
    search_url as freejavbt_url,
)
from server.sources.javbus import parse_detail as javbus_detail
from server.sources.javdb import parse_detail as javdb_detail
from server.sources.javdb import parse_search as javdb_search


def _fixture(name: str) -> str:
    return load_fixture(name)


@pytest.fixture(scope="module")
def javbus_html() -> str:
    return _fixture("javbus_detail.html")


@pytest.fixture(scope="module")
def javdb_search_html() -> str:
    return _fixture("javdb_search.html")


@pytest.fixture(scope="module")
def javdb_detail_html() -> str:
    return _fixture("javdb_detail.html")


# ---------------------------------------------------------------- javbus


def test_javbus_parses_real_detail_page(javbus_html):
    meta = javbus_detail(javbus_html, "MIDV-123")
    assert meta.number == "MIDV-123"
    assert meta.title
    # 标题必须是"剥掉番号前缀"的结果，不能按第一个连字符截断
    assert not meta.title.startswith("MIDV")
    assert meta.original_title.startswith("MIDV-123")
    assert meta.studio == "ムーディーズ"
    assert meta.publisher == "MOODYZ DIVA"
    assert meta.director == "五右衛門"
    assert meta.release_date == "2022-06-03"
    assert meta.year == 2022
    assert meta.runtime == 160


def test_javbus_collects_people_and_tags(javbus_html):
    meta = javbus_detail(javbus_html, "MIDV-123")
    assert len(meta.actors) >= 1
    assert len(meta.tags) >= 5


def test_javbus_absolute_urls(javbus_html):
    meta = javbus_detail(javbus_html, "MIDV-123")
    assert meta.poster_url and meta.poster_url.startswith("https://")
    assert all(url.startswith("https://") for url in meta.fanart_urls)


def test_javbus_raises_parse_error_on_unknown_layout():
    """站点改版时必须明确报 parse_error，而不是抛 AttributeError。"""
    with pytest.raises(SourceError) as excinfo:
        javbus_detail("<html><body><div>完全陌生的结构</div></body></html>", "X-1")
    assert excinfo.value.reason == "parse_error"


# ---------------------------------------------------------------- javdb


def test_javdb_search_finds_detail_link(javdb_search_html):
    href = javdb_search(javdb_search_html)
    assert href is not None
    assert href.startswith("/v/")


def test_javdb_search_on_unrelated_html_returns_none():
    assert javdb_search("<html><body>无结果</body></html>") is None


def test_javdb_parses_real_detail_page(javdb_detail_html):
    meta = javdb_detail(javdb_detail_html, "MIDV-123")
    assert meta.number == "MIDV-123"
    assert meta.title
    assert meta.original_title
    assert meta.studio == "MOODYZ"
    assert meta.director == "五右衛門"
    assert meta.release_date == "2022-06-07"
    assert meta.year == 2022
    assert meta.runtime == 160
    assert len(meta.tags) >= 4


def test_javdb_score_is_converted_to_ten_point_scale(javdb_detail_html):
    """评分在「評分」面板格里，不是 `.score` 选择器 —— 后者在本站不存在。

    而且它是 **5 分制**：同一份固件里，星星固定 5 颗，评论表单是
    `video_review[score]` 的 1~5 单选（很差~極好）。页面写「4.11分」。
    Emby 的 `<rating>` 是 10 分制，所以必须换算 —— 否则一部 4.11/5 的好评片
    在 Emby 里显示成 4.1/10，看着像烂片。"""
    meta = javdb_detail(javdb_detail_html, "MIDV-123")
    assert meta.score == pytest.approx(8.22)


def test_javdb_prefers_female_actors(javdb_detail_html):
    """演员链接混着男演员，必须优先取 `.actor-female`。"""
    meta = javdb_detail(javdb_detail_html, "MIDV-123")
    assert meta.actors == ["三田サクラ"]


def test_javdb_fanart_excludes_cover(javdb_detail_html):
    """剧照列表里混着封面，封面不能出现在剧照里，否则 Emby 会把封面重复展示。"""
    meta = javdb_detail(javdb_detail_html, "MIDV-123")
    assert meta.fanart_urls
    assert all("/covers/" not in url for url in meta.fanart_urls)
    assert meta.poster_url and "/covers/" in meta.poster_url


def test_javdb_login_wall_is_reported_as_blocked_not_parse_error():
    """javdb 把里番条目挡在登录墙后面，返回的是 HTTP 200 的登录页。

    必须识别出来并报 blocked —— 否则会被误报成"选择器失效"，排障时查错方向。
    """
    login_html = (
        '<html><head><title>登入 | JavDB 成人影片數據庫</title></head>'
        '<body><form action="/login">captcha</form></body></html>'
    )
    with pytest.raises(SourceBlocked) as excinfo:
        javdb_detail(login_html, "AqXvxO")
    assert "登入" in str(excinfo.value) or "登录" in str(excinfo.value)
    assert "Cookie" in str(excinfo.value)


def test_javdb_declares_cookie_requirement():
    """设置页靠这个标记决定要不要显示该源的 Cookie 输入框。"""
    from server.sources.javdb import PLUGIN as javdb_plugin

    assert javdb_plugin.descriptor.needs_cookie is True


def test_javdb_parse_error_on_unknown_layout():
    with pytest.raises(SourceError) as excinfo:
        javdb_detail("<html><body><div>陌生</div></body></html>", "X-1")
    assert excinfo.value.reason == "parse_error"


# ---------------------------------------------------------------- freejavbt


def test_freejavbt_search_url_uses_real_query_param():
    """真实首页的搜索表单是 `GET /zh/search?wd=...`，不是 `?q=`。"""
    assert freejavbt_url("MIDV-123") == "https://www.freejavbt.com/zh/search?wd=MIDV-123"


def test_freejavbt_search_page_picks_the_movie_not_the_promo():
    """搜索结果页第一张卡片是 Telegram 推广，不能被当成影片。"""
    href = freejavbt_search(_fixture("freejavbt_search.html"))
    assert href == "https://www.freejavbt.com/zh/MIDV-123"


@pytest.mark.parametrize("slug", ["censored", "uncensored", "western", "fc2", "search", "filter"])
def test_freejavbt_ignores_category_pages(slug):
    """分类页和详情页在同一层路径下，分类页不能被当成影片。"""
    html = f'<a href="https://www.freejavbt.com/zh/{slug}">x</a>'
    assert freejavbt_search(html) is None


def test_freejavbt_rejects_two_segment_category_paths():
    assert freejavbt_search('<a href="https://www.freejavbt.com/zh/censored/play">x</a>') is None


def test_freejavbt_accepts_real_number_slugs():
    html = '<a href="https://www.freejavbt.com/zh/MIDV-123">x</a>'
    assert freejavbt_search(html) == "https://www.freejavbt.com/zh/MIDV-123"


def test_freejavbt_description_parsing():
    desc = (
        "影片番号为MIDV-123，影片名是某标题，发佈日期为2022-06-07，"
        "主演女优是甲、乙、丙，影片时长160分钟，由拍摄的作品，主题为乳交、巨乳。"
    )
    parsed = freejavbt_description(desc)
    assert parsed["number"] == "MIDV-123"
    assert parsed["title"] == "某标题"
    assert parsed["release_date"] == "2022-06-07"
    assert parsed["actors"] == "甲、乙、丙"
    assert parsed["runtime"] == "160"
    assert parsed["tags"] == "乳交、巨乳"


def test_freejavbt_description_missing_pieces_do_not_raise():
    assert freejavbt_description("完全无关的一段话") == {}


def test_freejavbt_parses_real_detail_page():
    meta = freejavbt_detail(_fixture("freejavbt_detail.html"), "MIDV-123")
    assert meta.number == "MIDV-123"
    assert meta.title
    assert meta.release_date == "2022-06-07"
    assert meta.year == 2022
    assert meta.runtime == 160
    assert len(meta.actors) >= 1
    assert len(meta.tags) >= 4
    assert meta.poster_url and meta.poster_url.startswith("https://")


def test_freejavbt_fanart_excludes_lazy_placeholder():
    """图片是懒加载的，src 是占位 gif，真地址在 data-src。"""
    meta = freejavbt_detail(_fixture("freejavbt_detail.html"), "MIDV-123")
    assert meta.fanart_urls
    assert all("loading_" not in url for url in meta.fanart_urls)
    assert all("/samples/" in url for url in meta.fanart_urls)


def test_freejavbt_parse_error_on_unknown_layout():
    with pytest.raises(SourceError) as excinfo:
        freejavbt_detail("<html><body><div>陌生</div></body></html>", "X-1")
    assert excinfo.value.reason == "parse_error"
