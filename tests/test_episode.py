"""集数解析与里番的剧集结构落盘。

里番没有番号，**集数是它唯一的结构信息** —— 解析错了就进不了 Emby 的剧集库。
所以这里覆盖的形态比其他模块都多。
"""

from __future__ import annotations

import dataclasses

import pytest

from server.classify import classify
from server.episode import EpisodeInfo, kanji_to_number, parse_episode, resolve_episode
from server.models import AggregatedMetadata, ContentType, MediaMetadata
from server.storage import LocalStorage, RootGuard


@pytest.mark.parametrize(
    ("filename", "episode"),
    [
        ("作品名 第03話.mp4", 3),
        ("作品名 第3话.mkv", 3),
        ("作品名 第三集.mp4", 3),
        ("作品名 第１２話.mp4", 12),
        ("作品名 第10回.mp4", 10),
        ("作品名 其の弍.mkv", 2),
        ("作品名 其ノ参.mp4", 3),
        ("[Group] Title #04.mp4", 4),
        ("Title Vol.2.mkv", 2),
        ("Title VOL.10.mkv", 10),
        ("Title EP07.mp4", 7),
        ("Title Episode 12.mp4", 12),
        ("作品名 前編.mp4", 1),
        ("作品名 後編.mp4", 2),
        ("作品名 上巻.mp4", 1),
        ("作品名 下巻.mp4", 2),
    ],
)
def test_episode_markers(filename, episode):
    assert parse_episode(filename).episode == episode


@pytest.mark.parametrize(
    ("filename", "season", "episode"),
    [
        ("Title S02E05.mkv", 2, 5),
        ("Title s1e12.mkv", 1, 12),
        ("Title 2x05.mkv", 2, 5),
        ("Title 第二季 第5話.mp4", 2, 5),
        ("Title 第2期 第03話.mp4", 2, 3),
    ],
)
def test_season_and_episode(filename, season, episode):
    info = parse_episode(filename)
    assert info.season == season
    assert info.episode == episode


@pytest.mark.parametrize(
    "filename",
    [
        "普通的电影名.mp4",
        "MIDV-123-UC.mp4",       # UC 是无码标记，不是集数
        "MIDV-123.mp4",
        "ABC-001 4K.mkv",
    ],
)
def test_no_episode_marker_means_none(filename):
    """解析不出来就是 None，不许猜。"""
    assert parse_episode(filename).episode is None


def test_uc_suffix_is_not_treated_as_an_episode():
    """`-UC` 里的 U/C 很容易被误当成集数或第三集，必须排除。"""
    assert parse_episode("MIDV-123-UC.mp4").episode is None
    assert parse_episode("MIDV-123-C.mp4").cd is None


def test_cd_is_parsed_separately_from_episode():
    info = parse_episode("Title -CD2.mp4")
    assert info.episode is None
    assert info.cd == 2


def test_cd_fallback_fills_episode_and_marks_the_source():
    """没有明确集数标记时才拿分集兜底，并且标出来源，界面上能看出来。"""
    info = resolve_episode(parse_episode("Title -CD2.mp4"))
    assert info.episode == 2
    assert info.episode_source == "分集兜底"


def test_cd_fallback_does_not_override_an_explicit_episode():
    info = resolve_episode(parse_episode("Title 第05話 -CD2.mp4"))
    assert info.episode == 5
    assert info.episode_source == "标记"


def test_cd_fallback_can_be_disabled():
    info = resolve_episode(parse_episode("Title -CD2.mp4"), use_cd_fallback=False)
    assert info.episode is None


def test_letter_parts_map_to_positions():
    assert parse_episode("Title -A.mp4").cd == 1
    assert parse_episode("Title -B.mp4").cd == 2
    assert parse_episode("Title -D.mp4").cd == 4


def test_evidence_is_recorded_for_display():
    info = parse_episode("作品名 第03話.mp4")
    assert info.evidence
    assert "第03話" in info.evidence[0]


# ---------------------------------------------------------------- 汉字数字


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("十", 10), ("十二", 12), ("二十", 20), ("二十三", 23),
        ("三", 3), ("一百", 100), ("弍", 2), ("参", 3), ("零", 0),
        ("12", 12), ("", None), ("abc", None),
    ],
)
def test_kanji_to_number(text, expected):
    assert kanji_to_number(text) == expected


# ---------------------------------------------------------------- 与分类的衔接


def test_classify_carries_episode_for_janime():
    match = classify("/mnt/115/里番/作品名 第03話.mp4")
    assert match.content_type is ContentType.JANIME
    assert match.episode == 3
    assert match.episode_source == "标记"
    assert any("第03話" in item for item in match.evidence)


def test_classify_keeps_number_and_adds_season_episode():
    match = classify("/mnt/115/有码/Title S02E05.mkv")
    assert match.number is None  # 没有番号
    assert (match.season, match.episode) == (2, 5)


def test_episode_info_is_immutable():
    info = EpisodeInfo(episode=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.episode = 4  # type: ignore[misc]


# ---------------------------------------------------------------- 落盘


async def test_janime_writes_tvshow_and_episode_nfo(tmp_path):
    from server.pipeline import write_metadata

    class _Ctx:
        def __init__(self, storage, config):
            self.storage = storage
            self.config = config
            self.http = None

    from server.config import RuntimeConfig

    media = tmp_path / "media"
    media.mkdir()
    storage = LocalStorage(RootGuard([media]), allow_writes=True)
    ctx = _Ctx(storage, RuntimeConfig(organize_enabled=True, dry_run=False))

    aggregated = AggregatedMetadata(
        number=None,
        content_type=ContentType.JANIME,
        metadata=MediaMetadata(title="某里番作品"),
    )
    metadata_dir = media / "meta"

    # 把图片下载关掉，这个用例只关心 NFO
    ctx.config.images.poster = False
    ctx.config.images.thumb = False

    written = await write_metadata(
        ctx,  # type: ignore[arg-type]
        metadata=aggregated.metadata,
        aggregated=aggregated,
        metadata_dir=metadata_dir,
        season=1,
        episode=3,
    )
    names = {p.name for p in written}
    assert "tvshow.nfo" in names
    assert "unknown.nfo" in names  # 没有番号时用 unknown 作文件名

    from xml.etree import ElementTree as ET

    episode_root = ET.fromstring((metadata_dir / "unknown.nfo").read_text(encoding="utf-8"))
    assert episode_root.tag == "episodedetails"
    assert episode_root.findtext("episode") == "3"


async def test_janime_without_episode_writes_only_tvshow(tmp_path):
    """解析不出集数时不写 episode NFO —— 宁缺勿编。"""
    from server.config import RuntimeConfig
    from server.pipeline import write_metadata

    class _Ctx:
        def __init__(self, storage, config):
            self.storage = storage
            self.config = config

    media = tmp_path / "media"
    media.mkdir()
    storage = LocalStorage(RootGuard([media]), allow_writes=True)
    config = RuntimeConfig(organize_enabled=True, dry_run=False)
    config.images.poster = False
    config.images.thumb = False
    ctx = _Ctx(storage, config)

    aggregated = AggregatedMetadata(
        number=None, content_type=ContentType.JANIME, metadata=MediaMetadata(title="某里番作品")
    )
    written = await write_metadata(
        ctx,  # type: ignore[arg-type]
        metadata=aggregated.metadata,
        aggregated=aggregated,
        metadata_dir=media / "meta",
        season=None,
        episode=None,
    )
    assert {p.name for p in written} == {"tvshow.nfo"}
