"""Audit-log port (Blueprint §8.1, FR-AD-1).

A narrow append-only-ledger interface, not a repository — audit_events
is INSERT-only by grant (ADR §3.7.3), so this port deliberately has no
update/delete method for any adapter to implement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from aether.domain.entities import AuditEvent

__all__ = ["AuditEvent", "AuditLogPort"]


class AuditLogPort(Protocol):
    async def record(
        self,
        *,
        id: UUID,
        workspace_id: UUID | None,
        actor_user_id: UUID | None,
        actor_key_id: UUID | None,
        action: str,
        target_type: str,
        target_id: UUID,
        metadata: dict[str, Any],
    ) -> None: ...

    async def list_by_workspace(
        self, workspace_id: UUID, *, cursor: tuple[datetime, UUID] | None, limit: int
    ) -> list[AuditEvent]:
        """Keyset-paginated (ADR-4.4: cursor only, offset banned), newest
        first: ``cursor`` is the ``(occurred_at, id)`` of the last item
        from the previous page, or None for the first page."""
        ...
