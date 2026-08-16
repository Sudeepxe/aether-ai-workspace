"""UpdateBudget use case (issue #39). Admin+ only (MANAGE_BUDGETS,
enforced at the HTTP layer, matching UpdateWorkspace's split); owns the
ETag optimistic-concurrency semantics, same pattern as UpdateWorkspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aether.domain.errors import BudgetConcurrencyConflictError
from aether.ports.audit import AuditLogPort
from aether.ports.metering import Budget, BudgetRepositoryPort
from aether.ports.security import IdPort


@dataclass(frozen=True, slots=True)
class UpdateBudgetCommand:
    workspace_id: UUID
    actor_user_id: UUID
    monthly_limit_microcents: int
    expected_updated_at: datetime


class UpdateBudget:
    def __init__(
        self, *, budgets: BudgetRepositoryPort, audit_log: AuditLogPort, ids: IdPort
    ) -> None:
        self._budgets = budgets
        self._audit_log = audit_log
        self._ids = ids

    async def execute(self, command: UpdateBudgetCommand) -> Budget:
        updated = await self._budgets.update_limit(
            command.workspace_id,
            monthly_limit_microcents=command.monthly_limit_microcents,
            expected_updated_at=command.expected_updated_at,
        )
        if updated is None:
            # A budget row always exists once the workspace does (see
            # GetBudget's docstring) — None here only ever means a stale
            # If-Match, never "not found".
            raise BudgetConcurrencyConflictError(str(command.workspace_id))

        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            actor_key_id=None,
            action="budget.updated",
            target_type="budget",
            target_id=command.workspace_id,
            metadata={"monthly_limit_microcents": command.monthly_limit_microcents},
        )
        return updated
