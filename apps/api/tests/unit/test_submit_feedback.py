from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.threads.submit_feedback import SubmitFeedback, SubmitFeedbackCommand
from aether.domain.entities import FeedbackRating, MessageRole, MessageStatus
from aether.domain.errors import FeedbackNotEligibleError, MessageNotFoundError
from tests.unit.fakes.auth import FakeIdGenerator
from tests.unit.fakes.chat import FakeFeedbackRepository, FakeMessageStore

pytestmark = pytest.mark.unit


async def _assistant_message(messages: FakeMessageStore, workspace_id, thread_id):
    return await messages.create(
        id=uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        role=MessageRole.ASSISTANT,
        content="Acme costs $10/mo.",
        status=MessageStatus.COMPLETE,
        client_message_id=None,
    )


async def test_feedback_is_created_for_an_assistant_message() -> None:
    messages = FakeMessageStore()
    feedback = FakeFeedbackRepository()
    workspace_id, thread_id, user_id = uuid4(), uuid4(), uuid4()
    message = await _assistant_message(messages, workspace_id, thread_id)
    use_case = SubmitFeedback(messages=messages, feedback=feedback, ids=FakeIdGenerator())

    result = await use_case.execute(
        SubmitFeedbackCommand(
            workspace_id=workspace_id,
            thread_id=thread_id,
            message_id=message.id,
            user_id=user_id,
            rating=FeedbackRating.UP,
            reason=None,
        )
    )

    assert result.rating == FeedbackRating.UP
    assert result.message_id == message.id
    assert result.user_id == user_id


async def test_a_repeat_submission_upserts_rather_than_duplicates() -> None:
    messages = FakeMessageStore()
    feedback = FakeFeedbackRepository()
    workspace_id, thread_id, user_id = uuid4(), uuid4(), uuid4()
    message = await _assistant_message(messages, workspace_id, thread_id)
    use_case = SubmitFeedback(messages=messages, feedback=feedback, ids=FakeIdGenerator())
    command = SubmitFeedbackCommand(
        workspace_id=workspace_id,
        thread_id=thread_id,
        message_id=message.id,
        user_id=user_id,
        rating=FeedbackRating.UP,
        reason=None,
    )

    first = await use_case.execute(command)
    changed_mind = await use_case.execute(
        SubmitFeedbackCommand(
            workspace_id=workspace_id,
            thread_id=thread_id,
            message_id=message.id,
            user_id=user_id,
            rating=FeedbackRating.DOWN,
            reason="actually wrong",
        )
    )

    stored = await feedback.list_by_messages_for_user(workspace_id, [message.id], user_id)
    assert len(stored) == 1
    assert changed_mind.id == first.id
    assert changed_mind.rating == FeedbackRating.DOWN
    assert changed_mind.reason == "actually wrong"


async def test_feedback_on_a_user_message_is_rejected() -> None:
    messages = FakeMessageStore()
    feedback = FakeFeedbackRepository()
    workspace_id, thread_id, user_id = uuid4(), uuid4(), uuid4()
    user_message = await messages.create(
        id=uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        role=MessageRole.USER,
        content="hi",
        status=MessageStatus.COMPLETE,
        client_message_id="cmid-1",
    )
    use_case = SubmitFeedback(messages=messages, feedback=feedback, ids=FakeIdGenerator())

    with pytest.raises(FeedbackNotEligibleError):
        await use_case.execute(
            SubmitFeedbackCommand(
                workspace_id=workspace_id,
                thread_id=thread_id,
                message_id=user_message.id,
                user_id=user_id,
                rating=FeedbackRating.UP,
                reason=None,
            )
        )


async def test_feedback_on_an_unknown_message_raises_not_found() -> None:
    messages = FakeMessageStore()
    feedback = FakeFeedbackRepository()
    workspace_id, thread_id, user_id = uuid4(), uuid4(), uuid4()
    use_case = SubmitFeedback(messages=messages, feedback=feedback, ids=FakeIdGenerator())

    with pytest.raises(MessageNotFoundError):
        await use_case.execute(
            SubmitFeedbackCommand(
                workspace_id=workspace_id,
                thread_id=thread_id,
                message_id=uuid4(),
                user_id=user_id,
                rating=FeedbackRating.UP,
                reason=None,
            )
        )


async def test_feedback_on_a_message_from_a_different_thread_raises_not_found() -> None:
    messages = FakeMessageStore()
    feedback = FakeFeedbackRepository()
    workspace_id, thread_id, other_thread_id, user_id = uuid4(), uuid4(), uuid4(), uuid4()
    message = await _assistant_message(messages, workspace_id, thread_id)
    use_case = SubmitFeedback(messages=messages, feedback=feedback, ids=FakeIdGenerator())

    with pytest.raises(MessageNotFoundError):
        await use_case.execute(
            SubmitFeedbackCommand(
                workspace_id=workspace_id,
                thread_id=other_thread_id,
                message_id=message.id,
                user_id=user_id,
                rating=FeedbackRating.UP,
                reason=None,
            )
        )
