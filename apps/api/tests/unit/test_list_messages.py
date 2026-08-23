from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.threads.list_messages import ListMessages, ListMessagesCommand
from aether.domain.entities import CitationDraft, MessageRole, MessageStatus
from tests.unit.fakes.chat import FakeCitationRepository, FakeMessageStore

pytestmark = pytest.mark.unit


async def test_a_grounded_messages_citations_are_attached_to_its_page_item() -> None:
    messages = FakeMessageStore()
    citations = FakeCitationRepository()
    workspace_id, thread_id = uuid4(), uuid4()

    await messages.create(
        id=uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        role=MessageRole.USER,
        content="hi",
        status=MessageStatus.COMPLETE,
        client_message_id="cmid-1",
    )
    assistant = await messages.create(
        id=uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        role=MessageRole.ASSISTANT,
        content="Acme costs $10/mo.",
        status=MessageStatus.COMPLETE,
        client_message_id=None,
        grounded=True,
    )
    await citations.create_many(
        workspace_id,
        assistant.id,
        [
            CitationDraft(
                chunk_id=uuid4(),
                document_title="pricing.md",
                section_path="Pricing",
                page_start=1,
                page_end=1,
            )
        ],
    )

    use_case = ListMessages(messages=messages, citations=citations)
    page = await use_case.execute(
        ListMessagesCommand(
            workspace_id=workspace_id, thread_id=thread_id, after_seq=None, limit=10
        )
    )

    user_item = next(item for item in page if item.message.role == MessageRole.USER)
    assistant_item = next(item for item in page if item.message.role == MessageRole.ASSISTANT)
    assert user_item.citations == []
    assert len(assistant_item.citations) == 1
    assert assistant_item.citations[0].document_title == "pricing.md"


async def test_a_refusal_messages_citations_are_empty_and_never_looked_up() -> None:
    messages = FakeMessageStore()
    citations = FakeCitationRepository()
    workspace_id, thread_id = uuid4(), uuid4()

    await messages.create(
        id=uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        role=MessageRole.ASSISTANT,
        content="I don't have information about that in the knowledge base.",
        status=MessageStatus.COMPLETE,
        client_message_id=None,
        grounded=False,
    )

    use_case = ListMessages(messages=messages, citations=citations)
    page = await use_case.execute(
        ListMessagesCommand(
            workspace_id=workspace_id, thread_id=thread_id, after_seq=None, limit=10
        )
    )

    assert page[0].citations == []
