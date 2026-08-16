"""Redis Streams-backed IngestionQueuePort (§3.2.7, §3.2.9's D3-3):
per-tenant sub-streams (``ingest:{tenant_id}``) plus a rotating list of
tenants-with-pending-work (``ingest:rotation``) — the round-robin
scheduler that gives every tenant a turn regardless of how deep any one
tenant's backlog is.

Consumer-group semantics (one shared group, ``ingestion``, per stream)
give at-least-once delivery for free: a message stays in the group's
pending-entries list (PEL) until explicitly acked, and Redis itself
tracks how many times each entry has been delivered — read via
``XPENDING``, bumped by every ``XCLAIM`` — so retry-vs-dead-letter needs
no separate counter of our own.
"""

from __future__ import annotations

from uuid import UUID

import redis.asyncio as redis_asyncio
from redis.exceptions import ResponseError

from aether.ports.ingestion_queue import QueuedMessage

_GROUP_NAME = "ingestion"
_ROTATION_KEY = "ingest:rotation"
_ROTATION_MEMBERS_KEY = "ingest:rotation:members"
_DLQ_STREAM = "ingest:dlq"
_DEFAULT_MAX_DELIVERIES = 5


def _stream_key(tenant_id: UUID) -> str:
    return f"ingest:{tenant_id}"


class RedisIngestionQueue:
    def __init__(
        self,
        client: redis_asyncio.Redis,
        *,
        consumer_name: str,
        max_deliveries: int = _DEFAULT_MAX_DELIVERIES,
    ) -> None:
        self._client = client
        self._consumer_name = consumer_name
        self._max_deliveries = max_deliveries

    async def enqueue(self, *, tenant_id: UUID, payload: dict[str, str]) -> None:
        stream_key = _stream_key(tenant_id)
        await self._ensure_group(stream_key)
        await self._client.xadd(stream_key, dict(payload))  # type: ignore[arg-type]  # redis-py gap, not ours
        await self._schedule(tenant_id)

    async def claim_next(self) -> QueuedMessage | None:
        while True:
            tenant_id_str = await self._client.lpop(_ROTATION_KEY)  # type: ignore[misc]  # redis-py gap, not ours
            if tenant_id_str is None:
                return None
            await self._client.srem(_ROTATION_MEMBERS_KEY, tenant_id_str)  # type: ignore[misc]  # redis-py gap, not ours
            tenant_id = UUID(tenant_id_str)
            stream_key = _stream_key(tenant_id)

            message = await self._claim_pending_retry(stream_key, tenant_id)
            if message is None:
                message = await self._claim_fresh(stream_key, tenant_id)
            if message is None:
                # This tenant's stream had nothing ready right now —
                # don't reschedule them; the next enqueue() will.
                continue

            # Optimistic reschedule: there may be more work for this
            # tenant. A later empty claim simply won't reschedule again,
            # which is how a drained tenant naturally falls out of
            # rotation without any explicit "is it really empty" check.
            await self._schedule(tenant_id)
            return message

    async def ack(self, message: QueuedMessage) -> None:
        await self._client.xack(
            _stream_key(message.tenant_id), _GROUP_NAME, message.stream_message_id
        )

    async def fail(self, message: QueuedMessage) -> bool:
        if message.delivery_count >= self._max_deliveries:
            dlq_fields: dict[str, str] = {
                **message.payload,
                "tenant_id": str(message.tenant_id),
                "original_message_id": message.stream_message_id,
                "delivery_count": str(message.delivery_count),
            }
            await self._client.xadd(_DLQ_STREAM, dlq_fields)  # type: ignore[arg-type]  # redis-py gap, not ours
            await self._client.xack(
                _stream_key(message.tenant_id), _GROUP_NAME, message.stream_message_id
            )
            return True
        # Left un-acked in the PEL — claim_next()'s pending-retry check
        # picks it back up (and claim_next() already rescheduled this
        # tenant into rotation on the way in, so no re-add needed here).
        return False

    async def _ensure_group(self, stream_key: str) -> None:
        try:
            await self._client.xgroup_create(stream_key, _GROUP_NAME, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _schedule(self, tenant_id: UUID) -> None:
        tenant_id_str = str(tenant_id)
        added = await self._client.sadd(_ROTATION_MEMBERS_KEY, tenant_id_str)  # type: ignore[misc]  # redis-py gap, not ours
        if added:
            await self._client.rpush(_ROTATION_KEY, tenant_id_str)  # type: ignore[misc]  # redis-py gap, not ours

    async def _claim_pending_retry(self, stream_key: str, tenant_id: UUID) -> QueuedMessage | None:
        pending = await self._client.xpending_range(
            stream_key, _GROUP_NAME, min="-", max="+", count=1
        )
        if not pending:
            return None
        entry = pending[0]
        claimed = await self._client.xclaim(
            stream_key,
            _GROUP_NAME,
            self._consumer_name,
            min_idle_time=0,
            message_ids=[entry["message_id"]],
        )
        if not claimed:
            return None  # raced with another consumer claiming it first
        msg_id, fields = claimed[0]
        return QueuedMessage(
            stream_message_id=msg_id,
            tenant_id=tenant_id,
            payload=dict(fields),
            # xpending_range's times_delivered reflects the count *before*
            # this claim — XCLAIM itself is what bumps it, so the accurate
            # post-claim count is one more than what we just read, not a
            # second XPENDING round-trip's worth of eventual consistency.
            delivery_count=entry["times_delivered"] + 1,
        )

    async def _claim_fresh(self, stream_key: str, tenant_id: UUID) -> QueuedMessage | None:
        response = await self._client.xreadgroup(
            _GROUP_NAME, self._consumer_name, {stream_key: ">"}, count=1
        )
        if not response or not response[0][1]:
            return None
        _, entries = response[0]
        msg_id, fields = entries[0]
        return QueuedMessage(
            stream_message_id=msg_id, tenant_id=tenant_id, payload=dict(fields), delivery_count=1
        )
