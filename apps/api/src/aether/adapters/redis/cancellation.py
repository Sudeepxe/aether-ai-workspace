"""Redis pub/sub-backed CancellationPort (§3.2.3, Ch.3 F-3): whichever
replica receives ``DELETE /generations/{id}`` publishes on this
generation's channel; the replica actually streaming it — possibly a
different one — is subscribed and aborts within one token flush. This is
the second half of the mechanism that makes "no sticky sessions" true.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import redis.asyncio as redis_asyncio

from aether.ports.streaming import CancellationSubscription


class _Subscription:
    def __init__(self) -> None:
        self._cancelled = False

    def is_cancelled(self) -> bool:
        return self._cancelled

    def _mark_cancelled(self) -> None:
        self._cancelled = True


class RedisCancellationChannel:
    def __init__(self, client: redis_asyncio.Redis) -> None:
        self._client = client

    async def publish_cancel(self, workspace_id: UUID, generation_id: UUID) -> None:
        await self._client.publish(_channel(workspace_id, generation_id), "1")

    @asynccontextmanager
    async def subscription(
        self, workspace_id: UUID, generation_id: UUID
    ) -> AsyncIterator[CancellationSubscription]:
        pubsub = self._client.pubsub()
        channel = _channel(workspace_id, generation_id)
        await pubsub.subscribe(channel)
        sub = _Subscription()
        listener = asyncio.create_task(_listen(pubsub, sub))
        try:
            yield sub
        finally:
            listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py gap, not ours


async def _listen(pubsub: redis_asyncio.client.PubSub, sub: _Subscription) -> None:
    async for message in pubsub.listen():
        if message["type"] == "message":
            sub._mark_cancelled()
            return


def _channel(workspace_id: UUID, generation_id: UUID) -> str:
    return f"cancel:{workspace_id}:{generation_id}"
