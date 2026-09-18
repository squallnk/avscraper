"""抓回来的元数据到底对不对得上这次查询。

**为什么需要这一步**：里番没有番号，查询只能用作品名，而站点的搜索是模糊匹配。
库里没有这部作品时，它会返回"最像的"那一条 —— 实测 `牝を狩る村` 在 javdb 上
匹配到一部 2012 年的真人片，标题、演员、片商全是别人的。

这种错配**写进媒体库比没有元数据更糟**：封面上是别人，简介是别人，很难发现。
所以宁可标成"需要人工确认"，也不要写下去。
"""

from __future__ import annotations

import re
import unicodedata

from server.models import MediaMetadata

# 归一化时丢掉的分隔符／装饰字符
_NOISE = re.compile(r"[\s\-–—_·・、。，,.!！?？「」『』（）()【】\[\]＃#〜～:：;；'\"]+")

# 命中判定用的最小长度：太短的查询（如"序"）容易误判为匹配
MIN_QUERY_LENGTH = 3


def normalize_for_match(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text)
    return _NOISE.sub("", value).lower()


def query_matches_metadata(query: str, metadata: MediaMetadata) -> bool:
    """查询串是否体现在抓回来的标题里。

    判据取"双向包含"：查询出现在标题里，或者标题出现在查询里。
    后者是为了处理站点标题被截断的情况（标题只剩前半段）。

    查询太短时一律返回 True —— 短串（例如"序"）做包含判断没有意义，
    拦下来的假阳性会比放过的真问题还多。
    """
    q = normalize_for_match(query)
    if len(q) < MIN_QUERY_LENGTH:
        return True

    candidates = [
        normalize_for_match(metadata.title),
        normalize_for_match(metadata.original_title),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if q in candidate or candidate in q:
            return True
    return False


__all__ = ["MIN_QUERY_LENGTH", "normalize_for_match", "query_matches_metadata"]
