from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.chat.send_message import SendMessage, SendMessageCommand
from aether.domain.entities import MessageRole, MessageStatus
from aether.domain.streaming import (
    DoneStreamEvent,
    GenerationStatus,
    MetaStreamEvent,
    TokenStreamEvent,
    UsageStreamEvent,
)
from tests.unit.fakes.auth import FakeIdGenerator
from tests.unit.fakes.chat import (
    FakeCancellation,
    FakeGenerator,
    FakeMessageStore,
    FakeStreamBuffer,
)

pytestmark = pytest.mark.unit


def _orchestrator(
    *,
    deltas: list[str],
    messages: FakeMessageStore | None = None,
    cancellation: FakeCancellation | None = None,
) -> tuple[SendMessage, FakeMessageStore, FakeStreamBuffer, FakeCancellation]:
    messages = messages or FakeMessageStore()
    buffer = FakeStreamBuffer()
    cancellation = cancellation or FakeCancellation()
    orchestrator = SendMessage(
        messages=messages,
        generator=FakeGenerator(deltas=deltas),
        buffer=buffer,
        cancellation=cancellation,
        ids=FakeIdGenerator(),
    )
    return orchestrator, messages, buffer, cancellation


async def test_full_turn_persists_user_and_assistant_messages_and_follows_the_grammar() -> None:
    orchestrator, messages, buffer, _ = _orchestrator(deltas=["Hello", ", ", "world"])
    workspace_id, thread_id = uuid4(), uuid4()

    events = [
        e
        async for e in orchestrator.execute(
            SendMessageCommand(
                workspace_id=workspace_id,
                thread_id=thread_id,
                content="hi there",
                client_message_id="cmid-1",
            )
        )
    ]

    # meta -> token* -> usage -> done, per §4.4's grammar (citation/banner
    # are zero-occurrences here, a valid subsequence).
    assert isinstance(events[0], MetaStreamEvent)
    assert [type(e) for e in events[1:4]] == [TokenStreamEvent] * 3
    assert isinstance(events[4], UsageStreamEvent)
    assert isinstance(events[5], DoneStreamEvent)
    assert events[5].status == GenerationStatus.COMPLETE

    # seq is strictly monotonic across the whole stream, one shared counter.
    assert [e.seq for e in events] == list(range(len(events)))
    assert all(e.generation_id == events[0].generation_id for e in events)

    stored = list(messages._rows.values())
    assert len(stored) == 2
    user_msg = next(m for m in stored if m.role == MessageRole.USER)
    assistant_msg = next(m for m in stored if m.role == MessageRole.ASSISTANT)
    assert user_msg.content == "hi there"
    assert user_msg.status == MessageStatus.COMPLETE
    assert assistant_msg.content == "Hello, world"
    assert assistant_msg.status == MessageStatus.COMPLETE
    assert assistant_msg.seq == user_msg.seq + 1

    # Every event was also buffered — the resume/replay path's data source.
    buffered = buffer.events[(workspace_id, events[0].generation_id)]
    assert len(buffered) == len(events)


async def test_retried_post_with_same_client_message_id_replays_without_regenerating() -> None:
    orchestrator, messages, _, _ = _orchestrator(deltas=["first", " ", "reply"])
    workspace_id, thread_id = uuid4(), uuid4()
    command = SendMessageCommand(
        workspace_id=workspace_id, thread_id=thread_id, content="hi", client_message_id="cmid-dup"
    )

    first_events = [e async for e in orchestrator.execute(command)]
    stored_after_first = len(messages._rows)

    second_events = [e async for e in orchestrator.execute(command)]

    # No new messages persisted, no new generator invocation needed —
    # ADR-4.6 idempotency.
    assert len(messages._rows) == stored_after_first
    assert isinstance(second_events[1], TokenStreamEvent)
    assert second_events[1].delta == "first reply"
    last = second_events[-1]
    assert isinstance(last, DoneStreamEvent)
    assert last.status == GenerationStatus.COMPLETE
    # Replay uses a fresh generation_id (a new SSE envelope), not the
    # original one — it's a new response to a new request, just with
    # deterministic content.
    assert second_events[0].generation_id != first_events[0].generation_id


async def test_cancellation_before_any_token_persists_no_assistant_message() -> None:
    cancellation = FakeCancellation()
    orchestrator, messages, _, _ = _orchestrator(deltas=["a", "b", "c"], cancellation=cancellation)
    workspace_id, thread_id = uuid4(), uuid4()

    # Mark cancelled before the orchestrator even starts iterating the
    # generator — simulates a cancel that lands before the first token.
    agen = orchestrator.execute(
        SendMessageCommand(
            workspace_id=workspace_id, thread_id=thread_id, content="hi", client_message_id="cmid-2"
        )
    )
    meta = await agen.__anext__()
    cancellation.cancelled_generations.add(meta.generation_id)
    events = [meta] + [e async for e in agen]

    last = events[-1]
    assert isinstance(last, DoneStreamEvent)
    assert last.status == GenerationStatus.CANCELLED
    assert not any(m.role == MessageRole.ASSISTANT for m in messages._rows.values())


async def test_cancellation_mid_stream_persists_partial_content_as_cancelled() -> None:
    cancellation = FakeCancellation()
    orchestrator, messages, _, _ = _orchestrator(
        deltas=["one", "two", "three", "four"], cancellation=cancellation
    )
    workspace_id, thread_id = uuid4(), uuid4()

    agen = orchestrator.execute(
        SendMessageCommand(
            workspace_id=workspace_id, thread_id=thread_id, content="hi", client_message_id="cmid-3"
        )
    )
    meta = await agen.__anext__()
    first_token = await agen.__anext__()
    assert isinstance(first_token, TokenStreamEvent)
    cancellation.cancelled_generations.add(meta.generation_id)  # cancel arrives after one token
    remaining = [e async for e in agen]

    last = remaining[-1]
    assert isinstance(last, DoneStreamEvent)
    assert last.status == GenerationStatus.CANCELLED
    assistant_msg = next(m for m in messages._rows.values() if m.role == MessageRole.ASSISTANT)
    assert assistant_msg.content == "one"
    assert assistant_msg.status == MessageStatus.CANCELLED
