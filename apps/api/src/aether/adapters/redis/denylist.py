"""Redis-backed JTI denylist for session revocation (ADR-3.6).

Fail-open on Redis outage: a revocation *check* that can't reach Redis
returns "not denied" (bounded by the access token's own 15-min TTL) with
a loud (ERROR-level, structured) log line — never fail-closed, which
would turn a cache-tier outage into a total authentication outage. This
applies only to ``is_denied`` (the read path used on every request);
``deny`` (the write path, used at logout/revocation time) lets Redis
errors propagate — a revocation that silently didn't happen is a security
bug, not something to swallow quietly.
"""

from __future__ import annotations

from uuid import UUID

import redis.asyncio as redis_asyncio
from redis.exceptions import RedisError

from aether.logging import get_logger

log = get_logger(__name__)

_KEY_PREFIX = "aether:revoked-jti:"


class RedisJtiDenylist:
    def __init__(self, client: redis_asyncio.Redis) -> None:
        self._client = client

    async def deny(self, jti: UUID, *, ttl_seconds: int) -> None:
        await self._client.set(f"{_KEY_PREFIX}{jti}", "1", ex=max(ttl_seconds, 1))

    async def is_denied(self, jti: UUID) -> bool:
        try:
            return bool(await self._client.exists(f"{_KEY_PREFIX}{jti}"))
        except RedisError:
            log.error(
                "revocation_check_failed_open",
                jti=str(jti),
                reason="redis_unavailable",
            )
            return False
