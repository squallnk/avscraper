"""从图片字节里读出尺寸。

用途是**图片质量把关**：站点给的"剧照/背景图"候选经常是 120x90 的缩略图，
直接当背景图写进媒体库，在 Emby 详情页会被拉伸成一团模糊。
所以下载后先量一下尺寸，太小就换下一张候选。

只认 JPEG 与 PNG —— 这两者覆盖了所有实际用到的站点。
不引入 Pillow 之类的依赖：读个宽高不需要整个图像库。
"""

from __future__ import annotations

import struct

# SOF（Start Of Frame）标记，这些段里带尺寸
_JPEG_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    index = 2
    length = len(data)
    while index < length - 9:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in _JPEG_SOF_MARKERS:
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        # 无载荷的标记
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            index += 2
            continue
        segment_length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        if segment_length < 2:
            return None
        index += 2 + segment_length
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    # 8 字节签名 + 4 字节长度 + "IHDR" + 宽 4 + 高 4
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def image_size(data: bytes) -> tuple[int, int] | None:
    """返回 (width, height)。认不出来返回 None。

    认不出来时调用方应当**放行**而不是拦下 —— 站点偶尔会用 WebP 之类，
    因为读不出尺寸就丢掉一张好图，比放一张小图进来更亏。
    """
    if not data or len(data) < 16:
        return None
    if data[:2] == b"\xff\xd8":
        return _jpeg_size(data)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png_size(data)
    return None


__all__ = ["image_size"]
