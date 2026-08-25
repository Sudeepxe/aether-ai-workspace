"""The generic ingestion-queue consumer loop (§3.2.7/§3.2.8): claim one
message, hand it to a handler, ack on success or let ``fail()`` decide
retry-vs-dead-letter on an exception.

Deliberately handler-agnostic — the real document-processing handler
(malware scan -> parse -> chunk -> embed) lands in issues #46/#47; this
loop only owns delivery semantics, not pipeline stages. A handler is
responsible for its own idempotency (content-hash-keyed effects) since
at-least-once delivery means the same message can reach it more than
once after a failure between "effects applied" and "acked".

Graceful shutdown: a message already claimed when ``stop`` fires still
finishes processing (the handler is never interrupted mid-effect) — the
loop only checks ``stop`` between claims, never abandons in-flight work.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from aether.observability.tracing import linked_span
from aether.ports.ingestion_queue import IngestionQueuePort, QueuedMessage

_DEFAULT_IDLE_POLL_SECONDS = 0.1


async def run_ingestion_consumer(
    queue: IngestionQueuePort,
    handler: Callable[[QueuedMessage], Awaitable[None]],
    *,
    stop: asyncio.Event,
    idle_poll_seconds: float = _DEFAULT_IDLE_POLL_SECONDS,
) -> None:
    while not stop.is_set():
        message = await queue.claim_next()
        if message is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle_poll_seconds)
            except TimeoutError:
                pass  # normal: nothing pending within this idle window
            continue
        trace_context = json.loads(message.payload.get("trace_context", "{}"))
        try:
            with linked_span("ingestion.consume", trace_context):
                await handler(message)
        except Exception:
            await queue.fail(message)
        else:
            await queue.ack(message)
