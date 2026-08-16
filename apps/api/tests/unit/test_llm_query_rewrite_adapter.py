from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aether.adapters.llm.query_rewrite import LlmQueryRewriteAdapter
from aether.domain.entities import Message, MessageRole, MessageStatus
from aether.ports.llm import CompletionRequest, ProviderChunk, ProviderUsage

pytestmark = pytest.mark.unit


class _FakeProvider:
    def __init__(self, *, reply: str) -> None:
        self._reply = reply
        self.calls: list[CompletionRequest] = []

    async def stream_completion(self, request: CompletionRequest) -> AsyncIterator[ProviderChunk]:
        self.calls.append(request)
        for word in self._reply.split(" "):
            yield f"{word} "
        yield ProviderUsage(prompt_tokens=10, completion_tokens=5)


def _message(*, role: MessageRole, content: str) -> Message:
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


async def test_rewrite_returns_the_providers_streamed_reply_stripped() -> None:
    provider = _FakeProvider(reply="What is Acme Corp's pricing?")
    adapter = LlmQueryRewriteAdapter(provider=provider, model="gpt-4o-mini")

    result = await adapter.rewrite(
        history=[_message(role=MessageRole.ASSISTANT, content="Acme Corp is a widget maker.")],
        raw_query="what about its pricing?",
    )

    assert result == "What is Acme Corp's pricing?"


async def test_history_is_threaded_into_the_completion_request_in_order() -> None:
    provider = _FakeProvider(reply="condensed")
    adapter = LlmQueryRewriteAdapter(provider=provider, model="gpt-4o-mini")
    history = [
        _message(role=MessageRole.USER, content="turn one"),
        _message(role=MessageRole.ASSISTANT, content="reply one"),
    ]

    await adapter.rewrite(history=history, raw_query="follow-up")

    request = provider.calls[0]
    assert request.model == "gpt-4o-mini"
    roles_and_content = [(m.role.value, m.content) for m in request.messages]
    assert roles_and_content[-3:] == [
        ("user", "turn one"),
        ("assistant", "reply one"),
        ("user", "follow-up"),
    ]


async def test_an_empty_reply_falls_back_to_the_raw_query() -> None:
    provider = _FakeProvider(reply=" ")
    adapter = LlmQueryRewriteAdapter(provider=provider, model="gpt-4o-mini")

    result = await adapter.rewrite(history=[], raw_query="raw query")

    assert result == "raw query"
