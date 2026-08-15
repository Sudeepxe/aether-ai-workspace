"""GetThread use case (§4.3: GET /workspaces/{ws}/threads/{thread})."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Thread
from aether.domain.errors import ThreadNotFoundError
from aether.ports.repositories import ThreadRepositoryPort


@dataclass(frozen=True, slots=True)
class GetThreadCommand:
    workspace_id: UUID
    thread_id: UUID


class GetThread:
    def __init__(self, *, threads: ThreadRepositoryPort) -> None:
        self._threads = threads

    async def execute(self, command: GetThreadCommand) -> Thread:
        thread = await self._threads.get(command.workspace_id, command.thread_id)
        if thread is None:
            raise ThreadNotFoundError(str(command.thread_id))
        return thread
