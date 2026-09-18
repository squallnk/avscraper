import asyncio
import time

import pytest

from server.ratelimit import Breaker, Cooldown, RateLimiter, SerialQueue


async def test_rate_limiter_blocks_until_token_available():
    limiter = RateLimiter(rate=20, burst=1)
    started = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - started
    assert elapsed >= 0.04


async def test_rate_limiter_rejects_bad_config():
    with pytest.raises(ValueError):
        RateLimiter(rate=0)
    with pytest.raises(ValueError):
        RateLimiter(rate=1, burst=0)


def test_cooldown_trips_after_threshold():
    cd = Cooldown(base_seconds=10, threshold=2)
    cd.record_failure()
    assert not cd.tripped
    cd.record_failure()
    assert cd.tripped
    assert cd.remaining() > 0


def test_cooldown_backoff_grows():
    cd = Cooldown(base_seconds=10, threshold=1)
    cd.record_failure()
    first = cd.remaining()
    cd.record_failure()
    assert cd.remaining() > first


def test_cooldown_reset_on_success():
    cd = Cooldown(base_seconds=10, threshold=1)
    cd.record_failure()
    cd.record_success()
    assert not cd.tripped
    assert cd.failures == 0


async def test_serial_queue_never_overlaps():
    """串行队列的核心保证：同一时刻只有一个任务在跑。"""
    queue = SerialQueue(RateLimiter(rate=1000, burst=100))
    active = 0
    peak = 0

    async def job():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    await asyncio.gather(*(queue.run(job) for _ in range(8)))
    assert peak == 1


def test_breaker_tracks_per_key():
    breaker = Breaker(base_seconds=5, threshold=1)
    key = breaker.for_key("javbus")
    key.record_failure()
    assert key.tripped
    other = breaker.for_key("javdb")
    assert not other.tripped
    assert "javbus" in breaker.snapshot()
