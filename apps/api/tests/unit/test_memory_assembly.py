from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aether.app.chat.memory_assembly import MemoryAssembler
from aether.domain.entities import Message, MessageRole, MessageStatus
from tests.unit.fakes.auth import FakeIdGenerator
from tests.unit.fakes.chat import FakeMessageStore
from tests.unit.fakes.memory import FakeMemoryCompactor, FakeMemorySummaryStore

pytestmark = pytest.mark.unit


def _message(*, seq: int, role: MessageRole, content: str) -> Message:
    return Message(
        id=uuid4(),
        workspace_id=uuid4(),
        thread_id=uuid4(),
        seq=seq,
        role=role,
        content=content,
        status=MessageStatus.COMPLETE,
        client_message_id=None,
        model=None,
        prompt_tokens=None,
        completion_tokens=None,
        cost_microcents=None,
        grounded=False,
        created_at=datetime.now(UTC),
    )


async def _seed(store: FakeMessageStore, workspace_id, thread_id, count: int, *, words: int = 3):
    for i in range(count):
        await store.create(
            id=uuid4(),
            workspace_id=workspace_id,
            thread_id=thread_id,
            role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
            content=" ".join(f"word{i}-{j}" for j in range(words)),
            status=MessageStatus.COMPLETE,
            client_message_id=f"cmid-{i}" if i % 2 == 0 else None,
        )


def _assembler(*, compactor=None, summaries=None) -> tuple[MemoryAssembler, FakeMessageStore]:
    messages = FakeMessageStore()
    assembler = MemoryAssembler(
        messages=messages,
        summaries=summaries or FakeMemorySummaryStore(),
        compactor=compactor or FakeMemoryCompactor(),
        compactor_model_label="fake-compactor",
        ids=FakeIdGenerator(),
    )
    return assembler, messages


async def test_a_short_thread_needs_no_compaction() -> None:
    assembler, messages = _assembler()
    workspace_id, thread_id = uuid4(), uuid4()
    await _seed(messages, workspace_id, thread_id, count=4)

    context = await assembler.assemble(workspace_id, thread_id)

    assert context.summary is None
    assert len(context.window) == 4


async def test_a_long_thread_triggers_compaction_and_keeps_a_budgeted_window() -> None:
    compactor = FakeMemoryCompactor(summary="condensed history")
    summaries = FakeMemorySummaryStore()
    assembler, messages = _assembler(compactor=compactor, summaries=summaries)
    workspace_id, thread_id = uuid4(), uuid4()
    # Each message is ~300 words (well over 1200 tokens' worth across a
    # handful) so this thread genuinely overflows the window budget.
    await _seed(messages, workspace_id, thread_id, count=8, words=300)

    context = await assembler.assemble(workspace_id, thread_id)

    assert context.summary == "condensed history"
    assert len(context.window) < 8, "the whole thread must not fit in the window"
    assert compactor.calls, "the compactor must have been invoked"
    persisted = await summaries.get_by_thread(workspace_id, thread_id)
    assert persisted is not None
    assert persisted.content == "condensed history"
    assert persisted.model == "fake-compactor"


async def test_the_window_always_keeps_at_least_the_newest_message() -> None:
    assembler, messages = _assembler()
    workspace_id, thread_id = uuid4(), uuid4()
    await _seed(messages, workspace_id, thread_id, count=1, words=5000)

    context = await assembler.assemble(workspace_id, thread_id)

    assert len(context.window) == 1


async def test_only_messages_after_the_existing_summarys_upto_seq_are_considered() -> None:
    summaries = FakeMemorySummaryStore()
    workspace_id, thread_id = uuid4(), uuid4()
    await summaries.upsert(
        id=uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        upto_seq=2,
        content="old summary",
        model="fake-compactor",
        token_count=10,
    )
    assembler, messages = _assembler(summaries=summaries)
    await _seed(messages, workspace_id, thread_id, count=4)  # seqs 1..4

    context = await assembler.assemble(workspace_id, thread_id)

    assert context.summary == "old summary"
    assert all(m.seq > 2 for m in context.window)
    assert len(context.window) == 2


async def test_a_compaction_failure_falls_back_to_window_only_without_crashing() -> None:
    compactor = FakeMemoryCompactor(error=RuntimeError("provider down"))
    summaries = FakeMemorySummaryStore()
    assembler, messages = _assembler(compactor=compactor, summaries=summaries)
    workspace_id, thread_id = uuid4(), uuid4()
    await _seed(messages, workspace_id, thread_id, count=8, words=300)

    context = await assembler.assemble(workspace_id, thread_id)

    assert context.summary is None  # no previous summary, compaction failed
    assert context.window  # the turn still proceeds with whatever fit
    assert await summaries.get_by_thread(workspace_id, thread_id) is None


async def test_a_compaction_failure_leaves_the_previous_summary_unchanged() -> None:
    compactor = FakeMemoryCompactor(error=RuntimeError("provider down"))
    summaries = FakeMemorySummaryStore()
    workspace_id, thread_id = uuid4(), uuid4()
    await summaries.upsert(
        id=uuid4(),
        workspace_id=workspace_id,
        thread_id=thread_id,
        upto_seq=1,
        content="untouched summary",
        model="fake-compactor",
        token_count=5,
    )
    assembler, messages = _assembler(compactor=compactor, summaries=summaries)
    await _seed(messages, workspace_id, thread_id, count=8, words=300)

    context = await assembler.assemble(workspace_id, thread_id)

    assert context.summary == "untouched summary"
    persisted = await summaries.get_by_thread(workspace_id, thread_id)
    assert persisted is not None
    assert persisted.content == "untouched summary"
    assert persisted.upto_seq == 1
