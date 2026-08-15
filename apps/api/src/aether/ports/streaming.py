"""Cross-replica streaming-session state ports (Blueprint §3.2.3, Ch.3
self-review F-3): the per-generation Redis buffer that makes SSE
Last-Event-ID resume replica-independent, and the pub/sub cancellation
channel that makes ``DELETE /generations/{id}`` replica-independent too.
Without both mechanisms, "no sticky sessions" (§3.9.2) is an assertion
with no backing mechanism — exactly the gap F-3 exists to close.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class BufferedEvent:
    """One SSE event already serialized to its ``event:``/``data:`` wire
    payload — the buffer replays these verbatim on resume, it never
    re-derives them from domain objects."""

    seq: int
    event_type: str
    data: str


class StreamBufferPort(Protocol):
    """Short-TTL, per-generation append log. A reconnect with
    ``Last-Event-ID`` can land on any replica and call ``read_after`` to
    replay everything after the client's last-seen seq — the mechanism
    that makes cross-replica resume real rather than aspirational.

    ``workspace_id`` is folded into the buffer's key namespace (not just
    a filter applied after the fact) — the same "explicit typed
    parameter" discipline §3.7.2 layer 3 already uses for invitations:
    there is no ``generations`` table for RLS to key on, so a caller
    from workspace A must be structurally unable to read workspace B's
    buffer even if it somehow learned B's generation_id."""

    async def append(
        self, workspace_id: UUID, generation_id: UUID, event: BufferedEvent
    ) -> None: ...

    async def read_after(
        self, workspace_id: UUID, generation_id: UUID, *, after_seq: int
    ) -> list[BufferedEvent]: ...


class CancellationSubscription(Protocol):
    def is_cancelled(self) -> bool: ...


class CancellationPort(Protocol):
    """Redis pub/sub cancellation channel (§3.2.3): ``DELETE
    /generations/{id}`` publishes on this channel from whichever replica
    received the DELETE; the replica actually streaming that generation
    — which may be a *different* replica — subscribes for its own
    generation_id and aborts within one token flush.

    ``workspace_id`` is folded into the channel name for the same reason
    as ``StreamBufferPort`` above — a caller can only ever publish on
    (and subscribe to) their own workspace's channel namespace, since
    ``workspace_id`` here always comes from the authenticated caller's
    own URL path, never from attacker-supplied input."""

    async def publish_cancel(self, workspace_id: UUID, generation_id: UUID) -> None: ...

    def subscription(
        self, workspace_id: UUID, generation_id: UUID
    ) -> AbstractAsyncContextManager[CancellationSubscription]:
        """An async context manager: entering starts listening for a
        cancel signal on this generation's channel, exiting stops
        listening and releases the subscription's resources."""
        ...
