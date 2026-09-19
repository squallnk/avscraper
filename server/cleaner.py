"""刮削查询名的清洗。

**为什么必须有这一步**：里番没有番号，查询只能靠作品名。而文件名是"发布形态"的：

    [251128][魔人]勇者姫ミリア 第四話 砂漠の町のオークション！ ○○堕ちとメイドご奉仕.chs.mp4

把这一整串丢给站点搜索，一个都搜不到。必须先把发布信息剥掉，只留作品名：

    勇者姫ミリア

这是部署后跑了真实文件才暴露的问题 —— 分类判对了（janime），但刮削全部 not_found，
因为查询串里带着日期、制作组、集数、副标题、语言后缀。

清洗规则参考同类工具的做法（去日期前缀 / 去制作组方括号 / 去 OVA 标记 / 去副标题 /
去集数标记及其后续文本 / 去末尾作者方括号）。
"""

from __future__ import annotations

import re
from pathlib import Path

from server.episode import normalize_markers

VIDEO_EXT = re.compile(r"\.(mp4|mkv|avi|wmv|mov|flv|rmvb|ts|m2ts|webm|m4v|iso)$", re.I)

# 语言/字幕后缀，出现在扩展名之前
LANGUAGE_SUFFIXES = tuple(
    re.compile(p, re.I)
    for p in (
        r"\.chs\b", r"\.cht\b", r"\.chi\b", r"\.tc\b", r"\.sc\b",
        r"\.jpn\b", r"\.jap\b", r"\.eng\b", r"\.zho\b", r"\.und\b",
        r"\.[a-z]{2}-[a-z]{2}\b",
    )
)

DATE_PREFIX = re.compile(r"^\s*\[\s*(?:\d{6}|\d{8})\s*\]")
LEADING_BRACKET = re.compile(r"^\s*\[[^\]]{1,40}\]")
TRAILING_BRACKET = re.compile(r"\s*\[[^\]]{1,40}\]\s*$")

ANIMATION_MARKERS = tuple(
    re.compile(p, re.I)
    for p in (r"\bOVA\b", r"\bOAD\b", r"\bONA\b", r"\bTHE\s+ANIMATION\b", r"\bANIMATION\b")
)

SUBTITLE_WRAPPERS = tuple(
    re.compile(p) for p in (r"～[^～]{2,}～", r"〜[^〜]{2,}〜", r"「[^」]+」", r"『[^』]+』")
)

# 发行方在作品名后面加的宣传尾巴。
#
# 实测（三份真实响应）：
#   文件名  [250704][AnimeFesta]彼女がセパレートをまとう理由を見る
#   带尾巴搜  -> results=15，第一条「劇場版 魔法少女まどか☆マギカ [新編] 叛逆の物語」（完全无关）
#   去掉尾巴  -> results=26，第一条 id 568230「彼女がセパレートをまとう理由」，
#                中文名「女友穿上两截式的原因」，air_date 2025-07-04 —— 与文件名里的
#                [250704] 对得上，正是这一部
#
# 也就是说 bgm 的旧版搜索是**整串匹配**：匹配不上就退化成一堆热门条目。
# 差一个词就从"精确命中"变成"完全无关"，所以这个尾巴必须剪掉。
#
# 风险是有界的：万一某个作品的正名真的以「を見る」结尾，剪掉后查询变成它的前缀，
# 整串匹配失败 -> 退化成热门条目 -> 被 query_matches_metadata 拦下 -> 待确认。
# 结果是"要人工看一眼"，不是"悄悄写错"。
PROMO_SUFFIX = re.compile(r"\s*を[見み]る\s*$")

# 集数标记：命中即**从这里截断**，后续都是副标题
EPISODE_CUT = re.compile(
    r"第\s*[\d一二三四五六七八九十百〇零]+\s*[話话集回章弾幕巻卷]"
    r"|[＃#♯]\s*\d+"
    r"|\bVol\.?\s*\d+"
    r"|其[のノ之乃]\s*[\d一二三四五六七八九十]"
    r"|前編|後編|前篇|後篇|上巻|下巻"
    r"|\bEP?\s*\d{1,3}\b",
    re.I,
)


def clean_query_name(filename: str) -> str:
    """把发布形态的文件名清洗成可用于搜索的作品名。清洗不出东西时返回空串。"""
    text = Path(filename).name
    text = VIDEO_EXT.sub("", text)
    for pattern in LANGUAGE_SUFFIXES:
        text = pattern.sub("", text)

    text = DATE_PREFIX.sub("", text)
    text = LEADING_BRACKET.sub("", text)  # 制作组

    for pattern in ANIMATION_MARKERS:
        text = pattern.sub(" ", text)
    for pattern in SUBTITLE_WRAPPERS:
        text = pattern.sub(" ", text)

    # 集数标记之后的内容（副标题）全部丢掉。
    #
    # 匹配在**归一化后**的文本上做，截断却落在**原文**上 —— 因为 `normalize_markers`
    # 是逐字符 1:1 映射（`str.translate`），下标两边通用。
    # 这样做有两个好处：
    # 1. `其の弍` 与 `其の二` 行为一致（之前只有后者会被截断）；
    # 2. 标题里恰好含「参」「陸」这类异体字时**不会被改写**，只用来定位。
    #    如果直接对原文做整篇归一化，`参上！` 会变成 `三上！`，查询词就废了。
    matched = EPISODE_CUT.search(normalize_markers(text))
    if matched:
        text = text[: matched.start()]

    # 放在截断**之后**：尾巴总是在标记后面
    text = PROMO_SUFFIX.sub("", text)

    text = TRAILING_BRACKET.sub("", text)  # 末尾作者方括号
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -_　·・")


def episode_marker(filename: str) -> str:
    r"""文件名里那个"集/卷标记"的**原文**（如「第5話」「前編」「＃1」）。没有就返回空串。

    清洗查询词时会把它连同后面的副标题一起剪掉（搜索时剪掉更容易命中），
    但**挑候选时它有用**：getchu 搜「1LDK＋J系 …」会把整个系列的每一话都列出来，
    而列表是按发售日排的，取第一条多半是别的卷。带上标记去挑就能命中同一卷。

    返回原文而不是归一化后的：站点标题里写的是「第5話」，拿「第5话」去比就白比了。
    归一化只用来**定位**，截取仍在原文上做（`normalize_markers` 是逐字符 1:1 映射）。
    """
    text = VIDEO_EXT.sub("", Path(filename).name)
    matched = EPISODE_CUT.search(normalize_markers(text))
    if matched is None:
        return ""
    return text[matched.start() : matched.end()].strip()


__all__ = ["clean_query_name", "episode_marker"]
