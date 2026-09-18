"""防风控：令牌桶、串行队列、冷却退避、熔断。

设计取舍：
- 文件写操作**全部**经过 `SerialQueue`，同一时刻只有一个在跑，且受令牌桶限速。
  115 侧对短时间大量目录操作最敏感，串行是这里唯一靠谱的约束。
- HTTP 抓取按 **host** 分桶，站点之间互不阻塞，同一站点不并发。
- 失败进入 `Cooldown`，指数退避；连续失败超过阈值则熔断，熔断期内直接拒绝，
  不再把请求打到对端。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


class RateLimiter:
    """令牌桶。`rate` 为每秒补充的令牌数，`burst` 为桶容量。"""

    def __init__(self, rate: float, burst: int = 1) -> None:
        if rate <= 0:
            raise ValueError("rate 必须大于 0")
        if burst < 1:
            raise ValueError("burst 必须至少为 1")
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed <= 0:
            return
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._updated = now

    async def acquire(self, tokens: float = 1.0) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self._rate
            await asyncio.sleep(wait)

    def configure(self, rate: float, burst: int) -> None:
        if rate <= 0 or burst < 1:
            return
        self._rate = rate
        self._burst = burst
        self._tokens = min(self._tokens, float(burst))


class Cooldown:
    """指数退避 + 熔断。

    调用方在操作成功时 `reset()`，失败时 `record_failure()`；
    进入冷却后 `wait_if_cooling()` 会阻塞到冷却结束。
    """

    def __init__(self, base_seconds: float = 60.0, threshold: int = 3) -> None:
        self._base = max(1.0, base_seconds)
        self._threshold = max(1, threshold)
        self._failures = 0
        self._until = 0.0

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def tripped(self) -> bool:
        return time.monotonic() < self._until

    def remaining(self) -> float:
        return max(0.0, self._until - time.monotonic())

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            over = self._failures - self._threshold
            penalty = self._base * (2 ** min(over, 6))
            self._until = time.monotonic() + penalty

    def record_success(self) -> None:
        self._failures = 0
        self._until = 0.0

    async def wait_if_cooling(self) -> None:
        while (remaining := self.remaining()) > 0:
            await asyncio.sleep(min(remaining, 5.0))


class SerialQueue:
    """串行执行器：保证同一时刻只有一个文件操作在跑，并对其限速。"""

    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter
        self._lock = asyncio.Lock()
        self._depth = 0

    @property
    def depth(self) -> int:
        return self._depth

    async def run(self, fn: Callable[[], Awaitable[T]]) -> T:
        self._depth += 1
        try:
            async with self._lock:
                await self._limiter.acquire()
                return await fn()
        finally:
            self._depth -= 1


@dataclass
class HostLimiter:
    """按 host 分摊的限速器集合。"""

    rate: float = 1.0
    burst: int = 3
    _limiters: dict[str, RateLimiter] = field(default_factory=dict)

    def for_host(self, host: str) -> RateLimiter:
        limiter = self._limiters.get(host)
        if limiter is None:
            limiter = RateLimiter(self.rate, self.burst)
            self._limiters[host] = limiter
        return limiter

    def configure(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.burst = burst
        for limiter in self._limiters.values():
            limiter.configure(rate, burst)


class Breaker:
    """按 key（源 id / host）分组的熔断器。"""

    def __init__(self, base_seconds: float = 60.0, threshold: int = 3) -> None:
        self._base = base_seconds
        self._threshold = threshold
        self._cooldowns: dict[str, Cooldown] = {}

    def for_key(self, key: str) -> Cooldown:
        cd = self._cooldowns.get(key)
        if cd is None:
            cd = Cooldown(self._base, self._threshold)
            self._cooldowns[key] = cd
        return cd

    def configure(self, base_seconds: float, threshold: int) -> None:
        self._base = base_seconds
        self._threshold = threshold

    def snapshot(self) -> dict[str, Any]:
        return {
            key: {
                "failures": cd.failures,
                "cooling": cd.tripped,
                "remaining": round(cd.remaining(), 1),
            }
            for key, cd in self._cooldowns.items()
        }
