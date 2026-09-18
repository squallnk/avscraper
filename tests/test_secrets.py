"""密钥字段的脱敏语义，以及 Cookie 是否真的被发出去。

这两件事出错的方式都很隐蔽：掩码被写回库会把 Cookie 变成字面量 "***"，
Cookie 没被挂上则表现为"站点明明登录了却一直报 blocked"。
"""

from __future__ import annotations

import httpx
import pytest

from server.config import SECRET_MASK, RuntimeConfig
from server.models import ContentType
from server.sources.base import FetchContext, SourceBlocked
from server.sources.getchu import GetchuSource
from server.sources.http import HttpClient

# ---------------------------------------------------------------- 脱敏


def test_redacted_hides_secrets():
    config = RuntimeConfig(
        webhook_token="super-secret",
        source_cookies={"getchu": "sessionid=abc"},
    )
    shown = config.redacted()
    assert shown["webhook_token"] == SECRET_MASK
    assert shown["source_cookies"] == {"getchu": SECRET_MASK}
    assert "super-secret" not in str(shown)
    assert "sessionid=abc" not in str(shown)


def test_redacted_shows_empty_when_unset():
    shown = RuntimeConfig().redacted()
    assert shown["webhook_token"] == ""


def test_patch_with_mask_keeps_original():
    """前端拿到的是掩码，原样传回来不能把真值覆盖成掩码。"""
    config = RuntimeConfig(webhook_token="real", source_cookies={"getchu": "real-cookie"})
    updated = config.with_patch(
        {"webhook_token": SECRET_MASK, "source_cookies": {"getchu": SECRET_MASK}}
    )
    assert updated.webhook_token == "real"
    assert updated.source_cookies["getchu"] == "real-cookie"


def test_patch_with_empty_string_clears():
    config = RuntimeConfig(webhook_token="real", source_cookies={"getchu": "real-cookie"})
    updated = config.with_patch({"webhook_token": "", "source_cookies": {"getchu": ""}})
    assert updated.webhook_token == ""
    assert "getchu" not in updated.source_cookies


def test_patch_sets_new_cookie_without_dropping_others():
    config = RuntimeConfig(source_cookies={"getchu": "a", "javdb": "b"})
    updated = config.with_patch({"source_cookies": {"getchu": "c"}})
    assert updated.source_cookies == {"getchu": "c", "javdb": "b"}


def test_patch_ignores_unknown_keys():
    config = RuntimeConfig()
    updated = config.with_patch({"not_a_field": 1})
    assert updated == config


# ---------------------------------------------------------------- Cookie 外发


async def test_source_cookie_is_sent_only_for_that_source():
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("cookie", "")))
        return httpx.Response(200, text="ok")

    client = HttpClient(transport=httpx.MockTransport(handler))
    await client.start()
    try:
        client.set_source_cookies({"getchu": "sessionid=abc"})
        await client.get("https://www.getchu.com/soft.phtml?id=1", source="getchu")
        await client.get("https://javdb.com/a", source="javdb")
    finally:
        await client.close()

    assert seen[0][1] == "sessionid=abc"
    assert seen[1][1] == ""


async def test_empty_cookie_values_are_dropped():
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie", ""))
        return httpx.Response(200, text="ok")

    client = HttpClient(transport=httpx.MockTransport(handler))
    await client.start()
    try:
        client.set_source_cookies({"getchu": ""})
        await client.get("https://x.test/", source="getchu")
    finally:
        await client.close()
    assert seen == [""]


async def test_explicit_header_wins_over_configured_cookie():
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie", ""))
        return httpx.Response(200, text="ok")

    client = HttpClient(transport=httpx.MockTransport(handler))
    await client.start()
    try:
        client.set_source_cookies({"getchu": "from-config"})
        await client.get("https://x.test/", source="getchu", headers={"Cookie": "explicit"})
    finally:
        await client.close()
    assert seen == ["explicit"]


async def test_get_bytes_sends_referer_and_cookie():
    """部分站点的图片校验同源 Referer（javbus 的 pics.dmm.co.jp 就是），必须带上。"""
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (request.headers.get("referer", ""), request.headers.get("cookie", ""))
        )
        return httpx.Response(200, content=b"jpeg")

    client = HttpClient(transport=httpx.MockTransport(handler))
    await client.start()
    try:
        client.set_source_cookies({"javbus": "sid=1"})
        data = await client.get_bytes(
            "https://pics.dmm.co.jp/x.jpg", source="javbus", referer="https://www.javbus.com"
        )
    finally:
        await client.close()

    assert data == b"jpeg"
    assert seen == [("https://www.javbus.com", "sid=1")]


# ---------------------------------------------------------------- getchu 行为


class _StubClient:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self._status = status
        self._body = body

    async def get(self, url: str, *, source: str, headers: dict[str, str] | None = None):
        from server.sources.http import HttpResult

        return HttpResult(
            status=self._status,
            text=self._body.decode("euc-jp", errors="replace"),
            content=self._body,
            elapsed_ms=1,
            url=url,
        )


async def test_getchu_reports_blocked_on_403():
    """403 必须报 blocked，不能静默返回 None —— 否则排障时会被误判成"这片没有"。"""
    source = GetchuSource()
    with pytest.raises(SourceBlocked) as excinfo:
        await source.fetch(
            _StubClient(403), FetchContext(query="オナニー", content_type=ContentType.JANIME)
        )
    assert "403" in str(excinfo.value)


async def test_getchu_reports_blocked_on_attestation_page():
    html = "<html><head><title>年齢認証ページ</title></head></html>".encode("euc-jp")
    source = GetchuSource()
    with pytest.raises(SourceBlocked):
        await source.fetch(_StubClient(200, html), FetchContext(query="x", content_type=ContentType.JANIME))


async def test_getchu_returns_none_when_search_has_no_results():
    html = "<html><body>該当する商品はありません</body></html>".encode("euc-jp")
    source = GetchuSource()
    result = await source.fetch(
        _StubClient(200, html), FetchContext(query="x", content_type=ContentType.JANIME)
    )
    assert result is None


def test_getchu_no_longer_needs_a_cookie():
    """年龄墙已经用 gc=gc 参数解决，不该再要求用户填 Cookie。"""
    assert GetchuSource.descriptor.needs_cookie is False
