"""Chaos-lite experiment 1/3 (S9 #97, §10.5): kill Redis mid-session,
assert §3.2.12's documented degraded modes — not "kill Redis and
observe" (the exact anti-pattern the blueprint's own self-review
flagged, §10.9 F-5), but a named behavior per role, each with a real
pass/fail criterion.

Real container kill (``docker kill`` via the wrapped docker-py
container, not a mocked exception or an unreachable-port simulation) —
the existing integration suite already proves the RedisError code path
against an unreachable client (``test_fail_open_limiter_falls_back_to_local_bucket_on_redis_outage``);
this suite's job is to prove the same guarantee against an instance
that was actually alive and then actually died, including recovery
after it comes back.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import redis.asyncio as redis_asyncio
from testcontainers.redis import RedisContainer
from tests.unit.fakes.auth import FakeClock, FakeIdGenerator

from aether.adapters.redis.denylist import RedisJtiDenylist
from aether.adapters.redis.rate_limiter import (
    FailOpenRateLimiter,
    LocalTokenBucketRateLimiter,
    RedisTokenBucketRateLimiter,
)

pytestmark = pytest.mark.chaos


def _client_for(container: RedisContainer) -> redis_asyncio.Redis:
    host = container.get_container_host_ip()
    port = container.get_exposed_port(6379)
    return redis_asyncio.from_url(  # type: ignore[no-untyped-call]
        f"redis://{host}:{port}/0", socket_connect_timeout=2, socket_timeout=2
    )


async def test_rate_limiting_survives_a_real_redis_kill_by_falling_back_to_local_buckets(
    killable_redis: RedisContainer,
) -> None:
    """§3.2.12: "rate limiting -> fall back to in-process local buckets
    with conservative limits (fail-open, bounded blast radius)"."""
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    client = _client_for(killable_redis)
    limiter = FailOpenRateLimiter(
        RedisTokenBucketRateLimiter(client, clock=clock),
        LocalTokenBucketRateLimiter(clock=clock),
    )

    # Normal operation: real Redis-backed limiting.
    before = await limiter.check("chaos:rl", limit=2, window_seconds=60)
    assert before.allowed is True

    killable_redis.get_wrapped_container().kill()

    # Degraded mode: still functions, still bounded — not "everything
    # 500s" and not "unlimited requests allowed through".
    during_1 = await limiter.check("chaos:rl", limit=2, window_seconds=60)
    during_2 = await limiter.check("chaos:rl", limit=2, window_seconds=60)
    during_3 = await limiter.check("chaos:rl", limit=2, window_seconds=60)
    # The Redis-backed bucket already consumed 1 of 2 tokens before the
    # kill; the *local* fallback bucket starts fresh (per-process state,
    # §3.2.12's own accepted "not perfect accuracy" trade-off) — 2 more
    # allowed, then bounded.
    assert [during_1.allowed, during_2.allowed, during_3.allowed] == [True, True, False]

    killable_redis.get_wrapped_container().start()

    # Recovery: real Redis-backed limiting resumes once it's reachable
    # again (a fresh bucket, since the killed container's in-memory
    # state didn't persist — an accepted consequence of "fast and
    # forgettable", not a bug). Docker reassigns a new host-side
    # ephemeral port on this restart (observed empirically, not assumed)
    # — a fresh client bound to the *current* port is required, reusing
    # the pre-kill client's stale port would just time out.
    recovered_client = await _wait_for_redis(killable_redis)
    recovered_limiter = FailOpenRateLimiter(
        RedisTokenBucketRateLimiter(recovered_client, clock=clock),
        LocalTokenBucketRateLimiter(clock=clock),
    )
    after = await recovered_limiter.check("chaos:rl:after-recovery", limit=2, window_seconds=60)
    assert after.allowed is True

    await client.aclose()
    await recovered_client.aclose()


async def test_revocation_check_fails_open_on_a_real_redis_kill_within_the_documented_window(
    killable_redis: RedisContainer,
) -> None:
    """§3.2.12: "revocation -> fail-open on JWT validity, <=15-min
    exposure, loud alert (ADR-3.6)" — a genuinely security-relevant
    degraded mode: a revoked token becomes usable again for the
    duration of the outage, which is the accepted, documented trade-off
    (a cache-tier outage must never become a total auth outage), not a
    surprise."""
    client = _client_for(killable_redis)
    denylist = RedisJtiDenylist(client)
    jti = FakeIdGenerator().new_id()

    await denylist.deny(jti, ttl_seconds=900)
    assert await denylist.is_denied(jti) is True

    killable_redis.get_wrapped_container().kill()

    # Fail OPEN, not fail closed: the whole point of ADR-3.6's design.
    assert await denylist.is_denied(jti) is False

    killable_redis.get_wrapped_container().start()
    recovered_client = await _wait_for_redis(killable_redis)

    await client.aclose()
    await recovered_client.aclose()


async def _wait_for_redis(container: RedisContainer, *, attempts: int = 30) -> redis_asyncio.Redis:
    """Rebuilds the client fresh each attempt rather than reusing one
    bound to a pre-restart port — see the port-reassignment note in the
    caller above."""
    for _ in range(attempts):
        client = _client_for(container)
        try:
            if await client.ping():
                return client
        except Exception:
            await client.aclose()
        await asyncio.sleep(0.5)
    raise TimeoutError("redis did not become reachable again after restart")
