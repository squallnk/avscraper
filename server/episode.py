"""集数与季数解析。

里番和番剧的文件名里，集数标记五花八门：`第3話`、`其の弍`、`前編`、`＃04`、`Vol.2`。
这个模块只做一件事：从文件名里把这些标记抠出来。

三条约定：

1. **只是解析，不做判定**。解析不出来就是 `None`，不猜。
2. **分集标记（CD / Part）与集数分开**。`-CD1`/`-CD2` 通常是同一集被切成两半，
   不是两集。但单文件一集的库里，作者往往拿 CD2 当第二集用，
   所以 `resolve_episode()` 在没有更明确的集数标记时才拿它兜底，并在结果里标出来源。
3. **字母分集排除 C**。`-C` 在绝大多数中文命名里表示"中文字幕"，不是第三集。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 异体字与全角数字先归一，否则正则根本匹配不到「其の弍」这种写法
_VARIANT_MAP = str.maketrans({
    "弐": "二", "弍": "二", "貳": "二", "贰": "二",
    "参": "三", "參": "三", "肆": "四", "伍": "五",
    "陸": "六", "陆": "六", "漆": "七", "柒": "七",
    "捌": "八", "玖": "九", "拾": "十", "佰": "百",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
})

# `S02E05` / `2x05` 这类组合标记，季与集一次拿到
_SEASON_EPISODE_PATTERNS: list[tuple[str, int, int]] = [
    (r"\bS(\d{1,2})[\s._-]*E(\d{1,3})\b", 1, 2),
    (r"\b(\d{1,2})\s*x\s*(\d{1,3})\b", 1, 2),
]

_KANJI_DIGITS = {
    "〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100,
}

# 集数标记，按优先级排列。前两条是里番/番剧最常见的形态。
_EPISODE_PATTERNS: list[str] = [
    r"第\s*(\d{1,4}|[一二三四五六七八九十百〇零]{1,4})\s*[話话集回章弾幕]",
    r"其[のノ之乃]\s*(\d{1,4}|[一二三四五六七八九十]{1,3})",
    r"(?<![A-Za-z])EP(?:ISODE)?[\s._-]*0*(\d{1,3})(?!\d)",
    r"(?<![A-Za-z])E[\s._-]0*(\d{1,3})(?!\d)",
    r"[＃#♯]\s*0*(\d{1,3})",
    r"\bVOL\.?[\s._-]*0*(\d{1,3})\b",
]

# 只有顺序含义、没有数字的标记
_EPISODE_KEYWORDS: list[tuple[str, int]] = [
    (r"前[編篇]", 1),
    (r"後[編篇]", 2),
    (r"上巻", 1),
    (r"下巻", 2),
]

# 季数
_SEASON_PATTERNS: list[str] = [
    r"第\s*(\d{1,2}|[一二三四五六七八九十]{1,2})\s*[季期]",
    r"\bS(?:EASON)?\s*0*(\d{1,2})(?![\dA-Za-z])",
]

# 分集（CD / Part / 字母分集）。`C` 排除，因为它通常表示中文字幕。
_CD_PATTERNS: list[str] = [
    r"[-_.\s]CD\s*0*(\d{1,2})(?!\d)",
    r"[-_.\s]PART\s*0*(\d{1,2})(?!\d)",
    r"[-_.\s]([ABD])(?![A-Za-z0-9])",
]

_LETTER_TO_INDEX = {"A": 1, "B": 2, "D": 4}


def normalize_markers(text: str) -> str:
    """归一异体字与全角数字。正则匹配前必须先过这一步。"""
    return Path(text).name.translate(_VARIANT_MAP)


def kanji_to_number(text: str) -> int | None:
    """把「三十」「十二」「其の弍」这类写法转成阿拉伯数字。

    只处理 100 以内的常见写法：`十` = 10、`十二` = 12、`二十` = 20、`二十三` = 23。
    `弍`/`参` 这类异体字先归一。
    """
    normalized = normalize_markers(text.strip())
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)

    total = 0
    current = 0
    for char in normalized:
        value = _KANJI_DIGITS.get(char)
        if value is None:
            return None
        if value == 10:
            total += (current or 1) * 10
            current = 0
        elif value == 100:
            total += (current or 1) * 100
            current = 0
        else:
            current = value
    return total + current


def _to_int(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else kanji_to_number(raw)


@dataclass(frozen=True)
class EpisodeInfo:
    """解析结果。`episode_source` 说明集号是抠出来的还是用分集兜底的。"""

    season: int | None = None
    episode: int | None = None
    cd: int | None = None
    episode_source: str = ""
    evidence: tuple[str, ...] = ()

    @property
    def has_episode(self) -> bool:
        return self.episode is not None


def parse_episode(filename: str) -> EpisodeInfo:
    """从文件名解析季/集/分集。解析不出就返回空结果。"""
    name = normalize_markers(filename)
    evidence: list[str] = []

    # S02E05 / 2x05 优先：它们同时给出季与集，最没有歧义
    for pattern, season_group, episode_group in _SEASON_EPISODE_PATTERNS:
        matched = re.search(pattern, name, re.IGNORECASE)
        if matched is None:
            continue
        season_value = int(matched.group(season_group))
        episode_value = int(matched.group(episode_group))
        return EpisodeInfo(
            season=season_value,
            episode=episode_value,
            cd=None,
            episode_source="SxxEyy",
            evidence=(f"季集标记: {matched.group(0).strip()}",),
        )

    episode: int | None = None
    episode_source = ""
    for pattern in _EPISODE_PATTERNS:
        matched = re.search(pattern, name, re.IGNORECASE)
        if matched is None:
            continue
        value = _to_int(matched.group(1))
        if value is None:
            continue
        episode = value
        episode_source = "标记"
        evidence.append(f"集数标记: {matched.group(0).strip()}")
        break

    if episode is None:
        for pattern, value in _EPISODE_KEYWORDS:
            matched = re.search(pattern, name)
            if matched is not None:
                episode = value
                episode_source = "前後編"
                evidence.append(f"集数标记: {matched.group(0)}")
                break

    season: int | None = None
    for pattern in _SEASON_PATTERNS:
        matched = re.search(pattern, name, re.IGNORECASE)
        if matched is None:
            continue
        value = _to_int(matched.group(1))
        if value is not None:
            season = value
            evidence.append(f"季数标记: {matched.group(0).strip()}")
            break

    cd: int | None = None
    for pattern in _CD_PATTERNS:
        matched = re.search(pattern, name, re.IGNORECASE)
        if matched is None:
            continue
        raw = matched.group(1).upper()
        value = _LETTER_TO_INDEX.get(raw) if raw.isalpha() else _to_int(raw)
        if value is not None:
            cd = value
            evidence.append(f"分集标记: {matched.group(0).strip()}")
            break

    return EpisodeInfo(
        season=season,
        episode=episode,
        cd=cd,
        episode_source=episode_source,
        evidence=tuple(evidence),
    )


def resolve_episode(info: EpisodeInfo, *, use_cd_fallback: bool = True) -> EpisodeInfo:
    """没有明确集数标记时，允许用分集号兜底。

    理由：单文件一集的库里，作者常用 `-CD2` 表示"第二集"；
    真正的"同一集切两半"很少见，而且那种情况下用户可以在模板里用 `{cd}` 区分。
    兜底后 `episode_source` 会标成 `分集兜底`，界面上能看出来。
    """
    if info.episode is not None or not use_cd_fallback or info.cd is None:
        return info
    return EpisodeInfo(
        season=info.season,
        episode=info.cd,
        cd=info.cd,
        episode_source="分集兜底",
        evidence=(*info.evidence, "无明确集数标记，用分集号兜底"),
    )


__all__ = [
    "EpisodeInfo",
    "kanji_to_number",
    "normalize_markers",
    "parse_episode",
    "resolve_episode",
]
