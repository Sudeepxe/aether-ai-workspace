"""ListThreads use case (§4.3: GET /workspaces/{ws}/threads, cursor-
paginated on (created_at, id) per ADR-4.4). A thin pass-through — the
HTTP layer owns cursor encoding/decoding and next_cursor computation
(requesting ``limit + 1`` and trimming), matching where the opaque-
cursor wire format is otherwise handled."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aether.domain.entities import Thread
from aether.ports.repositories import ThreadRepositoryPort


@dataclass(frozen=True, slots=True)
class ListThreadsCommand:
    workspace_id: UUID
    after: tuple[datetime, UUID] | None
    limit: int


class ListThreads:
    def __init__(self, *, threads: ThreadRepositoryPort) -> None:
        self._threads = threads

    async def execute(self, command: ListThreadsCommand) -> list[Thread]:
        return await self._threads.list_by_workspace(
            command.workspace_id, after=command.after, limit=command.limit
        )
