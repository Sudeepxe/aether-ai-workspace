from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.chat.send_message import SendMessage, SendMessageCommand
from aether.domain.entities import MessageRole, MessageStatus
from aether.domain.errors import BudgetExhaustedError
from aether.domain.streaming import (
    DoneStreamEvent,
    ErrorStreamEvent,
    GenerationStatus,
    MetaStreamEvent,
    TokenStreamEvent,
    UsageStreamEvent,
)
from aether.ports.llm import ProviderError
from tests.unit.fakes.auth import FakeIdGenerator
from tests.unit.fakes.chat import (
    FakeBudgetAdmission,
    FakeCancellation,
    FakeGenerator,
    FakeMessageStore,
    FakeStreamBuffer,
    FakeUsageLedger,
)

pytestmark = pytest.mark.unit

_MAX_TOKENS = 1024
_CEILING_COST_PER_1K_MICROCENTS = 60_000


def _orchestrator(
    *,
    deltas: list[str],
    messages: FakeMessageStore | None = None,
    cancellation: FakeCancellation | None = None,
    generator: FakeGenerator | None = None,
    admission: FakeBudgetAdmission | None = None,
    usage_ledger: FakeUsageLedger | None = None,
) -> tuple[SendMessage, FakeMessageStore, FakeStreamBuffer, FakeCancellation, FakeUsageLedger]:
    messages = messages or FakeMessageStore()
    buffer = FakeStreamBuffer()
    cancellation = cancellation or FakeCancellation()
    admission = admission or FakeBudgetAdmission()
    usage_ledger = usage_ledger or FakeUsageLedger()
    orchestrator = SendMessage(
        messages=messages,
        generator=generator or FakeGenerator(deltas=deltas),
        buffer=buffer,
        cancellation=cancellation,
        admission=admission,
        usage_ledger=usage_ledger,
        ids=FakeIdGenerator(),
        max_tokens=_MAX_TOKENS,
        ceiling_cost_per_1k_microcents=_CEILING_COST_PER_1K_MICROCENTS,
    )
    return orchestrator, messages, buffer, cancellation, usage_ledger


async def test_full_turn_persists_user_and_assistant_messages_and_follows_the_grammar() -> None:
    orchestrator, messages, buffer, _, usage_ledger = _orchestrator(deltas=["Hello", ", ", "world"])
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
    # Real usage from the generator's final GenerationUsage, not a
    # word-count estimate — the orchestrator no longer computes this itself.
    assert assistant_msg.model == "fake-model"
    assert assistant_msg.cost_microcents == 100
    usage_event = events[4]
    assert isinstance(usage_event, UsageStreamEvent)
    assert usage_event.cost_microcents == 100

    # Every event was also buffered — the resume/replay path's data source.
    buffered = buffer.events[(workspace_id, events[0].generation_id)]
    assert len(buffered) == len(events)

    # Positive real cost settles into the usage ledger, same request.
    assert len(usage_ledger.recorded) == 1
    assert usage_ledger.recorded[0].cost_microcents == 100
    assert usage_ledger.recorded[0].model == "fake-model"


async def test_retried_post_with_same_client_message_id_replays_without_regenerating() -> None:
    orchestrator, messages, _, _, _ = _orchestrator(deltas=["first", " ", "reply"])
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
    orchestrator, messages, _, _, _ = _orchestrator(
        deltas=["a", "b", "c"], cancellation=cancellation
    )
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
    orchestrator, messages, _, _, _ = _orchestrator(
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


async def test_workspace_budget_exhausted_refuses_before_persisting_or_generating() -> None:
    admission = FakeBudgetAdmission(allow_workspace=False)
    orchestrator, messages, buffer, _, usage_ledger = _orchestrator(
        deltas=["should", "never", "run"], admission=admission
    )
    workspace_id, thread_id = uuid4(), uuid4()

    with pytest.raises(BudgetExhaustedError):
        async for _ in orchestrator.execute(
            SendMessageCommand(
                workspace_id=workspace_id,
                thread_id=thread_id,
                content="hi",
                client_message_id="cmid-budget-1",
            )
        ):
            pass

    assert messages._rows == {}
    assert usage_ledger.recorded == []
    assert buffer.events == {}
    assert admission.checked_workspaces == [workspace_id]


async def test_global_budget_exhausted_refuses_before_workspace_check() -> None:
    admission = FakeBudgetAdmission(allow_global=False)
    orchestrator, messages, _, _, _ = _orchestrator(deltas=["a"], admission=admission)
    workspace_id, thread_id = uuid4(), uuid4()

    with pytest.raises(BudgetExhaustedError):
        async for _ in orchestrator.execute(
            SendMessageCommand(
                workspace_id=workspace_id,
                thread_id=thread_id,
                content="hi",
                client_message_id="cmid-budget-2",
            )
        ):
            pass

    assert messages._rows == {}
    # The cheaper, workspace-independent check runs first — no point
    # reading one workspace's budget row when the whole system is capped.
    assert admission.checked_workspaces == []


async def test_provider_error_before_any_token_persists_nothing_and_emits_error_event() -> None:
    generator = FakeGenerator(deltas=["never", "sent"], error=ProviderError("down", retryable=True))
    orchestrator, messages, _, _, usage_ledger = _orchestrator(deltas=[], generator=generator)
    workspace_id, thread_id = uuid4(), uuid4()

    events = [
        e
        async for e in orchestrator.execute(
            SendMessageCommand(
                workspace_id=workspace_id,
                thread_id=thread_id,
                content="hi",
                client_message_id="cmid-err-1",
            )
        )
    ]

    error_events = [e for e in events if isinstance(e, ErrorStreamEvent)]
    assert len(error_events) == 1
    assert error_events[0].code == "provider_error"
    last = events[-1]
    assert isinstance(last, DoneStreamEvent)
    assert last.status == GenerationStatus.ERROR
    assert not any(m.role == MessageRole.ASSISTANT for m in messages._rows.values())
    assert usage_ledger.recorded == []


async def test_provider_error_mid_stream_persists_partial_content() -> None:
    generator = FakeGenerator(
        deltas=["one", "two", "three"],
        error=ProviderError("connection reset", retryable=True),
        fail_after=2,
    )
    orchestrator, messages, _, _, usage_ledger = _orchestrator(deltas=[], generator=generator)
    workspace_id, thread_id = uuid4(), uuid4()

    events = [
        e
        async for e in orchestrator.execute(
            SendMessageCommand(
                workspace_id=workspace_id,
                thread_id=thread_id,
                content="hi",
                client_message_id="cmid-err-2",
            )
        )
    ]

    error_events = [e for e in events if isinstance(e, ErrorStreamEvent)]
    assert len(error_events) == 1
    last = events[-1]
    assert isinstance(last, DoneStreamEvent)
    assert last.status == GenerationStatus.PARTIAL
    assistant_msg = next(m for m in messages._rows.values() if m.role == MessageRole.ASSISTANT)
    assert assistant_msg.content == "onetwo"
    assert assistant_msg.status == MessageStatus.PARTIAL
    # No authoritative GenerationUsage ever arrived — nothing to settle.
    assert usage_ledger.recorded == []
