"""Redis-backed token-bucket rate limiter (§3.6.3), atomic via a Lua
script — the check-refill-decrement sequence runs as one Redis operation,
so concurrent requests against the same bucket can't race past the
limit (a plain GET-then-SET from Python would).
"""

from __future__ import annotations

import redis.asyncio as redis_asyncio
from redis.exceptions import RedisError

from aether.logging import get_logger
from aether.ports.rate_limit import RateLimitResult
from aether.ports.security import ClockPort

log = get_logger(__name__)

# KEYS[1] = bucket key
# ARGV[1] = capacity (the limit)
# ARGV[2] = refill_rate (tokens/second = limit / window_seconds)
# ARGV[3] = now (seconds since epoch, float)
# Continuous refill (not "reset every window_seconds") avoids the
# thundering-herd-at-the-boundary problem fixed windows have.
_TOKEN_BUCKET_SCRIPT = """
local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then
    tokens = capacity
    ts = now
end

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill_rate) * 2)

local reset_seconds = 0
if tokens < capacity then
    reset_seconds = math.ceil((capacity - tokens) / refill_rate)
end

return {allowed, tostring(tokens), reset_seconds}
"""


class RedisTokenBucketRateLimiter:
    def __init__(self, client: redis_asyncio.Redis, *, clock: ClockPort) -> None:
        self._client = client
        self._clock = clock
        self._script = client.register_script(_TOKEN_BUCKET_SCRIPT)

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        refill_rate = limit / window_seconds
        now = self._clock.now().timestamp()
        allowed, remaining_raw, reset_seconds = await self._script(
            keys=[key], args=[limit, refill_rate, now]
        )
        return RateLimitResult(
            allowed=bool(int(allowed)),
            limit=limit,
            remaining=int(float(remaining_raw)),
            reset_seconds=int(reset_seconds),
        )


class LocalTokenBucketRateLimiter:
    """In-process fallback for the Redis-outage degraded mode (§3.2.12:
    "fall back to in-process local buckets with conservative limits").
    Per-process state, not shared across replicas — bounded blast
    radius (worst case is N replicas' worth of local capacity, not
    unlimited), not perfect accuracy, which is the explicit trade-off
    the blueprint accepts for this path."""

    def __init__(self, *, clock: ClockPort) -> None:
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        refill_rate = limit / window_seconds
        now = self._clock.now().timestamp()
        tokens, ts = self._buckets.get(key, (float(limit), now))
        elapsed = max(0.0, now - ts)
        tokens = min(float(limit), tokens + elapsed * refill_rate)

        allowed = tokens >= 1
        if allowed:
            tokens -= 1
        self._buckets[key] = (tokens, now)

        reset_seconds = 0 if tokens >= limit else int((limit - tokens) / refill_rate + 0.999)
        return RateLimitResult(
            allowed=allowed, limit=limit, remaining=int(tokens), reset_seconds=reset_seconds
        )


class FailOpenRateLimiter:
    """Wraps the Redis limiter with the local one as a fail-*bounded*-open
    fallback (§3.2.12) — a Redis outage degrades to conservative local
    limiting, it doesn't remove rate limiting entirely."""

    def __init__(
        self, primary: RedisTokenBucketRateLimiter, fallback: LocalTokenBucketRateLimiter
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        try:
            return await self._primary.check(key, limit=limit, window_seconds=window_seconds)
        except RedisError:
            log.error("rate_limit_check_failed_open", key=key, reason="redis_unavailable")
            return await self._fallback.check(key, limit=limit, window_seconds=window_seconds)
