r"""「我拉的是不是最新版」—— WebUI 侧边栏那个「检查更新」。

以前判断容器里跑的是哪个版本，只能去翻 `/api/health`，或者靠行为反推
（"代码改了却没变化"是不是镜像还是旧的）。排查时白绕了好几圈。

比较对象是 **GHCR 上 latest 镜像的 revision 标签**，不是 GitHub 上 main 的最新提交：
提交推上去之后还要跑构建，构建期间 main 已经更新、镜像还是旧的 —— 那会告诉用户
"有新版本"，可他拉下来还是老的。镜像上的标签才是他真正能拉到的东西。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.api import routes

CURRENT = "05594e7d7ab0"
LATEST = "05594e7d7ab07968dde2eee7e70ee5c737aa9de8"


def _request(proxy: str = "") -> SimpleNamespace:
    ctx = SimpleNamespace(config=SimpleNamespace(proxy=proxy))
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ctx=ctx)))


@pytest.fixture
def patch_version(monkeypatch):
    """把版本信息和"问 GHCR"都换掉，用例只测判断逻辑。"""
    asked: list[str] = []

    def _apply(build_sha: str, latest: str | None) -> list[str]:
        monkeypatch.setattr(
            routes,
            "build_info",
            lambda: {
                "version": "0.1.0",
                "build_sha": build_sha,
                "build_time": "2026-09-19T18:20:00Z",
            },
        )

        async def fetch(proxy: str) -> str:
            asked.append(proxy)
            if latest is None:
                raise RuntimeError("连不上 ghcr.io")
            return latest

        monkeypatch.setattr(routes, "_latest_published_sha", fetch)
        return asked

    return _apply


async def test_matching_build_is_reported_as_the_latest(patch_version):
    patch_version(CURRENT, LATEST)
    info = await routes.check_version(_request())
    assert info["up_to_date"] is True
    assert info["latest_sha"] == LATEST
    assert info["error"] is None
    assert info["build_sha"] == CURRENT  # 界面要同时显示"我现在是哪个"


async def test_an_older_build_is_reported_as_outdated(patch_version):
    patch_version("6c4e4f52870c", LATEST)
    info = await routes.check_version(_request())
    assert info["up_to_date"] is False


async def test_local_source_run_has_nothing_to_compare(patch_version):
    r"""本机源码跑的 build_sha 是 "dev"，没有可比的镜像 —— 如实说，不猜。"""
    asked = patch_version("dev", LATEST)
    info = await routes.check_version(_request())
    assert info["up_to_date"] is None
    assert info["latest_sha"] is None
    assert "本地源码" in info["error"]
    assert asked == []  # 这种情况一次网络都不该发


async def test_unreachable_registry_reports_the_error_instead_of_guessing(patch_version):
    """问不到就说问不到 —— 编一个"已是最新"比不显示更糟。"""
    patch_version(CURRENT, None)
    info = await routes.check_version(_request())
    assert info["up_to_date"] is None
    assert info["latest_sha"] is None
    assert "ghcr.io" in info["error"]


async def test_the_configured_proxy_is_used(patch_version):
    """容器要出网可能得走代理，出口路径得跟源抓取保持一致。"""
    asked = patch_version(CURRENT, LATEST)
    await routes.check_version(_request(proxy="http://127.0.0.1:10808"))
    assert asked == ["http://127.0.0.1:10808"]
