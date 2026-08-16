"""Worker-process composition root — the worker's counterpart to
http/composition.py. Same discipline: this is the one place in the
worker process allowed to import adapters directly (Blueprint §3.3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import asyncpg
import redis.asyncio as redis_asyncio

from aether.adapters.clock import SystemClock
from aether.adapters.email.resend import ResendEmailAdapter
from aether.adapters.email.smtp import SmtpEmailAdapter
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.pool import create_pool
from aether.adapters.redis.ingestion_queue import RedisIngestionQueue
from aether.app.ingestion.dispatch_outbox_to_queue import DispatchIngestionOutbox
from aether.app.notifications.dispatch_email_outbox import DispatchEmailOutbox
from aether.config import Settings
from aether.ports.email import EmailPort
from aether.ports.ingestion_queue import IngestionQueuePort
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.security import ClockPort


@dataclass
class WorkerContainer:
    db_pool: asyncpg.Pool
    redis_client: redis_asyncio.Redis
    outbox: OutboxRepositoryPort
    email: EmailPort
    clock: ClockPort
    ingestion_queue: IngestionQueuePort
    """The consumer loop itself (claim -> process -> ack/fail) isn't run
    by this worker yet — there's no real document-processing handler
    until issue #46 lands. This container only wires the outbox->queue
    dispatcher, which is real, self-contained infrastructure regardless
    of whether anything downstream consumes from the queue yet."""
    dispatch_email_outbox: DispatchEmailOutbox
    dispatch_ingestion_outbox: DispatchIngestionOutbox

    async def aclose(self) -> None:
        await self.db_pool.close()
        await self.redis_client.aclose()


def _build_email_adapter(settings: Settings) -> EmailPort:
    if settings.email_provider == "resend":
        return ResendEmailAdapter(api_key=settings.resend_api_key, sender=settings.email_sender)
    return SmtpEmailAdapter(
        host=settings.smtp_host, port=settings.smtp_port, sender=settings.email_sender
    )


async def build_worker_container(settings: Settings) -> WorkerContainer:
    db_pool = await create_pool(settings.database_worker_url)
    redis_client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]  # redis-py gap, not ours
        settings.redis_url, decode_responses=True
    )
    outbox = PostgresOutboxRepository(db_pool)
    email = _build_email_adapter(settings)
    clock = SystemClock()
    # One consumer name per worker process — distinct identities matter
    # for Redis Streams consumer-group bookkeeping (XPENDING/XCLAIM
    # attribute in-flight messages to a specific consumer), even though
    # this dispatcher's own enqueue() calls don't read as a consumer.
    ingestion_queue = RedisIngestionQueue(
        redis_client, consumer_name=f"worker-{uuid.uuid4().hex[:8]}"
    )

    return WorkerContainer(
        db_pool=db_pool,
        redis_client=redis_client,
        outbox=outbox,
        email=email,
        clock=clock,
        ingestion_queue=ingestion_queue,
        dispatch_email_outbox=DispatchEmailOutbox(outbox=outbox, email=email, clock=clock),
        dispatch_ingestion_outbox=DispatchIngestionOutbox(
            outbox=outbox, queue=ingestion_queue, clock=clock
        ),
    )
