from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aether.app.retrieval.query_rewrite import QueryRewriter
from aether.domain.entities import Message, MessageRole, MessageStatus
from tests.unit.fakes.query_rewrite import FakeQueryRewriter

pytestmark = pytest.mark.unit


def _message(*, role: MessageRole = MessageRole.USER, content: str = "hi") -> Message:
    return Message(
        id=uuid4(),
        workspace_id=uuid4(),
        thread_id=uuid4(),
        seq=1,
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


async def test_first_turn_skips_the_rewrite_entirely() -> None:
    rewriter = FakeQueryRewriter(rewritten="should never be used")
    query_rewriter = QueryRewriter(rewriter=rewriter)

    result = await query_rewriter.build_dual_feed_query(history=[], raw_query="what is Acme?")

    assert result.vector_query == "what is Acme?"
    assert result.lexical_queries == ["what is Acme?"]
    assert rewriter.calls == []  # no rewrite call made at all


async def test_dual_feed_the_lexical_leg_gets_both_raw_and_rewritten() -> None:
    """The load-bearing contract (ADR-6.3's own self-review flags the
    alternative as a real bug class): only the vector leg is exclusive
    to the rewritten query."""
    rewriter = FakeQueryRewriter(rewritten="What is Acme Corp's pricing?")
    query_rewriter = QueryRewriter(rewriter=rewriter)
    history = [_message(role=MessageRole.ASSISTANT, content="Acme Corp is a widget maker.")]

    result = await query_rewriter.build_dual_feed_query(
        history=history, raw_query="what about its pricing?"
    )

    assert result.vector_query == "What is Acme Corp's pricing?"
    assert result.lexical_queries == [
        "what about its pricing?",
        "What is Acme Corp's pricing?",
    ]


async def test_a_rewrite_identical_to_the_raw_query_does_not_duplicate_the_lexical_leg() -> None:
    rewriter = FakeQueryRewriter(rewritten="same query")
    query_rewriter = QueryRewriter(rewriter=rewriter)
    history = [_message()]

    result = await query_rewriter.build_dual_feed_query(history=history, raw_query="same query")

    assert result.lexical_queries == ["same query"]


async def test_a_timeout_past_the_150ms_budget_falls_back_to_the_raw_query() -> None:
    rewriter = FakeQueryRewriter(rewritten="too slow to matter", delay_seconds=1.0)
    query_rewriter = QueryRewriter(rewriter=rewriter)
    history = [_message()]

    result = await query_rewriter.build_dual_feed_query(history=history, raw_query="raw query")

    assert result.vector_query == "raw query"
    assert result.lexical_queries == ["raw query"]


async def test_a_failed_rewrite_call_falls_back_to_the_raw_query_not_a_crash() -> None:
    rewriter = FakeQueryRewriter(error=ConnectionError("provider unreachable"))
    query_rewriter = QueryRewriter(rewriter=rewriter)
    history = [_message()]

    result = await query_rewriter.build_dual_feed_query(history=history, raw_query="raw query")

    assert result.vector_query == "raw query"
    assert result.lexical_queries == ["raw query"]


async def test_history_and_raw_query_are_passed_through_to_the_rewriter() -> None:
    rewriter = FakeQueryRewriter(rewritten="condensed")
    query_rewriter = QueryRewriter(rewriter=rewriter)
    history = [_message(content="turn one")]

    await query_rewriter.build_dual_feed_query(history=history, raw_query="follow-up")

    assert rewriter.calls == [(history, "follow-up")]
