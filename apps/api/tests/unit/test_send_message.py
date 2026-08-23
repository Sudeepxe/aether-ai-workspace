from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.chat.send_message import SendMessage, SendMessageCommand
from aether.app.retrieval.hybrid_search import HybridSearch
from aether.app.retrieval.query_rewrite import QueryRewriter
from aether.app.retrieval.refusal_gate import RetrievalGate
from aether.domain.entities import MessageRole, MessageStatus
from aether.domain.errors import BudgetExhaustedError
from aether.domain.streaming import (
    CitationStreamEvent,
    DoneStreamEvent,
    ErrorStreamEvent,
    GenerationStatus,
    MetaStreamEvent,
    TokenStreamEvent,
    UsageStreamEvent,
)
from aether.ports.chat import NOT_IN_KNOWLEDGE_BASE_REPLY
from aether.ports.llm import ProviderError
from aether.ports.retrieval import ChunkSearchResult
from tests.unit.fakes.auth import FakeIdGenerator
from tests.unit.fakes.chat import (
    FakeBudgetAdmission,
    FakeCancellation,
    FakeGenerator,
    FakeMessageStore,
    FakeStreamBuffer,
    FakeUsageLedger,
)
from tests.unit.fakes.query_rewrite import FakeQueryRewriter
from tests.unit.fakes.retrieval import FakeChunkSearch, FakeQueryEmbedder

pytestmark = pytest.mark.unit

_MAX_TOKENS = 1024
_CEILING_COST_PER_1K_MICROCENTS = 60_000
_DEFAULT_THRESHOLD = 0.01


def _matching_chunk_search() -> FakeChunkSearch:
    """A single vector hit at rank 1 — RRF's sum for it (1/(60+1) ≈
    0.0164) clears the default 0.01 threshold, so Gate 1 passes by
    default and existing tests exercise the same generation path they
    did pre-#60. Tests that want a refusal pass ``chunk_search=FakeChunkSearch()``
    (zero results) explicitly."""
    return FakeChunkSearch(
        vector_results=[
            ChunkSearchResult(
                chunk_id=uuid4(),
                document_id=uuid4(),
                document_title="Acme Handbook",
                section_path="Pricing",
                page_start=1,
                page_end=1,
                content="Acme costs $10/mo.",
                embedding=[0.1, 0.2, 0.3, 0.4],
                score=0.9,
            )
        ]
    )


def _orchestrator(
    *,
    deltas: list[str],
    messages: FakeMessageStore | None = None,
    cancellation: FakeCancellation | None = None,
    generator: FakeGenerator | None = None,
    admission: FakeBudgetAdmission | None = None,
    usage_ledger: FakeUsageLedger | None = None,
    chunk_search: FakeChunkSearch | None = None,
    threshold: float = _DEFAULT_THRESHOLD,
) -> tuple[
    SendMessage,
    FakeMessageStore,
    FakeStreamBuffer,
    FakeCancellation,
    FakeUsageLedger,
    FakeGenerator,
]:
    messages = messages or FakeMessageStore()
    buffer = FakeStreamBuffer()
    cancellation = cancellation or FakeCancellation()
    admission = admission or FakeBudgetAdmission()
    usage_ledger = usage_ledger or FakeUsageLedger()
    fake_generator = generator or FakeGenerator(deltas=deltas)
    hybrid_search = HybridSearch(
        chunk_search=chunk_search if chunk_search is not None else _matching_chunk_search(),
        embedder=FakeQueryEmbedder(),
    )
    query_rewriter = QueryRewriter(rewriter=FakeQueryRewriter())
    retrieval_gate = RetrievalGate(threshold=threshold)
    orchestrator = SendMessage(
        messages=messages,
        generator=fake_generator,
        hybrid_search=hybrid_search,
        query_rewriter=query_rewriter,
        retrieval_gate=retrieval_gate,
        buffer=buffer,
        cancellation=cancellation,
        admission=admission,
        usage_ledger=usage_ledger,
        ids=FakeIdGenerator(),
        max_tokens=_MAX_TOKENS,
        ceiling_cost_per_1k_microcents=_CEILING_COST_PER_1K_MICROCENTS,
    )
    return orchestrator, messages, buffer, cancellation, usage_ledger, fake_generator


async def test_full_turn_persists_user_and_assistant_messages_and_follows_the_grammar() -> None:
    orchestrator, messages, buffer, _, usage_ledger, generator = _orchestrator(
        deltas=["Hello", ", ", "world"]
    )
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

    # meta -> token* -> citation* -> usage -> done, per §4.4's grammar —
    # this turn clears Gate 1 (see _matching_chunk_search), so it's
    # grounded with exactly one citation (one retrieved chunk).
    assert isinstance(events[0], MetaStreamEvent)
    assert events[0].grounded is True
    assert [type(e) for e in events[1:4]] == [TokenStreamEvent] * 3
    assert isinstance(events[4], CitationStreamEvent)
    assert events[4].document_title == "Acme Handbook"
    assert events[4].section_path == "Pricing"
    assert isinstance(events[5], UsageStreamEvent)
    assert isinstance(events[6], DoneStreamEvent)
    assert events[6].status == GenerationStatus.COMPLETE

    # seq is strictly monotonic across the whole stream, one shared counter.
    assert [e.seq for e in events] == list(range(len(events)))
    assert all(e.generation_id == events[0].generation_id for e in events)

    # The generator was called with the retrieved context (Gate 2's protocol).
    assert len(generator.contexts_seen) == 1
    assert generator.contexts_seen[0] is not None
    assert generator.contexts_seen[0].chunks[0].document_title == "Acme Handbook"

    stored = list(messages._rows.values())
    assert len(stored) == 2
    user_msg = next(m for m in stored if m.role == MessageRole.USER)
    assistant_msg = next(m for m in stored if m.role == MessageRole.ASSISTANT)
    assert user_msg.content == "hi there"
    assert user_msg.status == MessageStatus.COMPLETE
    assert assistant_msg.content == "Hello, world"
    assert assistant_msg.status == MessageStatus.COMPLETE
    assert assistant_msg.grounded is True
    assert assistant_msg.seq == user_msg.seq + 1
    # Real usage from the generator's final GenerationUsage, not a
    # word-count estimate — the orchestrator no longer computes this itself.
    assert assistant_msg.model == "fake-model"
    assert assistant_msg.cost_microcents == 100
    usage_event = events[5]
    assert isinstance(usage_event, UsageStreamEvent)
    assert usage_event.cost_microcents == 100

    # Every event was also buffered — the resume/replay path's data source.
    buffered = buffer.events[(workspace_id, events[0].generation_id)]
    assert len(buffered) == len(events)

    # Positive real cost settles into the usage ledger, same request.
    assert len(usage_ledger.recorded) == 1
    assert usage_ledger.recorded[0].cost_microcents == 100
    assert usage_ledger.recorded[0].model == "fake-model"


