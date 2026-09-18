import pytest

from server.storage import (
    Cd2PathError,
    Cd2PathMapper,
    LocalStorage,
    OutsideAllowedRoots,
    RootGuard,
    WritesDisabled,
)


@pytest.fixture()
def sandbox(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "a.mp4").write_bytes(b"x")
    (root / "sub").mkdir()
    (root / "sub" / "b.mkv").write_bytes(b"yy")
    (root / "note.txt").write_text("hi")
    return root


def test_root_guard_allows_inside(sandbox):
    guard = RootGuard([sandbox])
    assert guard.check(sandbox / "a.mp4") == (sandbox / "a.mp4").resolve()


def test_root_guard_rejects_outside(sandbox, tmp_path):
    guard = RootGuard([sandbox])
    with pytest.raises(OutsideAllowedRoots):
        guard.check(tmp_path / "other" / "x.mp4")


def test_root_guard_rejects_symlink_escape(sandbox, tmp_path):
    """软链接指向根目录之外时必须被拒绝。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mp4").write_bytes(b"z")
    link = sandbox / "link.mp4"
    try:
        link.symlink_to(outside / "secret.mp4")
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")
    guard = RootGuard([sandbox])
    with pytest.raises(OutsideAllowedRoots):
        guard.check(link)


def test_no_roots_means_no_access(sandbox):
    guard = RootGuard([])
    with pytest.raises(OutsideAllowedRoots):
        guard.check(sandbox / "a.mp4")


def test_storage_writes_disabled_by_default(sandbox):
    storage = LocalStorage(RootGuard([sandbox]))
    assert not storage.writes_enabled
    with pytest.raises(WritesDisabled):
        storage.write_text(sandbox / "new.nfo", "<movie/>")


def test_storage_writes_when_enabled(sandbox):
    storage = LocalStorage(RootGuard([sandbox]), allow_writes=True)
    target = storage.write_text(sandbox / "new.nfo", "<movie/>")
    assert target.read_text() == "<movie/>"


def test_iter_videos_skips_non_video_and_hidden(sandbox):
    (sandbox / ".hidden").mkdir()
    (sandbox / ".hidden" / "c.mp4").write_bytes(b"x")
    storage = LocalStorage(RootGuard([sandbox]))
    found = {p.name for p in storage.iter_videos(sandbox)}
    assert found == {"a.mp4", "b.mkv"}


def test_move_requires_writes(sandbox):
    storage = LocalStorage(RootGuard([sandbox]))
    with pytest.raises(WritesDisabled):
        storage.move(sandbox / "a.mp4", sandbox / "b" / "a.mp4")


def test_cd2_mapper_longest_prefix_wins():
    mapper = Cd2PathMapper([
        ("/115open", "/mnt/cd2"),
        ("/115open/云下载", "/mnt/cd2-extra"),
    ])
    assert mapper.to_local("/115open/云下载/a.mp4") == __import__("pathlib").Path("/mnt/cd2-extra/a.mp4")
    assert mapper.to_local("/115open/other/a.mp4") == __import__("pathlib").Path("/mnt/cd2/other/a.mp4")


def test_cd2_mapper_reverse():
    mapper = Cd2PathMapper([("/115open", "/mnt/cd2")])
    assert mapper.to_virtual("/mnt/cd2/云下载/a.mp4").endswith("/115open/云下载/a.mp4")


def test_cd2_mapper_raises_when_unmapped():
    mapper = Cd2PathMapper([("/115open", "/mnt/cd2")])
    with pytest.raises(Cd2PathError):
        mapper.to_local("/other/a.mp4")
    with pytest.raises(Cd2PathError):
        mapper.to_virtual("/mnt/nowhere/a.mp4")
