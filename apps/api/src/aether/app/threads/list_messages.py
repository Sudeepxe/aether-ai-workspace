"""ListMessages use case (§4.3: GET .../threads/{thread}/messages,
cursor-paginated on seq DESC — ADR-8.2's seq is itself a natural, gapless
pagination cursor)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Citation, Message
from aether.ports.repositories import CitationRepositoryPort, MessageRepositoryPort


@dataclass(frozen=True, slots=True)
class ListMessagesCommand:
    workspace_id: UUID
    thread_id: UUID
    after_seq: int | None
    limit: int


@dataclass(frozen=True, slots=True)
class MessageWithCitations:
    """A page item combining one message with its citations (issue #61's
    frontend needs both without a second round trip) — ``citations`` is
    always ``[]`` for a non-grounded message, never fetched for one
    (grounded is the only role citations exist under, ADR-8.6)."""

    message: Message
    citations: list[Citation]


class ListMessages:
    def __init__(
        self, *, messages: MessageRepositoryPort, citations: CitationRepositoryPort
    ) -> None:
        self._messages = messages
        self._citations = citations

    async def execute(self, command: ListMessagesCommand) -> list[MessageWithCitations]:
        page = await self._messages.list_by_thread(
            command.workspace_id,
            command.thread_id,
            after_seq=command.after_seq,
            limit=command.limit,
        )
        grounded_ids = [m.id for m in page if m.grounded]
        all_citations = await self._citations.list_by_messages(command.workspace_id, grounded_ids)
        by_message: dict[UUID, list[Citation]] = {}
        for citation in all_citations:
            by_message.setdefault(citation.message_id, []).append(citation)
        return [MessageWithCitations(message=m, citations=by_message.get(m.id, [])) for m in page]
