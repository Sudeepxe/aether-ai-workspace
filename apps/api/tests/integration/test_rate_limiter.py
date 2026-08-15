"""Rate-limit tests against a real Redis (issue #23's exact acceptance
criterion): the Lua token-bucket script's normal-operation behavior, and
the simulated-Redis-outage degraded mode — a real connection failure,
not a mocked exception, driving FailOpenRateLimiter's fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import redis.asyncio as redis_asyncio

from aether.adapters.redis.rate_limiter import (
    FailOpenRateLimiter,
    LocalTokenBucketRateLimiter,
    RedisTokenBucketRateLimiter,
)
from tests.unit.fakes.auth import FakeClock

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def test_redis_token_bucket_allows_up_to_limit_then_denies(
    redis_client: redis_asyncio.Redis,
) -> None:
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    limiter = RedisTokenBucketRateLimiter(redis_client, clock=clock)

    results = [await limiter.check("test:key", limit=3, window_seconds=60) for _ in range(4)]

    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[0].remaining == 2
    assert results[-1].remaining == 0
    assert results[-1].reset_seconds > 0


async def test_redis_token_bucket_is_atomic_under_concurrent_requests(
    redis_client: redis_asyncio.Redis,
) -> None:
    """The whole point of doing this in one Lua script instead of a
    Python GET-then-SET: concurrent requests against the same bucket
    can't race past the limit. Fire more concurrent checks than the
    limit allows and confirm exactly `limit` succeed — a race would
    over-admit."""
    import asyncio

    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    limiter = RedisTokenBucketRateLimiter(redis_client, clock=clock)

    results = await asyncio.gather(
        *[limiter.check("test:concurrent", limit=10, window_seconds=60) for _ in range(30)]
    )

    assert sum(1 for r in results if r.allowed) == 10


async def test_fail_open_limiter_uses_redis_when_reachable(
    redis_client: redis_asyncio.Redis,
) -> None:
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    limiter = FailOpenRateLimiter(
        RedisTokenBucketRateLimiter(redis_client, clock=clock),
        LocalTokenBucketRateLimiter(clock=clock),
    )

    results = [await limiter.check("test:normal", limit=2, window_seconds=60) for _ in range(3)]

    assert [r.allowed for r in results] == [True, True, False]


async def test_fail_open_limiter_falls_back_to_local_bucket_on_redis_outage() -> None:
    """A real connection failure (unreachable Redis), not a mocked
    exception — proves the actual RedisError path, not just that the
    Python code has a try/except shaped correctly."""
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    unreachable_client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]
        "redis://localhost:1",  # nothing listens on port 1
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    fallback = LocalTokenBucketRateLimiter(clock=clock)
    limiter = FailOpenRateLimiter(
        RedisTokenBucketRateLimiter(unreachable_client, clock=clock), fallback
    )

    results = [await limiter.check("test:outage", limit=2, window_seconds=60) for _ in range(3)]

    # Degraded mode is still bounded — a Redis outage doesn't mean
    # unlimited requests, it means conservative local limiting.
    assert [r.allowed for r in results] == [True, True, False]
    await unreachable_client.aclose()
