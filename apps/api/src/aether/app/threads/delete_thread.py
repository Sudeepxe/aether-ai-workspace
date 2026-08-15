"""DeleteThread use case (§4.3: DELETE .../threads/{thread}).

Soft-delete, idempotent no-op if already deleted or never existed — same
posture as DeleteWorkspace. Not audit-logged: FR-AD-1's audit trail is
scoped to security/compliance-relevant administrative actions (workspace
lifecycle, membership/role changes, invitations) per its own framing, not
routine product usage — a deleted chat thread doesn't carry the same
"who changed what governance-relevant thing" significance a deleted
workspace or demoted member does.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.repositories import ThreadRepositoryPort
from aether.ports.security import ClockPort


@dataclass(frozen=True, slots=True)
class DeleteThreadCommand:
    workspace_id: UUID
    thread_id: UUID


class DeleteThread:
    def __init__(self, *, threads: ThreadRepositoryPort, clock: ClockPort) -> None:
        self._threads = threads
        self._clock = clock

    async def execute(self, command: DeleteThreadCommand) -> None:
        await self._threads.soft_delete(
            command.workspace_id, command.thread_id, deleted_at=self._clock.now()
        )
