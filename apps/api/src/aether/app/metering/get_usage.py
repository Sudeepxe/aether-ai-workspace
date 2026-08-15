"""GetUsage use case (issue #39, FR-AD-2's usage dashboard). Pool-bound
— see ports.metering's module docstring — so this lives at the
Container level (like SendMessage), not on WorkspaceScope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aether.ports.metering import UsageLedgerPort, UsageRollup


@dataclass(frozen=True, slots=True)
class GetUsageCommand:
    workspace_id: UUID
    since: datetime


class GetUsage:
    def __init__(self, *, usage_ledger: UsageLedgerPort) -> None:
        self._usage_ledger = usage_ledger

    async def execute(self, command: GetUsageCommand) -> UsageRollup:
        return await self._usage_ledger.rollup(command.workspace_id, since=command.since)
