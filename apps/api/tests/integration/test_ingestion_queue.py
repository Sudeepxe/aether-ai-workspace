"""Real-Redis proof for RedisIngestionQueue (issue #45) — NFR-S-2's
literal acceptance measure (a queue fairness test), plus poison-message
DLQ and redelivery behavior, against actual Redis Streams consumer
groups, not a fake.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import redis.asyncio as redis_asyncio

from aether.adapters.redis.ingestion_queue import RedisIngestionQueue
from aether.app.ingestion.consume_queue import run_ingestion_consumer
from aether.ports.ingestion_queue import QueuedMessage

pytestmark = pytest.mark.integration


def _queue(redis_client: redis_asyncio.Redis, **kwargs: object) -> RedisIngestionQueue:
    return RedisIngestionQueue(redis_client, consumer_name="test-consumer", **kwargs)  # type: ignore[arg-type]


async def test_enqueue_and_claim_round_trips_the_payload(
    redis_client: redis_asyncio.Redis,
) -> None:
    queue = _queue(redis_client)
    tenant_id = uuid.uuid4()

    await queue.enqueue(tenant_id=tenant_id, payload={"doc_id": "abc"})
    message = await queue.claim_next()

    assert message is not None
    assert message.tenant_id == tenant_id
    assert message.payload["doc_id"] == "abc"
    assert message.delivery_count == 1

    await queue.ack(message)
    assert await queue.claim_next() is None


async def test_claim_next_returns_none_when_nothing_is_pending(
    redis_client: redis_asyncio.Redis,
) -> None:
    queue = _queue(redis_client)
    assert await queue.claim_next() is None


async def test_round_robin_prevents_a_large_backlog_from_starving_a_small_one(
    redis_client: redis_asyncio.Redis,
) -> None:
    """NFR-S-2's literal acceptance measure: a queue-fairness test."""
    queue = _queue(redis_client)
    big_tenant = uuid.uuid4()
    small_tenant = uuid.uuid4()

    for i in range(20):
        await queue.enqueue(tenant_id=big_tenant, payload={"seq": str(i)})
    await queue.enqueue(tenant_id=small_tenant, payload={"seq": "0"})

    # Claim exactly 2 messages — if this were plain FIFO/created_at
    # order, both would come from big_tenant's 20-deep backlog. Fair
    # round-robin guarantees small_tenant is served within its own
    # first turn, not after big_tenant's entire backlog drains.
    first = await queue.claim_next()
    second = await queue.claim_next()
    assert first is not None
    assert second is not None
    seen_tenants = {first.tenant_id, second.tenant_id}
    assert small_tenant in seen_tenants, (
        "small tenant's single job was starved behind the large tenant's backlog"
    )


async def test_poison_message_is_dead_lettered_after_max_deliveries(
    redis_client: redis_asyncio.Redis,
) -> None:
    queue = _queue(redis_client, max_deliveries=3)
    tenant_id = uuid.uuid4()
    await queue.enqueue(tenant_id=tenant_id, payload={"doc_id": "poison"})

    dead_lettered = False
    for _ in range(5):  # more attempts than max_deliveries allows
        message = await queue.claim_next()
        if message is None:
            break
        dead_lettered = await queue.fail(message)
        if dead_lettered:
            break

    assert dead_lettered is True
    assert await queue.claim_next() is None  # gone from the main stream

    dlq_entries = await redis_client.xrange("ingest:dlq")
    matching = [e for e in dlq_entries if e[1].get("doc_id") == "poison"]
    assert len(matching) == 1
    assert matching[0][1]["tenant_id"] == str(tenant_id)


