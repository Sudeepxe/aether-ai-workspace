"""CreateThread use case (§4.3: POST /workspaces/{ws}/threads)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Thread
from aether.ports.repositories import ThreadRepositoryPort
from aether.ports.security import IdPort


@dataclass(frozen=True, slots=True)
class CreateThreadCommand:
    workspace_id: UUID
    created_by: UUID
    title: str | None


class CreateThread:
    def __init__(self, *, threads: ThreadRepositoryPort, ids: IdPort) -> None:
        self._threads = threads
        self._ids = ids

    async def execute(self, command: CreateThreadCommand) -> Thread:
        return await self._threads.create(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            created_by=command.created_by,
            title=command.title,
        )
