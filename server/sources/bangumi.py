"""Bangumi (bgm.tv) —— 中文动画数据库。

**为什么用它**：里番没有番号，日文站给的标题和简介对中文库不直接可用。
Bangumi 每条都带 `name_cn`（中文名），并且实测**搜索精度极高** ——
拿真实文件名的清洗结果去搜，8/8 第一条就是正确条目，包括 javdb 上查不到的
`牝を狩る村`。这对"中文命名 + 中文字幕"的库是直接收益。

**两个接口的差别（实测，很重要）**：

| 接口 | 搜「勇者姫ミリア」的第一条 |
|---|---|
| `POST /v0/search/subjects`（正式） | 気絶勇者と暗殺姫 —— **完全不相干** |
| `GET /search/subject/{kw}`（旧版） | 勇者姫ミリア —— **精确命中** |

新式接口在做模糊/语义排序，不是字面匹配 —— 这跟 javdb 的错配是同一类问题。
所以这里用旧版接口做搜索。

**已知风险**：旧版接口是遗留接口，官方可能下线。所以解析失败一律抛
`SourceError`（报 `parse_error`）而不是静默返回 None —— 这样在记录页上能看出
"是这个源坏了"而不是"这部作品没有"。

**详情接口的实测结论（2026-09，三个 id 对照）**：

| 条目 | 类型 | `GET /subject/{id}?responseGroup=large` |
|---|---|---|
| 536363 気絶勇者と暗殺姫 | 普通番剧 | 200，返回 staff / crt / eps / rating / rank |
| 584818 牝を狩る村 | 里番 | **404** |
| 421743 1LDK＋J系 | 里番 | **404** |

**旧版详情接口不返回 NSFW 条目** —— 也就是恰好不返回我们最需要的那一类。
所以这里不做第二次请求：简介与发售日直接取搜索结果自带的字段
（见 `parse_search`），代价是必须让搜索接口返回它们。

另外实测该详情响应里**没有 `tags` 也没有 `infobox`**，
所以厂牌/中文别名也别指望从这个接口拿。
"""

from __future__ import annotations

import json
from urllib.parse import quote

from server.models import ContentType, MediaMetadata, SourceDescriptor
from server.sources.base import FetchContext, SourceError, SourcePlugin

BASE = "https://api.bgm.tv"
LEGACY_SEARCH = BASE + "/search/subject/{keyword}?type=2&responseGroup=small"

# Bangumi 会拦默认 UA，必须带一个标明用途的标识
API_USER_AGENT = "avscraper/0.1 (https://github.com/squallnk/avscraper)"

# 动画类型（1=书籍 2=动画 3=音乐 4=游戏 6=三次元）
SUBJECT_TYPE_ANIME = 2

_IMAGE_KEY_ORDER = ("large", "common", "medium", "small", "grid")


def _https(url: str | None) -> str | None:
    """旧版接口给的图片地址是 http，统一升到 https。

    站点本身支持 https（新版接口返回的就是 https），
    混用会带来混合内容与重定向的麻烦。
    """
    if not url:
        return None
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def parse_search(payload: str, query: str) -> MediaMetadata | None:
    """解析旧版搜索响应。取第一条；没有结果返回 None。

    纯函数，配合 `tests/fixtures/bgm_*.json` 做回归。
    """
    try:
        data = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise SourceError(f"Bangumi 返回的不是合法 JSON: {exc}", reason="parse_error") from exc

    if not isinstance(data, dict):
        raise SourceError("Bangumi 响应结构异常", reason="parse_error")

    # 旧版接口出错时返回 {"code":404,"error":"Not Found"}，没有 list 字段
    if "list" not in data:
        if data.get("code"):
            raise SourceError(
                f"Bangumi 旧版搜索接口返回 {data.get('code')}：{data.get('error')}",
                reason="parse_error",
            )
        raise SourceError("Bangumi 响应里没有 list 字段，接口结构可能已变", reason="parse_error")

    items = data.get("list") or []
    if not items:
        return None

    item = items[0]
    name = (item.get("name") or "").strip()
    name_cn = (item.get("name_cn") or "").strip()

    # 简介与发售日：搜索条目里就带这两个字段，不用再请求详情页
    # （详情页恰好对 NSFW 条目返回 404，见模块文档）。
    # 实测 responseGroup=small 时这两个字段是空串，large 时才有值，
    # 所以这里按"有就用、没有就算了"处理，不额外发请求。
    summary = (item.get("summary") or "").strip()
    air_date = (item.get("air_date") or "").strip()

    images = item.get("images") or {}
    poster = None
    for key in _IMAGE_KEY_ORDER:
        if images.get(key):
            poster = _https(images[key])
            break

    return MediaMetadata(
        number=None,
        # 中文库优先用中文名做标题，日文原名进 original_title
        title=name_cn or name or None,
        original_title=name or None,
        poster_url=poster,
        plot=summary or None,
        release_date=air_date or None,
        year=int(air_date[:4]) if air_date[:4].isdigit() else None,
        website=item.get("url") or (f"https://bgm.tv/subject/{item['id']}" if item.get("id") else None),
    )


class BangumiSource(SourcePlugin):
    descriptor = SourceDescriptor(
        id="bangumi",
        name="Bangumi",
        homepage="https://bgm.tv",
        supports=[ContentType.JANIME],
        needs_proxy=False,
        note="中文动画库。搜索精度高，提供中文名与多档封面。用旧版搜索接口（新版模糊排序会错配）。",
    )

    async def fetch(self, client: object, ctx: FetchContext) -> MediaMetadata | None:
        query = (ctx.query or ctx.number or "").strip()
        if not query:
            return None

        get = client.get
        url = LEGACY_SEARCH.format(keyword=quote(query))
        result = await get(url, source=self.descriptor.id, headers={"User-Agent": API_USER_AGENT})
        if result.status == 404 or not result.text:
            return None
        return parse_search(result.text, query)


PLUGIN = BangumiSource()
