"""ListMessages use case (§4.3: GET .../threads/{thread}/messages,
cursor-paginated on seq DESC — ADR-8.2's seq is itself a natural, gapless
pagination cursor)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Message
from aether.ports.repositories import MessageRepositoryPort


@dataclass(frozen=True, slots=True)
class ListMessagesCommand:
    workspace_id: UUID
    thread_id: UUID
    after_seq: int | None
    limit: int


class ListMessages:
    def __init__(self, *, messages: MessageRepositoryPort) -> None:
        self._messages = messages

    async def execute(self, command: ListMessagesCommand) -> list[Message]:
        return await self._messages.list_by_thread(
            command.workspace_id,
            command.thread_id,
            after_seq=command.after_seq,
            limit=command.limit,
        )
