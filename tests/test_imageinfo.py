"""图片尺寸解析，以及背景图的尺寸把关。

起因：javdb 的"剧照"候选全是 120x90 的缩略图（真地址是 `_s_N.jpg`，
去掉 `_s_` 的大图版本返回 403，站点根本没提供），
直接当背景图写进媒体库，在 Emby 详情页会被拉伸成一团模糊。
"""

from __future__ import annotations

import struct

import pytest

from server.imageinfo import image_size


def fake_jpeg(width: int, height: int) -> bytes:
    """构造一个只带 SOF0 的最小 JPEG —— 尺寸解析只需要这个段。"""
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", height, width)
    sof0 += b"\x03" + b"\x01\x11\x00" * 3
    return b"\xff\xd8" + sof0 + b"\xff\xd9"


def fake_png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"


@pytest.mark.parametrize(("w", "h"), [(120, 90), (800, 538), (1, 1), (3840, 2160)])
def test_jpeg_size(w, h):
    assert image_size(fake_jpeg(w, h)) == (w, h)


@pytest.mark.parametrize(("w", "h"), [(800, 538), (120, 90)])
def test_png_size(w, h):
    assert image_size(fake_png(w, h)) == (w, h)


def test_unknown_format_returns_none():
    """认不出格式返回 None —— 调用方应当放行，而不是因为读不出尺寸就丢图。"""
    assert image_size(b"RIFF....WEBPVP8 ") is None
    assert image_size(b"") is None
    assert image_size(b"\xff\xd8") is None          # 只有 SOI，没有尺寸段
    assert image_size(b"not an image at all!!") is None


def test_truncated_jpeg_does_not_crash():
    """截断的数据不能抛异常，只能返回 None。"""
    data = fake_jpeg(800, 538)[:8]
    assert image_size(data) is None
