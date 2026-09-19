"""图片下载的选择逻辑与落盘行为。

这一块最容易出的两种错：
1. 关掉的类型还是被下载了（用户明确说过剧照要能选）；
2. 某一张候选图挂了就整组失败，而源明明给了几十张候选。
"""

from __future__ import annotations

from pathlib import Path

from server.config import ImageDownloadConfig, RuntimeConfig
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


def test_default_only_downloads_poster_and_thumb():
    """剧照默认关闭 —— 体积大、张数多，用户要能自己决定。"""
    tasks = plan_images(_metadata(), ImageDownloadConfig(), stem="MIDV-123")
    assert [t.kind for t in tasks] == ["poster", "thumb"]


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
    config = ImageDownloadConfig(poster=False, thumb=False, fanart=False, extrafanart=False)
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
    config = ImageDownloadConfig(poster=False, thumb=False, fanart=True)
    tasks = plan_images(meta, config, stem="X")
    # 去重后保序，末尾垫上封面当大图兜底
    assert tasks[0].candidates == ["https://a/1.jpg", "https://a/2.jpg", "https://a/cover.jpg"]


def test_fanart_puts_cover_last_not_first():
    """封面是兜底，不能抢占真正的背景图候选。"""
    meta = _metadata(fanart_urls=["https://a/1.jpg"], poster_url="https://a/cover.jpg")
    tasks = plan_images(meta, ImageDownloadConfig(poster=False, thumb=False, fanart=True), stem="X")
    assert tasks[0].candidates[0] == "https://a/1.jpg"
    assert tasks[0].candidates[-1] == "https://a/cover.jpg"


def test_fanart_candidates_have_no_duplicate_of_cover():
    """封面已经出现在剧照列表里时，不要再追加一次。"""
    meta = _metadata(fanart_urls=["https://a/cover.jpg"], poster_url="https://a/cover.jpg")
    tasks = plan_images(meta, ImageDownloadConfig(poster=False, thumb=False, fanart=True), stem="X")
    assert tasks[0].candidates == ["https://a/cover.jpg"]


# ---------------------------------------------------------------- 落盘


class _FakeHttp:
    """按 URL 返回内容或抛错，用来验证候选回退。"""

    def __init__(self, mapping: dict[str, bytes | Exception]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def get_bytes(self, url: str, *, source: str, referer: str | None = None) -> bytes:
        self.calls.append(url)
        result = self.mapping.get(url)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise HttpError("404")
        return result


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
    http = _FakeHttp(
        {
            "https://img.test/poster.jpg": b"p",
            "https://img.test/thumb.jpg": b"t",
        }
    )
    ctx = _ctx(tmp_path, http)
    metadata_dir = tmp_path / "media" / "meta"

    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    written = {p.name for p in report.written}
    assert written == {"MIDV-123-poster.jpg", "MIDV-123-thumb.jpg"}
    assert (metadata_dir / "MIDV-123-poster.jpg").read_bytes() == b"p"


async def test_falls_back_to_next_candidate(tmp_path):
    """第一张挂了要试下一张，不能整组失败。"""
    http = _FakeHttp(
        {
            "https://img.test/thumb.jpg": HttpError("403"),
            "https://img.test/thumb2.jpg": b"t2",
        }
    )
    ctx = _ctx(tmp_path, http)
    metadata = _metadata(thumb_urls=["https://img.test/thumb.jpg", "https://img.test/thumb2.jpg"])

    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=metadata,
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=tmp_path / "media" / "meta",
    )
    assert http.calls == ["https://img.test/poster.jpg", "https://img.test/thumb.jpg", "https://img.test/thumb2.jpg"]
    assert any(p.name == "MIDV-123-thumb.jpg" for p in report.written)


async def test_all_candidates_failing_is_reported_not_raised(tmp_path):
    http = _FakeHttp({"https://img.test/thumb.jpg": HttpError("403")})
    ctx = _ctx(tmp_path, http)
    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=tmp_path / "media" / "meta",
    )
    assert any("thumb" in item and "失败" in item for item in report.failed)


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
    ctx = _ctx(tmp_path, http, images=ImageDownloadConfig(overwrite=False, thumb=False))
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
    ctx = _ctx(tmp_path, http, images=ImageDownloadConfig(overwrite=True, thumb=False))
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
    ctx = _ctx(tmp_path, http, images=ImageDownloadConfig(overwrite=True, thumb=False))
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
        images=ImageDownloadConfig(poster=False, thumb=False, fanart=True, fanart_min_width=400),
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
        images=ImageDownloadConfig(poster=False, thumb=False, fanart=True, fanart_min_width=400),
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
            poster=False, thumb=False, fanart=True, fanart_min_width=0
        ),
    )
    metadata_dir = tmp_path / "media" / "meta"

    await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(fanart_urls=["https://img.test/sample1.jpg"], poster_url=None),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=metadata_dir,
    )
    assert (metadata_dir / "MIDV-123-fanart.jpg").exists()


async def test_all_candidates_too_small_is_reported_as_skipped(tmp_path):
    http = _FakeHttp({"https://img.test/sample1.jpg": _jpeg(120, 90)})
    ctx = _ctx(
        tmp_path,
        http,
        images=ImageDownloadConfig(poster=False, thumb=False, fanart=True, fanart_min_width=400),
    )
    report = await ImageDownloader(ctx).run(  # type: ignore[arg-type]
        metadata=_metadata(fanart_urls=["https://img.test/sample1.jpg"], poster_url=None),
        aggregated=AggregatedMetadata(number="MIDV-123", content_type=ContentType.CENSORED),
        metadata_dir=tmp_path / "media" / "meta",
    )
    assert report.written == []
    assert any("小于" in item for item in report.skipped)
