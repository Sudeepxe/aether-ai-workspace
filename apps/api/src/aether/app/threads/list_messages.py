"""ListMessages use case (§4.3: GET .../threads/{thread}/messages,
cursor-paginated on seq DESC — ADR-8.2's seq is itself a natural, gapless
pagination cursor)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Citation, Feedback, Message, MessageRole
from aether.ports.repositories import (
    CitationRepositoryPort,
    FeedbackRepositoryPort,
    MessageRepositoryPort,
)


@dataclass(frozen=True, slots=True)
class ListMessagesCommand:
    workspace_id: UUID
    thread_id: UUID
    caller_user_id: UUID
    after_seq: int | None
    limit: int


@dataclass(frozen=True, slots=True)
class MessageWithCitations:
    """A page item combining one message with its citations (issue #61's
    frontend needs both without a second round trip) and the caller's
    own feedback (issue #83, same no-second-round-trip motive) —
    ``citations`` is always ``[]`` for a non-grounded message, never
    fetched for one (grounded is the only role citations exist under,
    ADR-8.6); ``feedback`` is always ``None`` for a non-assistant
    message, since only assistant turns are feedback-eligible."""

    message: Message
    citations: list[Citation]
    feedback: Feedback | None


class ListMessages:
    def __init__(
        self,
        *,
        messages: MessageRepositoryPort,
        citations: CitationRepositoryPort,
        feedback: FeedbackRepositoryPort,
    ) -> None:
        self._messages = messages
        self._citations = citations
        self._feedback = feedback

    async def execute(self, command: ListMessagesCommand) -> list[MessageWithCitations]:
        page = await self._messages.list_by_thread(
            command.workspace_id,
            command.thread_id,
            after_seq=command.after_seq,
            limit=command.limit,
        )
        grounded_ids = [m.id for m in page if m.grounded]
        all_citations = await self._citations.list_by_messages(command.workspace_id, grounded_ids)
        by_message_citations: dict[UUID, list[Citation]] = {}
        for citation in all_citations:
            by_message_citations.setdefault(citation.message_id, []).append(citation)

        assistant_ids = [m.id for m in page if m.role == MessageRole.ASSISTANT]
        all_feedback = await self._feedback.list_by_messages_for_user(
            command.workspace_id, assistant_ids, command.caller_user_id
        )
        by_message_feedback: dict[UUID, Feedback] = {f.message_id: f for f in all_feedback}

        return [
            MessageWithCitations(
                message=m,
                citations=by_message_citations.get(m.id, []),
                feedback=by_message_feedback.get(m.id),
            )
            for m in page
        ]
