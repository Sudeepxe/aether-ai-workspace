"""SSE wire-format layer (§4.4, normative) — where domain StreamEvents
and buffered replay events become actual ``text/event-stream`` bytes.

The key architectural move for cross-replica resume (§3.2.3, Ch.3 F-3):
the HTTP response body **never** iterates the orchestrator's async
generator directly. A new POST spawns generation as an independent
background task (so it keeps running, keeps writing to the Redis buffer,
regardless of whether the client that started it is still connected),
and the response body instead *follows the buffer* by polling
``read_after`` — the exact same code path a reconnect on a *different*
replica uses, since both are just "replay the shared Redis buffer from
some seq." There is deliberately only one read path, not a "live" path
and a separate "resume" path that could drift apart.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable
from uuid import UUID

from aether.domain.streaming import StreamEvent, event_payload, event_type_name
from aether.logging import get_logger
from aether.ports.streaming import BufferedEvent, StreamBufferPort

log = get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 15.0
_POLL_INTERVAL_SECONDS = 0.05
_DONE_EVENT_TYPE = "done"


def format_frame(*, generation_id: UUID, seq: int, event_type: str, data: str) -> bytes:
    return f"id: {generation_id}:{seq}\nevent: {event_type}\ndata: {data}\n\n".encode()


def heartbeat_frame() -> bytes:
    return b": heartbeat\n\n"


def buffer_event_from_domain(event: StreamEvent) -> BufferedEvent:
    return BufferedEvent(
        seq=event.seq, event_type=event_type_name(event), data=json.dumps(event_payload(event))
    )


async def drain(events: AsyncIterator[StreamEvent]) -> None:
    """Fully consumes the orchestrator's generator for its side effects
    (buffer writes, message persistence) — its yielded values are
    already in the buffer by the time they're yielded, so nothing here
    needs to re-read them; the HTTP response reads from the buffer
    independently via ``follow_buffer``."""
    async for _event in events:
        pass


def run_generation_in_background(coro: Awaitable[None]) -> asyncio.Task[None]:
    """Spawns generation as an independent task, decoupled from the
    requesting connection's lifetime — the whole point being that a
    client disconnect must not stop the assistant's reply from finishing
    and being persisted."""
    task = asyncio.ensure_future(coro)

    def _log_if_failed(t: asyncio.Task[None]) -> None:
        if not t.cancelled() and (exc := t.exception()) is not None:
            log.error("background_generation_failed", error=str(exc), exc_type=type(exc).__name__)

    task.add_done_callback(_log_if_failed)
    return task


async def follow_buffer(
    buffer: StreamBufferPort, workspace_id: UUID, generation_id: UUID, *, after_seq: int
) -> AsyncIterator[bytes]:
    """Polls the shared Redis buffer for events after ``after_seq``,
    yielding SSE frames as they appear, until a ``done`` event is seen.
    Runs identically whether this is the replica that started the
    generation or a different one that received the reconnect — the
    buffer, not process memory, is the source of truth.
    """
    cursor = after_seq
    elapsed_since_event = 0.0
    while True:
        events = await buffer.read_after(workspace_id, generation_id, after_seq=cursor)
        if not events:
            if elapsed_since_event >= HEARTBEAT_INTERVAL_SECONDS:
                yield heartbeat_frame()
                elapsed_since_event = 0.0
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed_since_event += _POLL_INTERVAL_SECONDS
            continue
        elapsed_since_event = 0.0
        for event in events:
            yield format_frame(
                generation_id=generation_id,
                seq=event.seq,
                event_type=event.event_type,
                data=event.data,
            )
            cursor = event.seq
            if event.event_type == _DONE_EVENT_TYPE:
                return