async def test_gate_1_refusal_short_circuits_before_any_generator_call() -> None:
    """The literal issue #60 acceptance scenario: an out-of-KB query
    (zero retrieved chunks) produces grounded=False, zero generator
    calls, zero citations, a clear refusal message."""
    generator = FakeGenerator(deltas=["should", "never", "run"])
    orchestrator, messages, _, _, usage_ledger, _ = _orchestrator(
        deltas=[], generator=generator, chunk_search=FakeChunkSearch()
    )
    workspace_id, thread_id = uuid4(), uuid4()

    events = [
        e
        async for e in orchestrator.execute(
            SendMessageCommand(
                workspace_id=workspace_id,
                thread_id=thread_id,
                content="what's the weather on Mars?",
                client_message_id="cmid-refusal",
            )
        )
    ]

    assert [type(e) for e in events] == [
        MetaStreamEvent,
        TokenStreamEvent,
        UsageStreamEvent,
        DoneStreamEvent,
    ]
    meta, token, usage, done = events
    assert isinstance(meta, MetaStreamEvent)
    assert meta.grounded is False
    assert isinstance(token, TokenStreamEvent)
    assert token.delta == NOT_IN_KNOWLEDGE_BASE_REPLY
    assert isinstance(usage, UsageStreamEvent)
    assert usage.cost_microcents == 0
    assert isinstance(done, DoneStreamEvent)
    assert done.status == GenerationStatus.COMPLETE
    assert [e.seq for e in events] == list(range(len(events)))

    # No generator call at all — Gate 1's whole point.
    assert generator.contexts_seen == []

    assistant_msg = next(m for m in messages._rows.values() if m.role == MessageRole.ASSISTANT)
    assert assistant_msg.content == NOT_IN_KNOWLEDGE_BASE_REPLY
    assert assistant_msg.grounded is False
    assert assistant_msg.status == MessageStatus.COMPLETE
    assert assistant_msg.cost_microcents == 0
    assert usage_ledger.recorded == []
    assert messages._citations == {}


async def test_gate_1_refusal_below_threshold_even_with_chunks_present() -> None:
    """A weak match (chunks exist but the top RRF score doesn't clear
    the threshold) is still a refusal, not just a literal empty result."""
    orchestrator, _, _, _, _, generator = _orchestrator(
        deltas=[], chunk_search=_matching_chunk_search(), threshold=1.0
    )
    workspace_id, thread_id = uuid4(), uuid4()

    events = [
        e
        async for e in orchestrator.execute(
            SendMessageCommand(
                workspace_id=workspace_id,
                thread_id=thread_id,
                content="weak match",
                client_message_id="cmid-weak",
            )
        )
    ]

    assert isinstance(events[0], MetaStreamEvent)
    assert events[0].grounded is False
    assert generator.contexts_seen == []


async def test_retried_post_with_same_client_message_id_replays_without_regenerating() -> None:
    orchestrator, messages, _, _, _, _ = _orchestrator(deltas=["first", " ", "reply"])
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
    assert isinstance(second_events[0], MetaStreamEvent)
    assert second_events[0].grounded is True  # replays the original grounded state
    assert isinstance(second_events[1], TokenStreamEvent)
    assert second_events[1].delta == "first reply"
    # The original grounded turn's citation is replayed too.
    assert any(isinstance(e, CitationStreamEvent) for e in second_events)
    last = second_events[-1]
    assert isinstance(last, DoneStreamEvent)
    assert last.status == GenerationStatus.COMPLETE
    # Replay uses a fresh generation_id (a new SSE envelope), not the
    # original one — it's a new response to a new request, just with
    # deterministic content.
    assert second_events[0].generation_id != first_events[0].generation_id


async def test_cancellation_before_any_token_persists_no_assistant_message() -> None:
    cancellation = FakeCancellation()
    orchestrator, messages, _, _, _, _ = _orchestrator(
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
    orchestrator, messages, _, _, _, _ = _orchestrator(
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
    orchestrator, messages, buffer, _, usage_ledger, _ = _orchestrator(
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
    orchestrator, messages, _, _, _, _ = _orchestrator(deltas=["a"], admission=admission)
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
    orchestrator, messages, _, _, usage_ledger, _ = _orchestrator(deltas=[], generator=generator)
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
    orchestrator, messages, _, _, usage_ledger, _ = _orchestrator(deltas=[], generator=generator)
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
