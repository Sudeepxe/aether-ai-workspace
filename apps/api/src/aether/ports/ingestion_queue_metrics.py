"""IngestionQueueMetricsPort (S9, §10.4's Ingestion dashboard): the
read-side query behind the ``aether_ingestion_queue_depth``/
``aether_ingestion_dlq_depth``/``aether_ingestion_pending_tenants``
gauges. Same rationale as ``ports.outbox_metrics``: a narrow port for
one consumer's stats need, not a growth of ``IngestionQueuePort`` (whose
Protocol every producer/consumer call site shares).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IngestionQueueStats:
    total_queued: int
    """Sum of pending stream length across every tenant currently in
    rotation — an approximation (a tenant claimed mid-poll can undercount
    by the in-flight message), acceptable for a dashboard gauge."""
    pending_tenants: int
    """Distinct tenants with at least one pending message — the
    low-cardinality fairness signal (see the gauge's own docstring on
    why per-tenant depth isn't exposed directly)."""
    dlq_depth: int


class IngestionQueueMetricsPort(Protocol):
    async def get_stats(self) -> IngestionQueueStats: ...
