"""DMM（FANZA）包装图：由番号直接拼出 URL，不走店铺页。

**为什么值得做**：DMM 的官方包装图在新 CDN 上是 **2184x1468**（实测
`midv00123pl.jpg` 997 KB），而 javdb 给的是 800x538 —— 7 倍像素。

**而且它不需要突破任何限制**（2026-09 实测）：

| 端点 | 结果 |
|---|---|
| `www.dmm.co.jp/digital/...` 店铺页 | 302 → `/age_check/` → 跟到底变成 `/en/age_check/`（按 IP 判区域） |
| `pics.dmm.co.jp` 老 CDN | 200，连 Referer 都不用 |
| `awsimgsrc.dmm.co.jp/pics_dig` 新 CDN | 200，同上 |
| `api.dmm.com/affiliate/v3` | 400，内容只是 api_id 无效 —— 端点可达 |

也就是**墙只挡店铺 UI**，图片 CDN 与 API 都不做地域限制。

番号转 content id 的规则：小写、去掉连字符、数字补零到 5 位。
实测 13 个 id（ssis / sone / mide / midv / ipx / pred / juq 各若干）全部 200，
而不存在的 id 一律 **硬 404**。

**只用新 CDN**：老 CDN 对不存在的 id 会 302 到 `pics.dmm.com/mono/noimage/...`
的灰色占位图 —— httpx 默认跟随重定向，那会把一张 no-image 当成海报写下去。
新 CDN 是硬 404，`raise_for_status()` 直接拦下，自动回退到原来的海报候选。

覆盖不是 100%：有的厂牌 id 空间不一样（实测 stars / fsdss / abw 那一批推不出来）。
推不出来就是 404 → 回退，**不会写错东西**。
"""

from __future__ import annotations

import re
import unicodedata

# label 只允许**纯字母**，数字 1~5 位。
# 这一条顺手把 FC2 / 素人 那类号码挡在外面（`FC2-PPV-123456`、`SIRO-4567`
# 里都夹着数字或超长），而它们本来也不在 DMM 的 digital/video 里。
_LABEL_NUMBER = re.compile(r"^(?P<label>[A-Za-z]{2,10})[-_ ]?(?P<number>\d{1,5})$")

CONTENT_ID_DIGITS = 5

# 新 CDN。老 CDN 会把不存在的 id 重定向到占位图，所以不能用 —— 见模块文档。
HD_CDN = "https://awsimgsrc.dmm.co.jp/pics_dig"


def to_content_id(number: str | None) -> str | None:
    """番号转 DMM content id：`MIDV-123` -> `midv00123`。推不出来返回 None。"""
    if not number:
        return None
    text = unicodedata.normalize("NFKC", number).strip()
    matched = _LABEL_NUMBER.match(text)
    if matched is None:
        return None
    label = matched.group("label").lower()
    return f"{label}{matched.group('number').zfill(CONTENT_ID_DIGITS)}"


def hd_cover_urls(number: str | None) -> list[str]:
    """这个番号在 DMM 上的官方包装图候选（通常一张）。推不出来返回空列表。"""
    content_id = to_content_id(number)
    if content_id is None:
        return []
    return [f"{HD_CDN}/digital/video/{content_id}/{content_id}pl.jpg"]


__all__ = ["CONTENT_ID_DIGITS", "HD_CDN", "hd_cover_urls", "to_content_id"]
