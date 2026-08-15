"""UpdateWorkspace use case. Role gating (Admin+) happens at the HTTP
layer via domain.policy before this executes; this use case owns the
ETag optimistic-concurrency semantics (§4.3: "ETag on PATCH")."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from aether.domain.entities import Workspace
from aether.domain.errors import WorkspaceConcurrencyConflictError, WorkspaceNotFoundError
from aether.ports.audit import AuditLogPort
from aether.ports.repositories import WorkspaceRepositoryPort
from aether.ports.security import IdPort


@dataclass(frozen=True, slots=True)
class UpdateWorkspaceCommand:
    workspace_id: UUID
    actor_user_id: UUID
    name: str
    settings: dict[str, Any]
    model_policy: dict[str, Any]
    expected_updated_at: datetime


class UpdateWorkspace:
    def __init__(
        self, *, workspaces: WorkspaceRepositoryPort, audit_log: AuditLogPort, ids: IdPort
    ) -> None:
        self._workspaces = workspaces
        self._audit_log = audit_log
        self._ids = ids

    async def execute(self, command: UpdateWorkspaceCommand) -> Workspace:
        updated = await self._workspaces.update(
            command.workspace_id,
            name=command.name,
            settings=command.settings,
            model_policy=command.model_policy,
            expected_updated_at=command.expected_updated_at,
        )
        if updated is None:
            # Disambiguate 404 (never existed / already deleted) from 409
            # (existed, but expected_updated_at was stale) — the update's
            # conditional WHERE can't tell these apart on its own.
            current = await self._workspaces.get_by_id(command.workspace_id)
            if current is None:
                raise WorkspaceNotFoundError(str(command.workspace_id))
            raise WorkspaceConcurrencyConflictError(str(command.workspace_id))

        await self._audit_log.record(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            actor_key_id=None,
            action="workspace.updated",
            target_type="workspace",
            target_id=command.workspace_id,
            metadata={},
        )
        return updated
