"""UpdateThread use case (§4.3: PATCH .../threads/{thread} — title only).
No ETag/If-Match: a thread title rename is low-conflict-risk, single-
field metadata, not the "mutable config" class of resource §4.2's ETag
rule targets (workspace settings, budgets, member roles) — matching the
membership-role PATCH precedent, which also has no ETag."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Thread
from aether.domain.errors import ThreadNotFoundError
from aether.ports.repositories import ThreadRepositoryPort


@dataclass(frozen=True, slots=True)
class UpdateThreadCommand:
    workspace_id: UUID
    thread_id: UUID
    title: str | None


class UpdateThread:
    def __init__(self, *, threads: ThreadRepositoryPort) -> None:
        self._threads = threads

    async def execute(self, command: UpdateThreadCommand) -> Thread:
        updated = await self._threads.update_title(
            command.workspace_id, command.thread_id, title=command.title
        )
        if updated is None:
            raise ThreadNotFoundError(str(command.thread_id))
        return updated
