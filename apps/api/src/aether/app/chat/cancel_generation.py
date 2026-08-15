"""DELETE /generations/{id} (issue #27): publishes on the workspace-
scoped Redis cancellation channel; whichever replica is actually
streaming that generation aborts within one token flush (§3.2.3)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.streaming import CancellationPort


@dataclass(frozen=True, slots=True)
class CancelGenerationCommand:
    workspace_id: UUID
    generation_id: UUID


class CancelGeneration:
    def __init__(self, *, cancellation: CancellationPort) -> None:
        self._cancellation = cancellation

    async def execute(self, command: CancelGenerationCommand) -> None:
        # No existence check before publishing: publishing to a channel
        # nobody is subscribed to is a harmless no-op (Redis pub/sub
        # semantics), and there is no generations table to check against
        # — the buffer (checked by GET .../generations/{id}, not this
        # path) is the closest thing to a existence oracle, and cancel is
        # deliberately never gated behind one (§4.5: "never shed...
        # cancellation — it frees capacity").
        await self._cancellation.publish_cancel(command.workspace_id, command.generation_id)
