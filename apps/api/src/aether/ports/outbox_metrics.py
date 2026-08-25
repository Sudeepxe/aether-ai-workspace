"""OutboxMetricsPort (S9, §10.4): the read-side query behind the
``aether_outbox_lag_seconds``/``aether_outbox_dlq_depth`` gauges (page-
grade alerts, §10.4 — outbox lag > 5min, DLQ > 0 sustained 15min).

A separate, narrow port rather than new methods on
``OutboxRepositoryPort`` — that Protocol is shared by every producer
call site (``enqueue``) across the whole app layer, and none of them
need a stats query; this mirrors the codebase's existing pattern of
purpose-built worker-plane ports (``workspace_deletion``,
``workspace_export``, ``deletion_verification``) instead of growing a
shared interface for one consumer's need.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class OutboxStats:
    oldest_pending_seconds: float | None
    """Age of the oldest not-yet-dispatched, not-yet-exhausted row for
    this event type, or ``None`` when nothing is pending (the healthy
    steady state — a gauge with no data point is preferable to a false
    zero, which would silently read as "just dispatched")."""
    dlq_depth: int
    """Rows that exhausted their dispatch attempts (§3.6.2's "capped
    attempts (5, exp backoff) -> DLQ") and are stuck until an operator
    intervenes."""


class OutboxMetricsPort(Protocol):
    async def get_stats(self, *, event_type: str, max_attempts: int) -> OutboxStats: ...
