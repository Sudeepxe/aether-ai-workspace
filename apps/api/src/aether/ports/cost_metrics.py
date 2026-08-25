"""CostMetricsPort (S9, §10.4's Cost dashboard): the read-side query
behind the ``aether_global_spend_microcents`` gauge. A narrow port
rather than a new method on the metering ports (``ports/metering.py``'s
``BudgetAdmissionPort``/``UsageLedgerPort`` are shaped around the
request-path admission/settlement flow, not periodic dashboard polling)
— same rationale as ``outbox_metrics``/``ingestion_queue_metrics``.
"""

from __future__ import annotations

from typing import Protocol


class CostMetricsPort(Protocol):
    async def get_global_spend_microcents(self) -> int: ...
