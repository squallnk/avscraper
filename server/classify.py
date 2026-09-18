"""番号解析与内容类型判定。

两条判据：

1. **番号**（`parse_number`）—— 适用于日本有码 / 无码 / 素人 / FC2 / 国产 / 欧美。
2. **路径关键词**（`detect_by_path`）—— 里番没有番号（getchu 商品是数字 id），
   只能靠目录名或文件名里的关键词判定；国产、欧美也常需要路径兜底。

判定结果都带 `evidence`，WebUI 直接展示命中的规则，出问题时不用翻日志。

匹配顺序是有讲究的（顺序错了会把类型判错）：
FC2  ->  素人标签  ->  无码日期号  ->  DMM 配信形式  ->  标准前缀号
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from server.models import CONTENT_TYPE_LABELS, ContentType, MatchInfo

# ---------------------------------------------------------------------------
# 预处理
# ---------------------------------------------------------------------------

_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)

# 中文字幕 / 分辨率 / 分集等尾缀，参与匹配前先摘掉，避免污染番号
_NOISE_SUFFIXES = re.compile(
    r"[-_.\s]*("
    r"CD\d|PART\d|DISC\d|"
    r"[45]K|8K|2160P|1080P|720P|HD|FHD|UHD|"
    r"UC|U|C|CH|CHS|CHT|SC|TC|JP|JPN|ENG|"
    r"H265|H264|HEVC|AVC|X264|X265|10BIT|8BIT|"
    r"AAC|AC3|DTS|FLAC"
    r")(?=[-_.\s]|$)",
    re.IGNORECASE,
)

# 无码标记：破解 / 流出 / 无修正，以及 -UC / -U 尾缀
_UNCENSORED_WORDS = (
    "无码", "無碼", "无修正", "無修正", "无修", "流出", "破解",
    "uncensored", "leaked", "cracked", "no_mosaic", "nomosaic",
)
_UNCENSORED_SUFFIX_RE = re.compile(r"[-_.\s](UC|U)(?=[-_.\s]|$)", re.IGNORECASE)

# 素人标签。必须带标签名，否则 300MIUM-123 会被 DMM 规则抢走
_AMATEUR_RE = re.compile(
    r"\b(\d{2,3})?(SIRO|LUXU|GANA|MIUM|MGS|ARA|MAAN|SHURI|KNB|JAC|LUXU)[-_\s]?(\d{2,5})\b",
    re.IGNORECASE,
)

_CHINESE_MARKERS = (
    "麻豆", "天美", "星空", "蜜桃", "精东", "乌鸦", "皇家", "蜜桃影像",
    "国产", "國產", "爱豆", "愛豆", "杏吧", "swag", "91cm", "91porn", "onlyfans",
)

_WESTERN_MARKERS = (
    "欧美", "歐美", "brazzers", "blacked", "vixen", "realitykings", "bangbros",
    "pornhub", "xvideo", "digitalplayground", "naughtyamerica",
)

# 里番：路径/文件名关键词。没有番号，只能靠这些。
_JANIME_MARKERS = (
    "里番", "裡番", "裏番", "成年コミック", "アニメ", "アニメーション",
    "ova", "oad", "ona", "hentai", "同人アニメ", "美少女ゲーム",
    # 英文形态。真实文件里 `XXX THE ANIMATION` 极常见（ピンクパイナップル 的命名习惯），
    # 只认片假名会漏掉一大片。
    "the animation",
)

# 里番制作组。真实放的里番文件名常常**只有制作组、没有「アニメ」这类关键词**，
# 例如 `[251128][nur]...`。没有这份名单就会判成 unknown。
#
# 匹配有前提：必须出现在 `[日期][制作组]` 这种方括号结构里（见 _RELEASE_PREFIX_RE），
# 而不是在文件名任意位置命中 —— 否则像 `Seven` `Milky` `Lune` 这种通用词
# 会把普通影片误判成里番。
_JANIME_STUDIOS = (
    "queen bee", "king bee", "pink pineapple", "ピンクパイナップル",
    "bunny walker", "ばにぃうぉ～か～", "ばにぃうぉーかー",
    "collaboration works", "studio fantasia", "schoolzone", "スクールゾーン",
    "majin", "魔人", "poro", "a1c", "エイ・ワン・シー", "arms", "lilith",
    "bootleg", "t-rex", "pixy", "milky", "pashmina", "gold bear", "discovery",
    "nur", "suzuki mirano", "ms pictures", "lune", "seven", "セブン",
    "chichinoya", "ちちのや", "mary jane", "メリー・ジェーン",
    "celeb", "セレブ", "breakbottle", "digital works",
    "green bunny", "グリーンバニー", "vanilla", "バニラ", "animac", "アニマック",
    "media bank", "メディアバンク", "とらのあな", "toranoana", "37℃",
    "white bear", "ホワイトベア", "hills", "ヒルズ", "オフィス8番",
)

# `[251114][Queen Bee]作品名...` —— 日期前缀 + 制作组方括号，是里番发布的典型结构
_RELEASE_PREFIX_RE = re.compile(r"^\s*\[\s*(?:\d{6}|\d{8})\s*\]\s*\[([^\]]{1,40})\]")

# 集数标记，出现即强烈暗示这是分集动画而不是单体影片
_EPISODE_MARKERS = re.compile(
    r"第\s*[\d一二三四五六七八九十百]+\s*[話话集回章弾幕]|"
    r"其[のノ之乃]\s*[\d一二三四五六七八九十]|"
    r"前編|後編|前篇|後篇|上巻|下巻|"
    r"\bEP?\s*\d{1,3}\b|\bVOL\.?\s*\d{1,3}\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 番号模式
# ---------------------------------------------------------------------------

_FC2_RE = re.compile(r"\bFC2[-_\s]*(?:PPV[-_\s]*)?(\d{5,7})\b", re.IGNORECASE)
_UNCENSORED_DATE_RE = re.compile(r"\b(\d{6})[-_](\d{3})\b")
_DMM_DELIVERY_RE = re.compile(r"\b(\d{2,6})([A-Z]{2,10})(\d{2,5})\b")
_STANDARD_RE = re.compile(r"\b([A-Z]{2,10})[-_\s]?(\d{2,5})\b")

# 前缀黑名单：这些是容器/编码标签，不是厂牌
_PREFIX_BLOCKLIST = {
    "MP4", "MKV", "AVI", "WMV", "MOV", "TS", "RMVB", "WEB", "WEBRIP", "HDTV",
    "BD", "BDRIP", "DVD", "DVDISO", "FHD", "UHD", "SD", "HEVC", "AVC", "X264",
    "X265", "AAC", "DTS", "FLAC", "CHS", "CHT", "JPN", "ENG", "UNCENSORED",
    "LEAKED", "CRACKED", "CD", "PART", "DISC", "EP", "VOL", "NO", "THE",
}


def normalize(text: str) -> str:
    """全角转半角、NFKC 归一、去扩展名、压空白、转大写。"""
    value = unicodedata.normalize("NFKC", text)
    value = value.translate(_FULLWIDTH)
    value = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", value)
    value = re.sub(r"[\[\]【】（）()]", " ", value)
    value = _NOISE_SUFFIXES.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.upper()


def has_uncensored_marker(raw_name: str) -> bool:
    """在**未清洗**的文件名上判无码标记，否则 `-UC` 会被噪音规则先摘掉。"""
    lowered = raw_name.lower()
    if any(word in lowered for word in _UNCENSORED_WORDS):
        return True
    return bool(_UNCENSORED_SUFFIX_RE.search(raw_name))


def _type_from_markers(raw_name: str) -> tuple[ContentType, list[str]]:
    lowered = raw_name.lower()
    evidence: list[str] = []
    content_type = ContentType.CENSORED
    if has_uncensored_marker(raw_name):
        content_type = ContentType.UNCENSORED
        evidence.append("文件名含无码/破解/流出标记")
    if any(marker in lowered for marker in _CHINESE_MARKERS):
        content_type = ContentType.CHINESE
        evidence.append("文件名含国产平台标记")
    elif any(marker in lowered for marker in _WESTERN_MARKERS):
        content_type = ContentType.WESTERN
        evidence.append("文件名含欧美平台标记")
    return content_type, evidence


# ---------------------------------------------------------------------------
# 番号
# ---------------------------------------------------------------------------


def parse_number(filename: str) -> MatchInfo:
    """从文件名解析番号与内容类型。"""
    raw = Path(filename).name
    value = normalize(raw)
    content_type, evidence = _type_from_markers(raw)

    fc2 = _FC2_RE.search(value)
    if fc2:
        return MatchInfo(
            number=f"FC2-PPV-{fc2.group(1)}",
            content_type=ContentType.FC2,
            category_label=CONTENT_TYPE_LABELS[ContentType.FC2],
            confidence=0.98,
            evidence=[f"FC2 模式: {fc2.group(0)}"],
        )

    amateur = _AMATEUR_RE.search(value)
    if amateur:
        digits, label, serial = amateur.group(1) or "", amateur.group(2).upper(), amateur.group(3)
        number = f"{digits}{label}-{serial}"
        return MatchInfo(
            number=number,
            content_type=ContentType.AMATEUR,
            category_label=CONTENT_TYPE_LABELS[ContentType.AMATEUR],
            confidence=0.9,
            evidence=[f"素人标签号: {amateur.group(0)}"],
        )

    uncensored_date = _UNCENSORED_DATE_RE.search(value)
    if uncensored_date:
        if content_type is ContentType.CENSORED:
            content_type = ContentType.UNCENSORED
            evidence.append("无码日期番号格式")
        return MatchInfo(
            number=f"{uncensored_date.group(1)}-{uncensored_date.group(2)}",
            content_type=content_type,
            category_label=CONTENT_TYPE_LABELS[content_type],
            confidence=0.8,
            evidence=[*evidence, f"日期番号模式: {uncensored_date.group(0)}"],
        )

    dmm = _DMM_DELIVERY_RE.search(value)
    if dmm:
        return MatchInfo(
            number=f"{dmm.group(2)}-{dmm.group(3)}",
            content_type=content_type,
            category_label=CONTENT_TYPE_LABELS[content_type],
            confidence=0.75,
            evidence=[*evidence, f"DMM 配信形式: {dmm.group(0)}"],
        )

    for match in _STANDARD_RE.finditer(value):
        prefix, digits = match.group(1), match.group(2)
        if prefix in _PREFIX_BLOCKLIST:
            continue
        return MatchInfo(
            number=f"{prefix}-{digits}",
            content_type=content_type,
            category_label=CONTENT_TYPE_LABELS[content_type],
            confidence=0.9,
            evidence=[*evidence, f"标准番号模式: {match.group(0)}"],
        )

    return MatchInfo(
        number=None,
        content_type=ContentType.UNKNOWN if not evidence else content_type,
        category_label=CONTENT_TYPE_LABELS[ContentType.UNKNOWN if not evidence else content_type],
        confidence=0.0,
        evidence=[*evidence, "未匹配到任何番号模式"],
    )


# ---------------------------------------------------------------------------
# 路径关键词
# ---------------------------------------------------------------------------


def _release_studio(filename: str) -> str | None:
    """从 `[日期][制作组]作品名` 结构里取制作组，并核对是否在名单里。

    只认这个结构，是为了避免"文件名里随便出现 Seven 就当里番"这类误判。
    """
    matched = _RELEASE_PREFIX_RE.match(filename)
    if matched is None:
        return None
    raw = matched.group(1).strip()
    lowered = raw.lower()
    for studio in _JANIME_STUDIOS:
        if studio in lowered:
            return raw
    return None


def detect_by_path(path: str | Path) -> MatchInfo | None:
    """用整条路径的关键词判定内容类型。命中才返回。

    里番没有番号，这是它唯一的分类入口。
    """
    parts = Path(path).parts
    haystack = " ".join(parts)
    lowered = haystack.lower()
    evidence: list[str] = []

    for marker in _JANIME_MARKERS:
        if marker in lowered:
            evidence.append(f"路径含里番标记: {marker}")

    if not evidence:
        studio = _release_studio(Path(path).name)
        if studio:
            evidence.append(f"里番制作组标记: [{studio}]")

    if not evidence and _EPISODE_MARKERS.search(haystack):
        evidence.append("路径含集数标记（第N話/其のN/前編 等）")

    if evidence:
        return MatchInfo(
            number=None,
            content_type=ContentType.JANIME,
            category_label=CONTENT_TYPE_LABELS[ContentType.JANIME],
            confidence=0.7,
            evidence=evidence,
        )

    if any(marker in lowered for marker in _UNCENSORED_WORDS):
        return MatchInfo(
            content_type=ContentType.UNCENSORED,
            category_label=CONTENT_TYPE_LABELS[ContentType.UNCENSORED],
            confidence=0.6,
            evidence=["路径含无码标记（无码/流出/破解等）"],
        )

    if any(marker in lowered for marker in _CHINESE_MARKERS):
        return MatchInfo(
            content_type=ContentType.CHINESE,
            category_label=CONTENT_TYPE_LABELS[ContentType.CHINESE],
            confidence=0.6,
            evidence=["路径含国产标记"],
        )

    if any(marker in lowered for marker in _WESTERN_MARKERS):
        return MatchInfo(
            content_type=ContentType.WESTERN,
            category_label=CONTENT_TYPE_LABELS[ContentType.WESTERN],
            confidence=0.6,
            evidence=["路径含欧美标记"],
        )

    return None


def _with_episode(info: MatchInfo, path: str | Path) -> MatchInfo:
    """把季集解析结果并进分类结果。里番没有番号，集数是它唯一的结构信息。"""
    from server.episode import parse_episode, resolve_episode

    episode = resolve_episode(parse_episode(str(path)))
    if not episode.evidence:
        return info
    return info.model_copy(
        update={
            "season": episode.season,
            "episode": episode.episode,
            "cd": episode.cd,
            "episode_source": episode.episode_source,
            "evidence": [*info.evidence, *episode.evidence],
        }
    )


def classify(path: str | Path) -> MatchInfo:
    """综合判定：番号优先，命中不了再用路径关键词。"""
    by_name = parse_number(Path(path).name)
    by_path = detect_by_path(path)

    if by_name.number:
        # 番号给出的是弱类型（默认有码）时，路径证据更可信：
        # 同一个番号的无码版常被放在 无码/ 目录下，国产作品也常常只有目录名能看出来。
        weak = by_name.content_type in (ContentType.CENSORED, ContentType.UNKNOWN)
        if by_path and weak and by_path.content_type is not by_name.content_type:
            return _with_episode(
                MatchInfo(
                    number=by_name.number,
                    content_type=by_path.content_type,
                    category_label=by_path.category_label,
                    confidence=max(by_name.confidence, by_path.confidence),
                    evidence=[*by_name.evidence, *by_path.evidence],
                ),
                path,
            )
        return _with_episode(by_name, path)

    if by_path:
        return _with_episode(
            MatchInfo(
                number=None,
                content_type=by_path.content_type,
                category_label=by_path.category_label,
                confidence=by_path.confidence,
                evidence=[*by_name.evidence, *by_path.evidence],
            ),
            path,
        )

    return _with_episode(by_name, path)


def is_video_file(name: str, extensions: set[str] | None = None) -> bool:
    exts = extensions or {
        ".mp4", ".mkv", ".avi", ".wmv", ".mov", ".flv", ".rmvb", ".ts",
        ".m2ts", ".webm", ".iso", ".m4v", ".mpg", ".mpeg", ".vob",
    }
    return Path(name).suffix.lower() in exts