async def test_get_stats_reflects_real_queue_depth_pending_tenants_and_dlq(
    redis_client: redis_asyncio.Redis,
) -> None:
    """S9 (§10.4's Ingestion dashboard): get_stats() isn't part of
    IngestionQueuePort (see ports/ingestion_queue_metrics.py's
    docstring) but must still be proven against real Redis, not assumed
    correct from reading the XLEN/SCARD calls."""
    queue = _queue(redis_client, max_deliveries=1)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    empty = await queue.get_stats()
    assert empty.total_queued == 0
    assert empty.pending_tenants == 0
    assert empty.dlq_depth == 0

    await queue.enqueue(tenant_id=tenant_a, payload={"doc_id": "a1"})
    await queue.enqueue(tenant_id=tenant_a, payload={"doc_id": "a2"})
    await queue.enqueue(tenant_id=tenant_b, payload={"doc_id": "b1"})

    populated = await queue.get_stats()
    assert populated.total_queued == 3
    assert populated.pending_tenants == 2
    assert populated.dlq_depth == 0

    # Dead-letter tenant_b's only message (max_deliveries=1: first fail
    # already exhausts it) — total_queued (lag+pending, the real "still
    # needs work" count) drops to just tenant_a's remaining backlog, and
    # dlq_depth rises. pending_tenants stays 2, not 1: claim_next()
    # optimistically reschedules a tenant the moment it hands out a
    # message, before the caller's later ack/fail outcome is known — see
    # claim_next()'s own docstring. That's a real, accepted imprecision
    # in this fairness signal, not a test bug.
    b_message = await queue.claim_next()
    while b_message is not None and b_message.tenant_id != tenant_b:
        b_message = await queue.claim_next()
    assert b_message is not None
    assert await queue.fail(b_message) is True

    after_dlq = await queue.get_stats()
    assert after_dlq.total_queued == 2
    assert after_dlq.pending_tenants == 2
    assert after_dlq.dlq_depth == 1


async def test_a_failed_message_is_redelivered_with_incremented_delivery_count(
    redis_client: redis_asyncio.Redis,
) -> None:
    queue = _queue(redis_client, max_deliveries=5)
    tenant_id = uuid.uuid4()
    await queue.enqueue(tenant_id=tenant_id, payload={"doc_id": "flaky"})

    first_attempt = await queue.claim_next()
    assert first_attempt is not None
    assert first_attempt.delivery_count == 1
    dead_lettered = await queue.fail(first_attempt)
    assert dead_lettered is False

    second_attempt = await queue.claim_next()
    assert second_attempt is not None
    # Same underlying message, redelivered with the same payload — the
    # mechanism a handler needs to implement its own idempotency check
    # (e.g. "have I already applied doc_id=flaky's effects?").
    assert second_attempt.stream_message_id == first_attempt.stream_message_id
    assert second_attempt.payload == first_attempt.payload
    assert second_attempt.delivery_count == 2

    await queue.ack(second_attempt)
    assert await queue.claim_next() is None


async def test_ingestion_consumer_loop_processes_and_acks_a_message(
    redis_client: redis_asyncio.Redis,
) -> None:
    queue = _queue(redis_client)
    tenant_id = uuid.uuid4()
    await queue.enqueue(tenant_id=tenant_id, payload={"doc_id": "x"})

    processed: list[QueuedMessage] = []

    async def handler(message: QueuedMessage) -> None:
        processed.append(message)

    stop = asyncio.Event()

    async def _stop_after_one() -> None:
        while not processed:
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            run_ingestion_consumer(queue, handler, stop=stop, idle_poll_seconds=0.01),
            _stop_after_one(),
        ),
        timeout=5.0,
    )

    assert len(processed) == 1
    assert processed[0].payload["doc_id"] == "x"
    assert await queue.claim_next() is None  # acked, not redelivered


async def test_ingestion_consumer_loop_sends_a_repeatedly_failing_handler_to_the_dlq(
    redis_client: redis_asyncio.Redis,
) -> None:
    queue = _queue(redis_client, max_deliveries=2)
    tenant_id = uuid.uuid4()
    await queue.enqueue(tenant_id=tenant_id, payload={"doc_id": "always-fails"})

    async def handler(message: QueuedMessage) -> None:
        raise RuntimeError("simulated poison message")

    stop = asyncio.Event()

    async def _stop_once_dead_lettered() -> None:
        for _ in range(200):
            entries = await redis_client.xrange("ingest:dlq")
            if any(e[1].get("doc_id") == "always-fails" for e in entries):
                stop.set()
                return
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            run_ingestion_consumer(queue, handler, stop=stop, idle_poll_seconds=0.01),
            _stop_once_dead_lettered(),
        ),
        timeout=5.0,
    )

    dlq_entries = await redis_client.xrange("ingest:dlq")
    assert any(e[1].get("doc_id") == "always-fails" for e in dlq_entries)
