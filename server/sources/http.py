"""带限速、重试与熔断的 HTTP 客户端。

限速按 **host** 分桶：站点之间互不影响，同一站点不并发。
失败按 **source** 熔断：一个源连续失败到达阈值后，冷却期内不再发起请求，
既保护对端也避免把本机的出口 IP 打脏。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from server.ratelimit import Breaker, HostLimiter

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


@dataclass
class HttpResult:
    status: int
    text: str
    content: bytes
    elapsed_ms: int
    url: str

    def decode(self, encoding: str) -> str:
        """按指定编码解码。getchu 用 EUC-JP，交给 httpx 猜不如显式指定。"""
        return self.content.decode(encoding, errors="replace")


class HttpError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class HttpBlocked(HttpError):
    pass


class HttpClient:
    def __init__(
        self,
        *,
        proxy: str = "",
        timeout: float = 20.0,
        rate: float = 1.0,
        burst: int = 3,
        max_retries: int = 2,
        breaker: Breaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._transport = transport
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        self._limiters = HostLimiter(rate=rate, burst=burst)
        self._breaker = breaker or Breaker()
        self._proxy = proxy
        self._client: httpx.AsyncClient | None = None
        self._inflight: dict[str, asyncio.Lock] = {}
        # 站点特例：某些站需要登录态/年龄确认 Cookie，按源 id 注入。
        self._source_cookies: dict[str, str] = {}
        self._user_agent = DEFAULT_UA

    async def __aenter__(self) -> HttpClient:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is not None:
            return
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self._timeout),
            "follow_redirects": True,
            "headers": {"User-Agent": self._user_agent, "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8"},
        }
        if self._proxy and self._transport is None:
            kwargs["proxy"] = self._proxy
        if self._transport is not None:
            # 测试注入的传输层优先；有它时不再走代理，避免两条路径互相干扰
            kwargs["transport"] = self._transport
        self._client = httpx.AsyncClient(**kwargs)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def set_user_agent(self, user_agent: str) -> None:
        """设置 UA。

        按请求带，而不是改客户端默认头 —— 改默认头要重建客户端，
        而重建是异步的，会出现"配置改了但下一个请求还用旧 UA"的竞态窗口。
        """
        self._user_agent = (user_agent or "").strip() or DEFAULT_UA

    def set_source_cookies(self, cookies: dict[str, str]) -> None:
        self._source_cookies = {k: v for k, v in cookies.items() if v}

    def configure(self, *, rate: float, burst: int, timeout: float, max_retries: int, proxy: str) -> None:
        self._limiters.configure(rate, burst)
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        if proxy != self._proxy:
            self._proxy = proxy
            # 代理变了就重建客户端
            asyncio.get_event_loop().create_task(self._rebuild())

    async def _rebuild(self) -> None:
        await self.close()
        await self.start()

    def _lock_for(self, host: str) -> asyncio.Lock:
        lock = self._inflight.get(host)
        if lock is None:
            lock = asyncio.Lock()
            self._inflight[host] = lock
        return lock

    async def get(self, url: str, *, source: str, headers: dict[str, str] | None = None) -> HttpResult:
        if self._client is None:
            await self.start()
        assert self._client is not None

        host = httpx.URL(url).host or ""
        cooldown = self._breaker.for_key(source)
        if cooldown.tripped:
            raise HttpError(
                f"源 {source} 处于冷却中，剩余 {cooldown.remaining():.0f}s", status=None
            )

        limiter = self._limiters.for_host(host)
        lock = self._lock_for(host)
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            await limiter.acquire()
            started = time.monotonic()
            try:
                request_headers = dict(headers or {})
                request_headers["User-Agent"] = self._user_agent
                cookie = self._source_cookies.get(source)
                if cookie and "Cookie" not in request_headers:
                    request_headers["Cookie"] = cookie
                async with lock:
                    response = await self._client.get(url, headers=request_headers)
                elapsed = int((time.monotonic() - started) * 1000)
                if response.status_code in (401, 403, 429):
                    cooldown.record_failure()
                    raise HttpBlocked(
                        f"HTTP {response.status_code}（疑似反爬或需要登录）",
                        status=response.status_code,
                    )
                if response.status_code == 404:
                    cooldown.record_success()
                    return HttpResult(404, "", b"", elapsed, str(response.url))
                response.raise_for_status()
                cooldown.record_success()
                return HttpResult(
                    response.status_code, response.text, response.content, elapsed, str(response.url)
                )
            except HttpBlocked:
                raise
            except httpx.HTTPStatusError as exc:
                last_error = exc
                cooldown.record_failure()
            except httpx.HTTPError as exc:
                last_error = exc
                cooldown.record_failure()

            if attempt < self._max_retries:
                await asyncio.sleep(min(2.0 * (attempt + 1) + random.random(), 10.0))

        raise HttpError(f"请求失败: {last_error}", status=None)

    async def get_bytes(self, url: str, *, source: str, referer: str | None = None) -> bytes:
        if self._client is None:
            await self.start()
        assert self._client is not None
        host = httpx.URL(url).host or ""
        await self._limiters.for_host(host).acquire()
        headers: dict[str, str] = {"User-Agent": self._user_agent}
        if referer:
            headers["Referer"] = referer
        cookie = self._source_cookies.get(source)
        if cookie:
            headers["Cookie"] = cookie
        try:
            response = await self._client.get(url, headers=headers)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as exc:
            raise HttpError(f"下载失败: {exc}") from exc

    def status(self) -> dict[str, Any]:
        return {"cooldowns": self._breaker.snapshot()}
