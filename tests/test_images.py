"""图片下载的选择逻辑与落盘行为。

这一块最容易出的两种错：
1. 关掉的类型还是被下载了（用户明确说过剧照要能选）；
2. 某一张候选图挂了就整组失败，而源明明给了几十张候选。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.config import ImageDownloadConfig, RuntimeConfig
from server.dmm import hd_cover_urls
from server.images import ImageDownloader, plan_images
from server.models import AggregatedMetadata, ContentType, MediaMetadata
from server.sources.http import HttpError
from server.storage import LocalStorage, RootGuard


def _metadata(**kwargs) -> MediaMetadata:
    base = {
        "number": "MIDV-123",
        "poster_url": "https://img.test/poster.jpg",
        "thumb_urls": ["https://img.test/thumb.jpg"],
        "fanart_urls": [f"https://img.test/sample{i}.jpg" for i in range(1, 9)],
    }
    base.update(kwargs)
    return MediaMetadata(**base)


# ---------------------------------------------------------------- 计划


def test_default_only_downloads_poster():
    """默认只下海报：背景图与剧照都要用户自己决定。"""
    tasks = plan_images(_metadata(), ImageDownloadConfig(), stem="MIDV-123")
    assert [t.kind for t in tasks] == ["poster"]


# ---------------------------------------------------------------- DMM 官方包装图


def _dmm_url(number: str) -> str:
    return hd_cover_urls(number)[0]


def test_hd_cover_is_the_first_poster_candidate():
    r"""候选顺序很关键：DMM 官方图放**最前面**，源站海报垫底。

    DMM 的 `pl.jpg` 是 2184x1468，源站（javdb）是 800x538；两者都在候选里，
    前一张失败就自动换下一张 —— 所以推不出 content id 或 DMM 没有这张图时，
    行为跟以前完全一样。
    """
    tasks = plan_images(
        _metadata(), ImageDownloadConfig(), stem="MIDV-123", content_type=ContentType.CENSORED
    )
    assert tasks[0].kind == "poster"
    assert tasks[0].candidates == [_dmm_url("MIDV-123"), "https://img.test/poster.jpg"]


@pytest.mark.parametrize("content_type", [ContentType.CENSORED, ContentType.UNCENSORED])
def test_hd_cover_applies_to_censored_and_uncensored(content_type):
    """无码往往是同番号的流出，DMM 那张（有码版）包装图仍然是同一部作品。"""
    tasks = plan_images(
        _metadata(), ImageDownloadConfig(), stem="X", content_type=content_type
    )
    assert any("awsimgsrc" in url for url in tasks[0].candidates)


@pytest.mark.parametrize(
    "content_type",
    [ContentType.JANIME, ContentType.FC2, ContentType.AMATEUR, ContentType.WESTERN, ContentType.UNKNOWN],
)
def test_hd_cover_not_offered_for_other_types(content_type):
    """里番没有番号、FC2/素人的 id 空间不是这一套 —— 不要白跑一趟。"""
    tasks = plan_images(_metadata(), ImageDownloadConfig(), stem="X", content_type=content_type)
    assert tasks[0].candidates == ["https://img.test/poster.jpg"]


async def test_hd_cover_wins_when_it_exists(tmp_path):
    http = _FakeHttp(
        {
            _dmm_url("MIDV-123"): _jpeg(2184, 1468),
            "https://img.test/poster.jpg": _jpeg(800, 538),
        }
    )
    ctx = _ctx(tmp_path, http)
    metadata_dir = tmp_path / "media" / "meta"

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-poster.jpg").read_bytes() == _jpeg(2184, 1468)
    assert http.calls == [_dmm_url("MIDV-123")]


async def test_hd_cover_404_falls_back_to_the_source_poster(tmp_path):
    """DMM 没有这张图（硬 404）时，老老实实用源站那张。"""
    http = _FakeHttp(
        {
            _dmm_url("MIDV-123"): HttpError("404"),
            "https://img.test/poster.jpg": _jpeg(800, 538),
        }
    )
    ctx = _ctx(tmp_path, http)
    metadata_dir = tmp_path / "media" / "meta"

    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-poster.jpg").read_bytes() == _jpeg(800, 538)
    assert http.calls == [_dmm_url("MIDV-123"), "https://img.test/poster.jpg"]
    assert report.failed == []


# ---------------------------------------------------------------- 图片 Referer


async def test_getchu_images_get_the_getchu_referer(tmp_path):
    r"""getchu 的图**必须**带 getchu 自己的 Referer。

    实测 `https://www.getchu.com/brandnew/<id>/c<id>sample1.jpg`：
    不带 Referer -> 403，带 bgm.tv 的 -> 403，带 getchu 的 -> 200。

    而以前整批图片只算**一个** Referer，取的是 `poster_url` 的来源 ——
    对这些文件来说海报来自 bangumi，于是 getchu 的剧照一律 403 被丢掉，
    最后只能拿竖版封面兜底（`-fanart.jpg` 和 `-poster.jpg` 是同一张）。

    这里故意把字段来源写成 javdb：**域名匹配要压过字段来源**。
    """
    url = "https://www.getchu.com/brandnew/1/c1sample1.jpg"
    http = _FakeHttp({url: b"x"})
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=0),
    )
    aggregated = AggregatedMetadata(
        number=None,
        content_type=ContentType.JANIME,
        field_sources={"poster_url": "bangumi", "fanart_urls": "javdb"},
    )

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(poster_url=None, fanart_urls=[url]),
        aggregated=aggregated,
        metadata_dir=tmp_path / "media" / "meta",
    )
    assert referer_of(http, url) == "https://www.getchu.com"


async def test_foreign_image_host_falls_back_to_the_field_source(tmp_path):
    """图片放在别的域名上（javdb 的 c0.jdbstatic.com）时，按"谁上报了这个字段"取主页。"""
    url = "https://c0.jdbstatic.com/samples/a_s_0.jpg"
    http = _FakeHttp({url: b"x"})
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=0),
    )
    aggregated = AggregatedMetadata(
        number=None,
        content_type=ContentType.CENSORED,
        field_sources={"poster_url": "javbus", "fanart_urls": "javdb,freejavbt"},
    )

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(number=None, poster_url=None, fanart_urls=[url]),
        aggregated=aggregated,
        metadata_dir=tmp_path / "media" / "meta",
    )
    # 列表字段的来源是逗号拼起来的 —— 以前直接拿整串去查源，查不到就等于没有 Referer
    assert referer_of(http, url) == "https://javdb.com"


def test_referer_of_list_fields_takes_the_first_known_source():
    """回归：`field_sources` 里存的是 "javdb,freejavbt" 这种逗号串。"""
    ctx = _ctx(Path("."), _FakeHttp({}))
    aggregated = AggregatedMetadata(field_sources={"fanart_urls": "javdb,freejavbt"})
    assert ImageDownloader(ctx)._referer_for(aggregated, "fanart_urls") == "https://javdb.com"
    assert ImageDownloader(ctx)._referer_for(aggregated, "不存在的字段") is None


def test_config_from_a_previous_version_still_loads():
    r"""去掉 ``thumb`` 是一次**单向下线**：库里存的配置 JSON 还带着这个键。

    必须照样能加载 —— 配置文件在数据库里，报错的话用户没法手工"修一下"，
    而 ``from_json`` 又会把异常吞掉退成默认值（等于把用户所有设置悄悄清空）。
    这里连值一起断言，不只看"没抛异常"。
    """
    from server.config import RuntimeConfig

    old = (
        '{"dry_run": false, "organize_enabled": true,'
        ' "images": {"poster": false, "thumb": true, "fanart": true,'
        ' "extrafanart_limit": 7, "fanart_min_width": 640}}'
    )
    config = RuntimeConfig.from_json(old)

    assert config.dry_run is False
    assert config.organize_enabled is True
    assert config.images.poster is False
    assert config.images.fanart is True
    assert config.images.extrafanart_limit == 7
    assert config.images.fanart_min_width == 640
    assert not hasattr(config.images, "thumb")


def test_thumb_urls_are_never_downloaded():
    """缩略图能力已经去掉，源上报的 thumb_urls 不再产生任何下载任务。

    它唯一的数据来源是站点那批 120x90 的缩略图，拉到 Emby 卡片里只会糊成一片；
    Emby 自己从封面/截图生成的效果更好。元数据模型里仍保留这个字段（源照常上报）。
    """
    assert _metadata().thumb_urls  # 前提：元数据里确实有
    tasks = plan_images(_metadata(), ImageDownloadConfig(), stem="MIDV-123")
    assert not [t for t in tasks if t.kind == "thumb"]


def test_extrafanart_respects_limit():
    config = ImageDownloadConfig(fanart=False, extrafanart=True, extrafanart_limit=3)
    tasks = plan_images(_metadata(), config, stem="MIDV-123")
    extra = [t for t in tasks if t.kind == "extrafanart"]
    assert len(extra) == 3
    assert extra[0].relative_path == Path("extrafanart") / "MIDV-123-01.jpg"


def test_extrafanart_skips_the_image_used_as_fanart():
    """背景图已经占了第一张，剧照就该从第二张开始，否则同一张图占两个槽位。"""
    config = ImageDownloadConfig(fanart=True, extrafanart=True, extrafanart_limit=2)
    tasks = plan_images(_metadata(), config, stem="MIDV-123")
    fanart = next(t for t in tasks if t.kind == "fanart")
    extra = [t for t in tasks if t.kind == "extrafanart"]
    assert len(extra) == 2
    assert fanart.candidates[0] == "https://img.test/sample1.jpg"
    assert all("sample1.jpg" not in t.candidates for t in extra)


def test_extrafanart_starts_at_first_when_fanart_disabled():
    config = ImageDownloadConfig(fanart=False, extrafanart=True, extrafanart_limit=1)
    tasks = plan_images(_metadata(), config, stem="MIDV-123")
    extra = next(t for t in tasks if t.kind == "extrafanart")
    assert extra.candidates == ["https://img.test/sample1.jpg"]


def test_all_off_produces_nothing():
    config = ImageDownloadConfig(poster=False, fanart=False, extrafanart=False)
    assert plan_images(_metadata(), config, stem="X") == []


def test_missing_source_images_are_skipped():
    config = ImageDownloadConfig(fanart=True, extrafanart=True)
    meta = MediaMetadata(number="X", poster_url=None, thumb_urls=[], fanart_urls=[])
    assert plan_images(meta, config, stem="X") == []


def test_candidate_urls_are_deduped():
    meta = _metadata(
        fanart_urls=["https://a/1.jpg", "https://a/1.jpg", "https://a/2.jpg"],
        poster_url="https://a/cover.jpg",
    )
    config = ImageDownloadConfig(poster=False, fanart=True)
    tasks = plan_images(meta, config, stem="X")
    # 去重后保序，末尾垫上封面当大图兜底
    assert tasks[0].candidates == ["https://a/1.jpg", "https://a/2.jpg", "https://a/cover.jpg"]


def test_fanart_puts_cover_last_not_first():
    """封面是兜底，不能抢占真正的背景图候选。"""
    meta = _metadata(fanart_urls=["https://a/1.jpg"], poster_url="https://a/cover.jpg")
    tasks = plan_images(meta, ImageDownloadConfig(poster=False, fanart=True), stem="X")
    assert tasks[0].candidates[0] == "https://a/1.jpg"
    assert tasks[0].candidates[-1] == "https://a/cover.jpg"


def test_fanart_candidates_have_no_duplicate_of_cover():
    """封面已经出现在剧照列表里时，不要再追加一次。"""
    meta = _metadata(fanart_urls=["https://a/cover.jpg"], poster_url="https://a/cover.jpg")
    tasks = plan_images(meta, ImageDownloadConfig(poster=False, fanart=True), stem="X")
    assert tasks[0].candidates == ["https://a/cover.jpg"]


# ---------------------------------------------------------------- 落盘


class _FakeHttp:
    """按 URL 返回内容或抛错，用来验证候选回退。"""

    def __init__(self, mapping: dict[str, bytes | Exception]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []
        self.referers: list[str | None] = []

    async def get_bytes(self, url: str, *, source: str, referer: str | None = None) -> bytes:
        self.calls.append(url)
        self.referers.append(referer)
        result = self.mapping.get(url)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise HttpError("404")
        return result


def referer_of(http: _FakeHttp, url: str) -> str | None:
    """取某个 URL 实际带上的 Referer。

    图片任务是并发跑的，``referers[0]`` 属于哪张图不确定 —— 必须按 URL 查。
    """
    assert url in http.calls, f"没有请求过 {url}"
    return http.referers[http.calls.index(url)]


class _Ctx:
    def __init__(self, config: RuntimeConfig, storage: LocalStorage, http: _FakeHttp) -> None:
        self.config = config
        self.storage = storage
        self.http = http


def _ctx(tmp_path: Path, http: _FakeHttp, **overrides) -> _Ctx:
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    config = RuntimeConfig(
        organize_enabled=True,
        dry_run=False,
        **overrides,
    )
    storage = LocalStorage(RootGuard([media]), allow_writes=True)
    return _Ctx(config, storage, http)


async def test_no_write_when_writes_disabled(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    storage = LocalStorage(RootGuard([media]), allow_writes=False)
    http = _FakeHttp({})
    ctx = _Ctx(RuntimeConfig(organize_enabled=False, dry_run=True), storage, http)

    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=media,
    )
    assert report.written == []
    assert report.skipped
    assert http.calls == []


async def test_downloads_enabled_types(tmp_path):
    http = _FakeHttp({"https://img.test/poster.jpg": b"p"})
    ctx = _ctx(tmp_path, http)
    metadata_dir = tmp_path / "media" / "meta"

    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    assert {p.name for p in report.written} == {"MIDV-123-poster.jpg"}
    assert (metadata_dir / "MIDV-123-poster.jpg").read_bytes() == b"p"
    assert "https://img.test/thumb.jpg" not in http.calls


async def test_falls_back_to_next_candidate(tmp_path):
    """第一张挂了要试下一张，不能整组失败。"""
    http = _FakeHttp(
        {
            "https://img.test/s1.jpg": HttpError("403"),
            "https://img.test/s2.jpg": b"s2",
        }
    )
    # 关掉宽度把关，让候选能走到下载这一步（那是另一组用例的事）
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=0),
    )
    metadata = _metadata(
        fanart_urls=["https://img.test/s1.jpg", "https://img.test/s2.jpg"], poster_url=None
    )

    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=metadata,
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=tmp_path / "media" / "meta",
    )
    assert http.calls == ["https://img.test/s1.jpg", "https://img.test/s2.jpg"]
    assert any(p.name == "MIDV-123-fanart.jpg" for p in report.written)


async def test_all_candidates_failing_is_reported_not_raised(tmp_path):
    http = _FakeHttp({"https://img.test/s1.jpg": HttpError("403")})
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=0),
    )
    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(fanart_urls=["https://img.test/s1.jpg"], poster_url=None),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=tmp_path / "media" / "meta",
    )
    assert any("fanart" in item and "失败" in item for item in report.failed)


async def test_existing_file_is_skipped_unless_overwrite(tmp_path):
    http = _FakeHttp({"https://img.test/poster.jpg": b"new"})
    ctx = _ctx(tmp_path, http)
    metadata_dir = tmp_path / "media" / "meta"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "MIDV-123-poster.jpg").write_bytes(b"old")

    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-poster.jpg").read_bytes() == b"old"
    assert "https://img.test/poster.jpg" not in http.calls
    assert any("已存在" in item for item in report.skipped)


async def test_run_can_force_overwrite_over_the_config(tmp_path):
    """人工重刮要能压过配置里的 overwrite=false。

    场景：删掉一条错配记录后重刮。NFO 换了新内容，但错的那张封面还在磁盘上 ——
    不覆盖的话 NFO 是新的、图还是旧的，看起来像修好了其实没有。
    这种"修了一半"比明显没修更难发现。
    """
    http = _FakeHttp({"https://img.test/poster.jpg": b"new"})
    ctx = _ctx(tmp_path, http, images=ImageDownloadConfig(overwrite=False))
    metadata_dir = tmp_path / "media" / "meta"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "MIDV-123-poster.jpg").write_bytes(b"old")

    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
        overwrite=True,
    )
    assert (metadata_dir / "MIDV-123-poster.jpg").read_bytes() == b"new"
    assert not report.skipped


async def test_run_overwrite_false_also_wins_over_the_config(tmp_path):
    """反过来也要成立：批量扫描不该因为配置开了 overwrite 就重下。

    参数是"这一次跑的行为"，不是"或"。
    """
    http = _FakeHttp({"https://img.test/poster.jpg": b"new"})
    ctx = _ctx(tmp_path, http, images=ImageDownloadConfig(overwrite=True))
    metadata_dir = tmp_path / "media" / "meta"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "MIDV-123-poster.jpg").write_bytes(b"old")

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
        overwrite=False,
    )
    assert (metadata_dir / "MIDV-123-poster.jpg").read_bytes() == b"old"
    assert http.calls == []


async def test_overwrite_replaces_existing(tmp_path):
    http = _FakeHttp({"https://img.test/poster.jpg": b"new"})
    ctx = _ctx(tmp_path, http, images=ImageDownloadConfig(overwrite=True))
    metadata_dir = tmp_path / "media" / "meta"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "MIDV-123-poster.jpg").write_bytes(b"old")

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-poster.jpg").read_bytes() == b"new"


async def test_image_download_uses_source_referer(tmp_path, monkeypatch):
    """javbus 的图片校验同源 Referer，下载时必须带上来源站主页。"""
    seen: list[str | None] = []

    class _Recorder(_FakeHttp):
        async def get_bytes(self, url: str, *, source: str, referer: str | None = None) -> bytes:
            seen.append(referer)
            return b"x"

    ctx = _ctx(tmp_path, _Recorder({}))
    aggregated = AggregatedMetadata(
        number="MIDV-123",
        content_type=ContentType.CENSORED,
        field_sources={"poster_url": "javbus"},
    )
    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=aggregated,
        metadata_dir=tmp_path / "media" / "meta",
    )
    assert seen and all(r == "https://www.javbus.com" for r in seen)

# ---------------------------------------------------------------- 背景图尺寸把关


def _jpeg(width: int, height: int) -> bytes:
    import struct

    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", height, width)
    sof0 += b"\x03" + b"\x01\x11\x00" * 3
    return b"\xff\xd8" + sof0 + b"\xff\xd9"


async def test_fanart_skips_small_candidates_and_falls_back_to_cover(tmp_path):
    """站点的"剧照"只是 120x90 缩略图时，背景图要改用封面大图。"""
    http = _FakeHttp(
        {
            "https://img.test/sample1.jpg": _jpeg(120, 90),
            "https://img.test/sample2.jpg": _jpeg(120, 90),
            "https://img.test/poster.jpg": _jpeg(800, 538),
        }
    )
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=400),
    )
    metadata_dir = tmp_path / "media" / "meta"

    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )

    assert [p.name for p in report.written] == ["MIDV-123-fanart.jpg"]
    assert (metadata_dir / "MIDV-123-fanart.jpg").read_bytes() == _jpeg(800, 538)


async def test_fanart_keeps_a_large_sample_when_available(tmp_path):
    """如果站点真给了大图，就用自己的，不用退而求其次拿封面。"""
    http = _FakeHttp(
        {
            "https://img.test/sample1.jpg": _jpeg(1280, 720),
            "https://img.test/poster.jpg": _jpeg(800, 538),
        }
    )
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=400),
    )
    metadata_dir = tmp_path / "media" / "meta"

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-fanart.jpg").read_bytes() == _jpeg(1280, 720)


async def test_zero_threshold_disables_the_guard(tmp_path):
    """把阈值设成 0 就是关掉把关，小图也照收。"""
    http = _FakeHttp({"https://img.test/sample1.jpg": _jpeg(120, 90)})
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(
            poster=False, fanart=True, fanart_min_width=0
        ),
    )
    metadata_dir = tmp_path / "media" / "meta"

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(fanart_urls=["https://img.test/sample1.jpg"], poster_url=None),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-fanart.jpg").exists()


async def test_cover_fallback_ignores_the_width_guard(tmp_path):
    """封面兜底不做宽度检查。

    实跑踩到：同一个目录 10 个文件，8 个有 `-fanart.jpg`、2 个没有 —— 差别只在
    封面宽度是否刚好过 400px（376x526 那个就被毙了）。"有背景图/没背景图"
    取决于封面恰好多大，而界面上完全看不出来。

    宽度检查的本意是挡掉站点的 120x90 缩略图，封面是我们自己挑中的代表图，
    不该被同一条规则毙掉。
    """
    http = _FakeHttp(
        {
            "https://img.test/sample1.jpg": _jpeg(120, 90),
            "https://img.test/poster.jpg": _jpeg(376, 526),
        }
    )
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=400),
    )
    metadata_dir = tmp_path / "media" / "meta"

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(fanart_urls=["https://img.test/sample1.jpg"]),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-fanart.jpg").read_bytes() == _jpeg(376, 526)


async def test_all_candidates_too_small_is_reported_as_skipped(tmp_path):
    http = _FakeHttp({"https://img.test/sample1.jpg": _jpeg(120, 90)})
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=400),
    )
    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(fanart_urls=["https://img.test/sample1.jpg"], poster_url=None),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=tmp_path / "media" / "meta",
    )
    assert report.written == []
    assert any("不合格" in item for item in report.skipped)


async def test_fanart_prefers_a_landscape_candidate(tmp_path):
    r"""背景图必须是横版，不能抓到第一张就用。

    实跑里 getchu 的剧照是**混着**的 —— 同一部作品：

        sample1  715 x 800   竖版
        sample2  800 x 770   横版
        sample3  800 x 450   横版

    只取第一张的话，背景图还是竖的，只是不再是封面的复制品而已。
    所以要往下试，试到横版为止；全都竖版的话才回退到封面。
    """
    http = _FakeHttp(
        {
            "https://img.test/s1.jpg": _jpeg(715, 800),   # 竖版，跳过
            "https://img.test/s2.jpg": _jpeg(800, 770),   # 横版，用它
            "https://img.test/poster.jpg": _jpeg(566, 800),
        }
    )
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=400),
    )
    metadata_dir = tmp_path / "media" / "meta"

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(
            poster_url="https://img.test/poster.jpg",
            fanart_urls=["https://img.test/s1.jpg", "https://img.test/s2.jpg"],
        ),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.JANIME),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-fanart.jpg").read_bytes() == _jpeg(800, 770)
    assert http.calls == ["https://img.test/s1.jpg", "https://img.test/s2.jpg"]


async def test_fanart_falls_back_to_the_cover_when_all_are_portrait(tmp_path):
    """横版一个都没有时，宁可回退到封面：有背景图总比没有强。"""
    http = _FakeHttp(
        {
            "https://img.test/s1.jpg": _jpeg(715, 800),
            "https://img.test/poster.jpg": _jpeg(566, 800),
        }
    )
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, fanart=True, fanart_min_width=400),
    )
    metadata_dir = tmp_path / "media" / "meta"

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(
            poster_url="https://img.test/poster.jpg", fanart_urls=["https://img.test/s1.jpg"]
        ),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.JANIME),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-fanart.jpg").read_bytes() == _jpeg(566, 800)
