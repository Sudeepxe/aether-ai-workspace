"""Real-Redis proof for the idempotency replay store (S10 #106, ADR-4.6)."""

from __future__ import annotations

import pytest
import redis.asyncio as redis_asyncio

from aether.adapters.redis.idempotency import RedisIdempotencyStore
from aether.ports.idempotency import IdempotencySnapshot

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def test_get_returns_none_when_absent(redis_client: redis_asyncio.Redis) -> None:
    store = RedisIdempotencyStore(redis_client)
    assert await store.get("idempotency:absent") is None


async def test_set_then_get_round_trips(redis_client: redis_asyncio.Redis) -> None:
    store = RedisIdempotencyStore(redis_client)
    snapshot = IdempotencySnapshot(
        body_sha256="deadbeef", status_code=201, response_body='{"id": "1"}'
    )

    await store.set("idempotency:key-a", snapshot, ttl_seconds=86400)
    fetched = await store.get("idempotency:key-a")

    assert fetched == snapshot


async def test_set_applies_a_real_ttl(redis_client: redis_asyncio.Redis) -> None:
    store = RedisIdempotencyStore(redis_client)
    snapshot = IdempotencySnapshot(body_sha256="x", status_code=200, response_body="{}")

    await store.set("idempotency:key-b", snapshot, ttl_seconds=86400)

    ttl = await redis_client.ttl("idempotency:key-b")
    assert 0 < ttl <= 86400
