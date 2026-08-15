from __future__ import annotations

import json

import httpx
import pytest

from aether.adapters.openai.completion import OpenAiCompletionAdapter
from aether.ports.llm import (
    CompletionRequest,
    LlmMessage,
    LlmMessageRole,
    ProviderError,
    ProviderUsage,
)

pytestmark = pytest.mark.unit

_REQUEST = CompletionRequest(
    messages=[LlmMessage(role=LlmMessageRole.USER, content="hi")],
    model="gpt-4o-mini",
    max_tokens=100,
)


def _sse_body(*lines: str) -> bytes:
    return ("\n\n".join(f"data: {line}" for line in lines) + "\n\n").encode()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_stream_completion_yields_text_deltas_then_usage() -> None:
    body = _sse_body(
        json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
        json.dumps({"choices": [{"delta": {"content": " world"}}]}),
        json.dumps({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 34}}),
        "[DONE]",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    adapter = OpenAiCompletionAdapter(api_key="sk-test", client=_client(handler))
    chunks = [c async for c in adapter.stream_completion(_REQUEST)]

    assert chunks[0] == "Hello"
    assert chunks[1] == " world"
    usage = chunks[2]
    assert isinstance(usage, ProviderUsage)
    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 34
    assert len(chunks) == 3  # nothing yielded after [DONE]


async def test_request_body_requests_streamed_usage() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_body("[DONE]"))

    adapter = OpenAiCompletionAdapter(api_key="sk-test", client=_client(handler))
    async for _ in adapter.stream_completion(_REQUEST):
        pass

    assert captured["body"]["stream_options"] == {"include_usage": True}
    assert captured["body"]["stream"] is True
    assert captured["body"]["model"] == "gpt-4o-mini"


async def test_5xx_response_raises_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"service unavailable")

    adapter = OpenAiCompletionAdapter(api_key="sk-test", client=_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is True


async def test_429_response_raises_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"rate limited")

    adapter = OpenAiCompletionAdapter(api_key="sk-test", client=_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is True


async def test_400_response_raises_non_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"invalid request")

    adapter = OpenAiCompletionAdapter(api_key="sk-test", client=_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is False


async def test_transport_error_raises_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = OpenAiCompletionAdapter(api_key="sk-test", client=_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is True


def test_capabilities_reports_gpt_4o_mini() -> None:
    adapter = OpenAiCompletionAdapter(api_key="sk-test", client=httpx.AsyncClient())
    caps = adapter.capabilities()
    assert any(c.model == "gpt-4o-mini" for c in caps)
