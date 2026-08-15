from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aether.adapters.redis.rate_limiter import LocalTokenBucketRateLimiter
from tests.unit.fakes.auth import FakeClock

pytestmark = pytest.mark.unit


async def test_allows_up_to_the_limit_then_denies() -> None:
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    limiter = LocalTokenBucketRateLimiter(clock=clock)

    results = [await limiter.check("k", limit=3, window_seconds=60) for _ in range(4)]

    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[-1].remaining == 0


async def test_refills_continuously_over_time() -> None:
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    limiter = LocalTokenBucketRateLimiter(clock=clock)

    for _ in range(5):
        await limiter.check("k", limit=5, window_seconds=60)  # exhaust the bucket
    denied = await limiter.check("k", limit=5, window_seconds=60)
    assert denied.allowed is False

    clock.advance(timedelta(seconds=60))  # full window elapsed -> fully refilled
    allowed = await limiter.check("k", limit=5, window_seconds=60)
    assert allowed.allowed is True


async def test_different_keys_have_independent_buckets() -> None:
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    limiter = LocalTokenBucketRateLimiter(clock=clock)

    for _ in range(2):
        await limiter.check("a", limit=2, window_seconds=60)
    a_denied = await limiter.check("a", limit=2, window_seconds=60)
    b_allowed = await limiter.check("b", limit=2, window_seconds=60)

    assert a_denied.allowed is False
    assert b_allowed.allowed is True
