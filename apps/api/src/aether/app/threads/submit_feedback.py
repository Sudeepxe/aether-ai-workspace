"""SubmitFeedback use case (FR-CH-6, §4.3: POST .../messages/{msg}/feedback).

Only an assistant's own turns are feedback-eligible — feedback on a
user's own message is meaningless (there is no "the model said this
wrong" claim to make about the caller's own words).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Feedback, FeedbackRating, MessageRole
from aether.domain.errors import FeedbackNotEligibleError, MessageNotFoundError
from aether.ports.repositories import FeedbackRepositoryPort, MessageRepositoryPort
from aether.ports.security import IdPort


@dataclass(frozen=True, slots=True)
class SubmitFeedbackCommand:
    workspace_id: UUID
    thread_id: UUID
    message_id: UUID
    user_id: UUID
    rating: FeedbackRating
    reason: str | None


class SubmitFeedback:
    def __init__(
        self, *, messages: MessageRepositoryPort, feedback: FeedbackRepositoryPort, ids: IdPort
    ) -> None:
        self._messages = messages
        self._feedback = feedback
        self._ids = ids

    async def execute(self, command: SubmitFeedbackCommand) -> Feedback:
        message = await self._messages.get_by_id(command.workspace_id, command.message_id)
        if message is None or message.thread_id != command.thread_id:
            # A message that exists but under a different thread is
            # indistinguishable from "doesn't exist" at this URL — the
            # route is nested under thread_id, so that's the resource
            # the caller is really addressing.
            raise MessageNotFoundError(str(command.message_id))
        if message.role != MessageRole.ASSISTANT:
            raise FeedbackNotEligibleError(str(command.message_id))
        return await self._feedback.upsert(
            id=self._ids.new_id(),
            workspace_id=command.workspace_id,
            message_id=command.message_id,
            user_id=command.user_id,
            rating=command.rating,
            reason=command.reason,
        )
