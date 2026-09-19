r"""番号 -> DMM content id -> 官方包装图 URL。

**为什么不走店铺页**：店铺页有地域+年龄墙（302 → /age_check → /en/age_check），
但图片 CDN 完全没有（实测 200，连 Referer 都不用）。所以只要能拼出 URL，
就能拿到 DMM 的 2184x1468 官方包装图，不需要代理、不需要 Cookie、不需要账号。

规则：小写、去掉连字符、数字补零到 5 位。
"""

from __future__ import annotations

import pytest

from server.dmm import HD_CDN, hd_cover_urls, to_content_id


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("MIDV-123", "midv00123"),
        ("SSIS-001", "ssis00001"),
        ("ABC-1", "abc00001"),
        ("midv-123", "midv00123"),
        # 没有连字符、已经补过零、全角都要一样
        ("SSIS001", "ssis00001"),
        ("MIDV-00123", "midv00123"),
        ("ＭＩＤＶ－１２３", "midv00123"),
        ("  MIDV-123  ", "midv00123"),
    ],
)
def test_to_content_id(number, expected):
    assert to_content_id(number) == expected


@pytest.mark.parametrize(
    "number",
    [
        None,
        "",
        # FC2 与素人那类号码夹着数字或超长 —— 本来也不在 DMM 的 digital/video 里
        "FC2-PPV-123456",
        "FC2PPV-123456",
        "T28-123",
        # 数字比 5 位还长，不是这套 id
        "MIDV-1234567",
        "MIDV",
        "123",
    ],
)
def test_underivable_numbers_return_none(number):
    assert to_content_id(number) is None
    assert hd_cover_urls(number) == []


def test_hd_cover_url_uses_the_new_cdn():
    r"""**必须用新 CDN**：老 CDN 对不存在的 id 会 302 到
    `pics.dmm.com/mono/noimage/...` 的灰色占位图，而 httpx 默认跟随重定向 ——
    那会把一张 no-image 当海报写下去。新 CDN 是硬 404，会被 raise_for_status 拦下。"""
    urls = hd_cover_urls("MIDV-123")
    assert urls == [f"{HD_CDN}/digital/video/midv00123/midv00123pl.jpg"]
    assert "pics.dmm.co.jp" not in urls[0]
