"""GET /generations/{id} (§4.3): live status from the Redis buffer.

Deliberately narrow scope for S3: this reads the buffer only. Once the
buffer's TTL expires, this 404s — the documented fallback (§3.2.3, §5.2)
is client-side: the SPA already knows the resulting message's
client_message_id/message_id from context and reconciles by fetching the
thread's messages instead of calling this endpoint again.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.errors import GenerationNotFoundError
from aether.ports.streaming import StreamBufferPort


@dataclass(frozen=True, slots=True)
class GetGenerationStatusCommand:
    workspace_id: UUID
    generation_id: UUID


@dataclass(frozen=True, slots=True)
class GenerationStatusView:
    generation_id: UUID
    last_seq: int
    latest_event_type: str
    is_done: bool


class GetGenerationStatus:
    def __init__(self, *, buffer: StreamBufferPort) -> None:
        self._buffer = buffer

    async def execute(self, command: GetGenerationStatusCommand) -> GenerationStatusView:
        events = await self._buffer.read_after(
            command.workspace_id, command.generation_id, after_seq=-1
        )
        if not events:
            raise GenerationNotFoundError(str(command.generation_id))
        latest = max(events, key=lambda e: e.seq)
        return GenerationStatusView(
            generation_id=command.generation_id,
            last_seq=latest.seq,
            latest_event_type=latest.event_type,
            is_done=latest.event_type == "done",
        )
