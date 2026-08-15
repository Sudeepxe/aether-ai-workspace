"""GetBudget use case (issue #39). Connection-bound — lives on
WorkspaceScope, like GetWorkspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.metering import Budget, BudgetRepositoryPort


@dataclass(frozen=True, slots=True)
class GetBudgetCommand:
    workspace_id: UUID


class GetBudget:
    def __init__(self, *, budgets: BudgetRepositoryPort) -> None:
        self._budgets = budgets

    async def execute(self, command: GetBudgetCommand) -> Budget:
        budget = await self._budgets.get(command.workspace_id)
        # CreateWorkspace provisions a budget row atomically with the
        # workspace itself — a caller that resolved WorkspaceScope (so
        # the workspace exists and they're a member of it) always has one.
        assert budget is not None  # noqa: S101 — invariant maintained by CreateWorkspace
        return budget
