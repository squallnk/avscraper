"""Bangumi 源回归。

固件是**真实抓取**的接口响应（`tests/fixtures/bgm_*.json`），
用你库里 8 个真实文件名的清洗结果搜出来的。

这组用例固化了一个重要事实：**旧版搜索接口是精确匹配，新版是模糊排序**。
换成新版接口会重演 javdb 那种错配。
"""

from __future__ import annotations

import json

import pytest
from conftest import load_fixture

from server.matching import query_matches_metadata
from server.sources.bangumi import parse_search
from server.sources.base import SourceError


def _parse(fixture: str, query: str):
    return parse_search(load_fixture(fixture), query)


@pytest.mark.parametrize(
    ("fixture", "query", "title_cn", "original"),
    [
        ("bgm_onabarukai.json", "牝を狩る村", "狩猎雌性的村庄", "牝を狩る村"),
        ("bgm_oshikake.json", "おしかけ！爆乳ギャルハーレム性活", None, "おしかけ！爆乳ギャルハーレム性活"),
        ("bgm_dS.json", "ドSなペット", "抖S的宠物", None),
        ("bgm_kikenamori.json", "危険な森 おにごっこ", "危险之森 捉鬼游戏", "危険な森 おにごっこ"),
    ],
)
def test_first_result_is_the_right_entry(fixture, query, title_cn, original):
    meta = _parse(fixture, query)
    assert meta is not None
    if title_cn:
        assert meta.title == title_cn
    if original:
        assert meta.original_title == original
    # 关键：抓回来的东西必须能通过匹配校验，否则会被判为需人工确认
    assert query_matches_metadata(query, meta) is True


def test_chinese_title_is_preferred_over_japanese():
    """中文库优先用 name_cn 做标题，日文原名进 original_title。"""
    meta = _parse("bgm_onabarukai.json", "牝を狩る村")
    assert meta.title == "狩猎雌性的村庄"
    assert meta.original_title == "牝を狩る村"


def test_poster_upgrades_http_to_https():
    """旧版接口给的是 http 图片地址，要升到 https。

    站点本身支持 https（新版接口返回的就是 https），混用会带来混合内容问题。
    """
    meta = _parse("bgm_onabarukai.json", "牝を狩る村")
    assert meta.poster_url is not None
    assert meta.poster_url.startswith("https://lain.bgm.tv/")
    assert "/pic/cover/l/" in meta.poster_url  # 取 large 那一档


def test_website_points_at_the_subject():
    meta = _parse("bgm_onabarukai.json", "牝を狩る村")
    assert meta.website and "584818" in meta.website


def test_empty_result_returns_none():
    """``{"results":0,"list":[]}`` 是正常的"没有"，不是错误。"""
    assert parse_search('{"results":0,"list":[]}', "任意关键词") is None


def test_legacy_api_error_is_reported_as_parse_error():
    """旧版接口出错时返回 ``{"code":404,"error":"Not Found"}``，没有 list 字段。

    这种情况必须报 parse_error —— 让人看出"是这个源坏了"，
    而不是误判成"这部作品没有"。
    """
    payload = json.dumps({"request": "/subject/457954", "code": 404, "error": "Not Found"})
    with pytest.raises(SourceError) as excinfo:
        parse_search(payload, "牝を狩る村")
    assert excinfo.value.reason == "parse_error"
    assert "404" in str(excinfo.value)


def test_structurally_changed_response_is_reported():
    """响应里没有 list 也没有 code —— 接口结构可能变了，也要报出来。"""
    with pytest.raises(SourceError) as excinfo:
        parse_search('{"something": "else"}', "x")
    assert excinfo.value.reason == "parse_error"


def test_invalid_json_is_reported():
    with pytest.raises(SourceError) as excinfo:
        parse_search("<html>不是 JSON</html>", "x")
    assert excinfo.value.reason == "parse_error"


def test_descriptor_is_usable_without_proxy_or_cookie():
    """Bangumi 是公开 API —— 不需要代理也不需要 Cookie，这是它最大的优点。"""
    from server.sources.bangumi import PLUGIN

    assert PLUGIN.descriptor.needs_proxy is False
    assert PLUGIN.descriptor.needs_cookie is False
