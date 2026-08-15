"""Redis-backed StreamBufferPort (§3.2.3): a per-generation sorted set,
score = event seq, so ``read_after`` is an O(log n) range query — exactly
what Last-Event-ID resume needs ("give me everything after seq N"), and
what makes that resume replica-independent: whichever replica the
reconnect lands on reads the same Redis key, not in-process state.

Short TTL, refreshed on every append: long enough to outlive an
in-progress generation plus a reasonable reconnect grace window, short
enough that "nothing in Redis is the only copy of anything that matters"
(§3.2.12) stays true — the persisted (possibly partial) message is the
durable fallback once the buffer expires.
"""

from __future__ import annotations

import json
from uuid import UUID

import redis.asyncio as redis_asyncio

from aether.ports.streaming import BufferedEvent

_BUFFER_TTL_SECONDS = 300


class RedisStreamBuffer:
    def __init__(
        self, client: redis_asyncio.Redis, *, ttl_seconds: int = _BUFFER_TTL_SECONDS
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds

    async def append(self, workspace_id: UUID, generation_id: UUID, event: BufferedEvent) -> None:
        key = _key(workspace_id, generation_id)
        member = json.dumps({"seq": event.seq, "event_type": event.event_type, "data": event.data})
        await self._client.zadd(key, {member: event.seq})
        await self._client.expire(key, self._ttl_seconds)

    async def read_after(
        self, workspace_id: UUID, generation_id: UUID, *, after_seq: int
    ) -> list[BufferedEvent]:
        members = await self._client.zrangebyscore(
            _key(workspace_id, generation_id), after_seq + 1, "+inf"
        )
        events = [json.loads(m) for m in members]
        return [
            BufferedEvent(seq=e["seq"], event_type=e["event_type"], data=e["data"]) for e in events
        ]


def _key(workspace_id: UUID, generation_id: UUID) -> str:
    return f"genbuf:{workspace_id}:{generation_id}"
