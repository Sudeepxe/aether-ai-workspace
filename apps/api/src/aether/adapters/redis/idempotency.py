"""Redis-backed idempotency replay store (ADR-4.6) — reuses the existing
Redis infra (like rate limiting, stream buffers, revocation) rather than
a new Postgres table; a 24h-TTL cache is exactly what Redis's native
EXPIRE already does declaratively, with no separate housekeeping sweep
needed.
"""

from __future__ import annotations

import json

import redis.asyncio as redis_asyncio

from aether.ports.idempotency import IdempotencySnapshot


class RedisIdempotencyStore:
    def __init__(self, client: redis_asyncio.Redis) -> None:
        self._client = client

    async def get(self, key: str) -> IdempotencySnapshot | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        return IdempotencySnapshot(
            body_sha256=data["body_sha256"],
            status_code=data["status_code"],
            response_body=data["response_body"],
        )

    async def set(self, key: str, snapshot: IdempotencySnapshot, *, ttl_seconds: int) -> None:
        value = json.dumps(
            {
                "body_sha256": snapshot.body_sha256,
                "status_code": snapshot.status_code,
                "response_body": snapshot.response_body,
            }
        )
        await self._client.set(key, value, ex=ttl_seconds)
