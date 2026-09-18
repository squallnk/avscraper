"""抓回来的元数据到底对不对得上这次查询。

这组用例的起因是**实测踩到的错配**：文件是 `[251114][Queen Bee]牝を狩る村 前編`，
javdb 的模糊搜索返回了一部 2012 年的真人片，标题、演员、片商全是别人的，
而当时的状态是 success —— 差一点就把别人的封面和简介写进媒体库。

里番没有番号，查询只能用作品名，模糊搜索又必然会"返回最像的"，所以这道校验是必需的。
"""

import pytest

from server.matching import normalize_for_match, query_matches_metadata
from server.models import MediaMetadata


@pytest.mark.parametrize(
    ("query", "title", "original_title", "expected"),
    [
        # 实测的错配：查询是里番，抓回来是真人片
        ("牝を狩る村", "男1人を責め続ける3人の痴女 川上ゆう", "男1人を責め続ける3人の痴女", False),
        # 正常命中
        ("勇者姫ミリア", "勇者姫ミリア 第四話 砂漠の町のオークション！", None, True),
        ("ドSなペット", "ドSなペット ～初めての躾け～", None, True),
        ("ながちち永井さん", "ながちち永井さん THE ANIMATION Vol.1", None, True),
        # 日文原标题命中，中文标题不含也算匹配
        ("危険な森 おにごっこ", "危險森林捉迷藏", "危険な森 おにごっこ 第二話", True),
        # 站点标题被截断（标题是查询的前缀）
        ("おしかけ！爆乳ギャルハーレム性活", "おしかけ！爆乳ギャル", None, True),
        # 完全不相干
        ("メイド教育", "別の作品", None, False),
    ],
)
def test_query_matching(query, title, original_title, expected):
    metadata = MediaMetadata(title=title, original_title=original_title)
    assert query_matches_metadata(query, metadata) is expected


@pytest.mark.parametrize("query", ["", "序", "上", "AB"])
def test_short_queries_are_not_judged(query):
    """太短的查询做包含判断没有意义，一律放行。

    拦下来的假阳性会比放过的真问题还多 —— 而且用户能从标题一眼看出不对。
    """
    assert query_matches_metadata(query, MediaMetadata(title="完全不相干的东西")) is True


def test_missing_title_is_not_a_match():
    """抓回来的东西连标题都没有，不能算命中。"""
    assert query_matches_metadata("牝を狩る村", MediaMetadata()) is False


def test_normalization_ignores_separators_and_width():
    """全角/半角、空格、破折号、中点这些不该影响判定。"""
    assert normalize_for_match("ドＳなペット ～初めての躾け～") == normalize_for_match(
        "ドSなペット～初めての躾け～"
    )
    assert query_matches_metadata("1LDK＋J系", MediaMetadata(title="1LDK+J系 いきなり同居")) is True


def test_need_selection_status_exists():
    """错配要有专门的状态，不能混在 not_found 或 success 里。"""
    from server.models import ScrapeStatus

    assert ScrapeStatus.NEED_SELECTION.value == "need_selection"
