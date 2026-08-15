"""GetWorkspace use case. Authorization (caller must be a member) is
already established by the time this runs — see http/deps.py's
get_workspace_scope, which resolves the caller's membership before any
workspace-scoped route body executes."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Workspace
from aether.domain.errors import WorkspaceNotFoundError
from aether.ports.repositories import WorkspaceRepositoryPort


@dataclass(frozen=True, slots=True)
class GetWorkspaceCommand:
    workspace_id: UUID


class GetWorkspace:
    def __init__(self, *, workspaces: WorkspaceRepositoryPort) -> None:
        self._workspaces = workspaces

    async def execute(self, command: GetWorkspaceCommand) -> Workspace:
        # Reachable even though membership already proved the caller has a
        # role here: soft-delete doesn't cascade-remove memberships, so a
        # member can outlive their now-deleted workspace's visible row.
        workspace = await self._workspaces.get_by_id(command.workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(str(command.workspace_id))
        return workspace
