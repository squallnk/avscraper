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

from server.episode import parse_episode
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


def episode_conflicts(episode: int | None, metadata: MediaMetadata) -> int | None:
    """标题里自带的集/卷号与文件解析出的集号是否冲突。

    站点搜索是模糊的，同一部**多卷** OVA 会返回"某一卷"的页面，而查询词
    （剪掉了卷号的作品名）确实是那个标题的子串 —— 所以包含判定拦不住。
    实测的三例：

        文件 ＃1     -> 抓回「OVA おしかけ！爆乳ギャルハーレム性活 ＃2」
        文件 Vol.1   -> 抓回「ながちち永井さん THE ANIMATION Vol.2 ドキドキ…」
        文件 第二話  -> 抓回「危険な森 おにごっこ 第一話 「待っててね。お姉ちゃん」」

    三例都判成了 success 并写进媒体库：标题、封面、简介全是另一卷的。

    返回冲突的那个集号；标题里没有集号标记、或与文件一致时返回 `None`。
    判断不出来就放行 —— 这里只拦"明确矛盾"，不猜。
    """
    if episode is None:
        return None
    title = metadata.title or ""
    if not title:
        return None
    title_episode = parse_episode(title).episode
    if title_episode is None or title_episode == episode:
        return None
    return title_episode


__all__ = [
    "MIN_QUERY_LENGTH",
    "episode_conflicts",
    "normalize_for_match",
    "query_matches_metadata",
]
