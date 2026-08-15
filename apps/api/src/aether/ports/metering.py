"""Usage ledger and budget ports (§3.2.14, FR-AD-2/3).

Two different connection-lifetime shapes, matching the split already
established for chat (ports.repositories.MessageRepositoryPort vs
ports.chat.MessageStorePort):

- ``BudgetRepositoryPort`` is connection-bound — used by WorkspaceScope
  for the ordinary GET/PUT /budget HTTP endpoints, which fit the normal
  one-connection-per-request pattern.
- ``BudgetAdmissionPort`` and ``UsageLedgerPort`` are pool-bound — the
  admission check runs on the orchestrator's hot path (which holds no
  long-lived connection, same reasoning as MessageStorePort), and the
  ledger is written by the worker's settlement consumer, which has no
  per-request connection to bind to at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from aether.domain.entities import Budget, UsageEvent, UsageEventKind

__all__ = [
    "Budget",
    "BudgetAdmissionPort",
    "BudgetRepositoryPort",
    "UsageEvent",
    "UsageEventKind",
    "UsageLedgerPort",
    "UsageModelRollup",
    "UsageRollup",
]


class BudgetRepositoryPort(Protocol):
    async def get(self, workspace_id: UUID) -> Budget | None: ...

    async def create(
        self,
        *,
        workspace_id: UUID,
        monthly_limit_microcents: int,
        soft_pct: int,
        current_period_start: date,
    ) -> Budget: ...

    async def update_limit(
        self,
        workspace_id: UUID,
        *,
        monthly_limit_microcents: int,
        expected_updated_at: datetime,
    ) -> Budget | None:
        """Optimistic-concurrency update (ETag = updated_at), same
        pattern as WorkspaceRepositoryPort.update. None means the row
        doesn't exist *or* expected_updated_at was stale."""
        ...


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    allowed: bool
    soft_limit_crossed: bool
    settled_microcents: int
    monthly_limit_microcents: int


class BudgetAdmissionPort(Protocol):
    async def check(self, workspace_id: UUID, *, ceiling_microcents: int) -> AdmissionDecision:
        """Reads settled_microcents + monthly_limit_microcents and
        decides whether a request with this cost ceiling may proceed.
        A workspace with no budget row is treated as inadmissible (fail
        closed, not fail open — an unconfigured budget must never mean
        unlimited spend)."""
        ...

    async def check_global(self, *, ceiling_microcents: int) -> bool:
        """The global monthly kill switch (NFR-C-1), independent of any
        per-workspace budget — enforced at the Router, not the
        per-workspace Usage Metering layer."""
        ...


@dataclass(frozen=True, slots=True)
class UsageModelRollup:
    model: str
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    cost_microcents: int


@dataclass(frozen=True, slots=True)
class UsageRollup:
    workspace_id: UUID
    period_start: datetime
    by_model: list[UsageModelRollup]
    total_cost_microcents: int


class UsageLedgerPort(Protocol):
    async def record(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        user_id: UUID | None,
        kind: UsageEventKind,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_microcents: int,
        generation_id: UUID | None,
    ) -> UsageEvent: ...

    async def rollup(self, workspace_id: UUID, *, since: datetime) -> UsageRollup:
        """Aggregated tokens/cost/requests per model (FR-AD-2's usage
        dashboard) since the given timestamp."""
        ...
