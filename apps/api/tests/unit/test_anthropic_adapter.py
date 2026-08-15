from __future__ import annotations

import json

import httpx
import pytest

from aether.adapters.anthropic.completion import AnthropicCompletionAdapter
from aether.ports.llm import (
    CompletionRequest,
    LlmMessage,
    LlmMessageRole,
    ProviderError,
    ProviderUsage,
)

pytestmark = pytest.mark.unit

_REQUEST = CompletionRequest(
    messages=[
        LlmMessage(role=LlmMessageRole.SYSTEM, content="You are helpful."),
        LlmMessage(role=LlmMessageRole.USER, content="hi"),
    ],
    model="claude-haiku-4-5",
    max_tokens=100,
)


def _event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}"


def _sse_body(*blocks: str) -> bytes:
    return ("\n\n".join(blocks) + "\n\n").encode()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_stream_completion_yields_text_deltas_then_usage() -> None:
    body = _sse_body(
        _event("message_start", {"message": {"usage": {"input_tokens": 20}}}),
        _event(
            "content_block_delta",
            {"delta": {"type": "text_delta", "text": "Hello"}},
        ),
        _event(
            "content_block_delta",
            {"delta": {"type": "text_delta", "text": " world"}},
        ),
        _event("message_delta", {"usage": {"output_tokens": 8}}),
        _event("message_stop", {}),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    adapter = AnthropicCompletionAdapter(api_key="sk-ant-test", client=_client(handler))
    chunks = [c async for c in adapter.stream_completion(_REQUEST)]

    assert chunks[0] == "Hello"
    assert chunks[1] == " world"
    usage = chunks[2]
    assert isinstance(usage, ProviderUsage)
    assert usage.prompt_tokens == 20
    assert usage.completion_tokens == 8
    assert len(chunks) == 3


async def test_repeated_message_delta_keeps_the_final_output_token_count() -> None:
    # Anthropic can emit message_delta more than once mid-stream; the
    # last one before message_stop is authoritative.
    body = _sse_body(
        _event("message_start", {"message": {"usage": {"input_tokens": 5}}}),
        _event("content_block_delta", {"delta": {"type": "text_delta", "text": "hi"}}),
        _event("message_delta", {"usage": {"output_tokens": 1}}),
        _event("message_delta", {"usage": {"output_tokens": 7}}),
        _event("message_stop", {}),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    adapter = AnthropicCompletionAdapter(api_key="sk-ant-test", client=_client(handler))
    chunks = [c async for c in adapter.stream_completion(_REQUEST)]

    usage = chunks[-1]
    assert isinstance(usage, ProviderUsage)
    assert usage.completion_tokens == 7


async def test_system_messages_are_sent_as_top_level_system_field_not_in_messages() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_body(_event("message_stop", {})))

    adapter = AnthropicCompletionAdapter(api_key="sk-ant-test", client=_client(handler))
    async for _ in adapter.stream_completion(_REQUEST):
        pass

    body = captured["body"]
    assert body["system"] == "You are helpful."
    assert all(m["role"] != "system" for m in body["messages"])
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_5xx_response_raises_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(529, content=b"overloaded")

    adapter = AnthropicCompletionAdapter(api_key="sk-ant-test", client=_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is True


async def test_400_response_raises_non_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"invalid request")

    adapter = AnthropicCompletionAdapter(api_key="sk-ant-test", client=_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is False


async def test_transport_error_raises_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = AnthropicCompletionAdapter(api_key="sk-ant-test", client=_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is True


def test_capabilities_reports_claude_haiku() -> None:
    adapter = AnthropicCompletionAdapter(api_key="sk-ant-test", client=httpx.AsyncClient())
    caps = adapter.capabilities()
    assert any(c.model == "claude-haiku-4-5" for c in caps)
