from __future__ import annotations

import json

import httpx
import pytest

from aether.adapters.groq.completion import GroqCompletionAdapter
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
    model="openai/gpt-oss-20b",
    max_tokens=100,
)


def _sse_body(*lines: str) -> bytes:
    return ("\n\n".join(f"data: {line}" for line in lines) + "\n\n").encode()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _adapter(client: httpx.AsyncClient, *, base_url: str = "https://api.groq.com/openai/v1"):
    return GroqCompletionAdapter(
        api_key="gsk_test_key",
        model="openai/gpt-oss-20b",
        base_url=base_url,
        client=client,
    )


async def test_stream_completion_yields_text_deltas_then_usage() -> None:
    # Same wire shape as OpenAI (test_openai_adapter.py's twin test) —
    # Groq's Chat Completions surface is OpenAI-compatible by design.
    body = _sse_body(
        json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
        json.dumps({"choices": [{"delta": {"content": " world"}}]}),
        json.dumps({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 34}}),
        "[DONE]",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    adapter = _adapter(_client(handler))
    chunks = [c async for c in adapter.stream_completion(_REQUEST)]

    assert chunks[0] == "Hello"
    assert chunks[1] == " world"
    usage = chunks[2]
    assert isinstance(usage, ProviderUsage)
    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 34
    assert len(chunks) == 3  # nothing yielded after [DONE]


async def test_request_is_sent_to_the_configured_base_url() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth_header"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_body("[DONE]"))

    adapter = _adapter(_client(handler), base_url="https://custom.groq.example/openai/v1")
    async for _ in adapter.stream_completion(_REQUEST):
        pass

    assert captured["url"] == "https://custom.groq.example/openai/v1/chat/completions"
    assert captured["auth_header"] == "Bearer gsk_test_key"
    assert captured["body"]["model"] == "openai/gpt-oss-20b"
    assert captured["body"]["stream"] is True
    assert captured["body"]["stream_options"] == {"include_usage": True}


async def test_default_base_url_has_no_trailing_slash_duplication() -> None:
    # base_url is stored with a trailing "/" stripped (config.py's
    # groq_base_url default itself has none, but a user-supplied value
    # with one shouldn't produce "//chat/completions").
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, content=_sse_body("[DONE]"))

    adapter = _adapter(_client(handler), base_url="https://api.groq.com/openai/v1/")
    async for _ in adapter.stream_completion(_REQUEST):
        pass

    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"


async def test_5xx_response_raises_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"service unavailable")

    adapter = _adapter(_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is True


async def test_429_response_raises_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"rate limited")

    adapter = _adapter(_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is True


async def test_400_response_raises_non_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"invalid request")

    adapter = _adapter(_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is False


async def test_transport_error_raises_retryable_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = _adapter(_client(handler))
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert exc_info.value.retryable is True


async def test_api_key_never_appears_in_a_provider_error_message() -> None:
    """Security review requirement: a Groq error (4xx/5xx body, or a
    transport failure) must never leak the API key into the exception
    message an operator might log or a client might see."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"invalid_api_key: the key you provided is malformed")

    adapter = GroqCompletionAdapter(
        api_key="gsk_super_secret_value_do_not_leak",
        model="openai/gpt-oss-20b",
        base_url="https://api.groq.com/openai/v1",
        client=_client(handler),
    )
    with pytest.raises(ProviderError) as exc_info:
        async for _ in adapter.stream_completion(_REQUEST):
            pass
    assert "gsk_super_secret_value_do_not_leak" not in str(exc_info.value)


def test_capabilities_reports_the_configured_model() -> None:
    # openai/gpt-oss-120b, not the default openai/gpt-oss-20b — proves
    # capabilities() reflects whatever model was actually configured.
    # Must be a model with verified metadata (Phase 4) since construction
    # now fails loudly on an unrecognized one.
    adapter = GroqCompletionAdapter(
        api_key="gsk_test_key",
        model="openai/gpt-oss-120b",
        base_url="https://api.groq.com/openai/v1",
        client=httpx.AsyncClient(),
    )
    caps = adapter.capabilities()
    assert any(c.model == "openai/gpt-oss-120b" and c.provider == "groq" for c in caps)


def test_name_attribute_is_groq() -> None:
    adapter = GroqCompletionAdapter(
        api_key="gsk_test_key",
        model="openai/gpt-oss-20b",
        base_url="https://api.groq.com/openai/v1",
        client=httpx.AsyncClient(),
    )
    assert adapter.name == "groq"


# --- Phase 4 (docs/REMEDIATION_PLAN.md): per-model metadata ----------------


def test_known_model_resolves_its_own_verified_context_and_cost() -> None:
    """Each verified model in _MODEL_METADATA reports its own real
    figures, not a single value reused across every model — the exact
    bug this phase fixes. Values cross-checked independently against
    Groq's published docs (console.groq.com/docs/models) on 2026-09-04,
    not copied from the old hardcoded constants."""
    twenty_b = GroqCompletionAdapter(
        api_key="gsk_test_key",
        model="openai/gpt-oss-20b",
        base_url="https://api.groq.com/openai/v1",
        client=httpx.AsyncClient(),
    ).capabilities()[0]
    assert twenty_b.max_context_tokens == 131_072
    assert twenty_b.cost_per_1k_prompt_microcents == 7_500  # $0.075 / 1M input
    assert twenty_b.cost_per_1k_completion_microcents == 30_000  # $0.30 / 1M output

    one_twenty_b = GroqCompletionAdapter(
        api_key="gsk_test_key",
        model="openai/gpt-oss-120b",
        base_url="https://api.groq.com/openai/v1",
        client=httpx.AsyncClient(),
    ).capabilities()[0]
    assert one_twenty_b.max_context_tokens == 131_072
    assert one_twenty_b.cost_per_1k_prompt_microcents == 15_000  # $0.15 / 1M input
    assert one_twenty_b.cost_per_1k_completion_microcents == 60_000  # $0.60 / 1M output


def test_unrecognized_model_fails_loudly_at_construction_not_capabilities() -> None:
    """Wrong-but-plausible billing numbers are worse than a crash: an
    AETHER_GROQ_MODEL with no verified metadata must raise immediately
    when the adapter is constructed (composition.py builds it eagerly
    at process startup) — never lazily on the first capabilities()/
    stream_completion() call, and never silently fall back to some
    other model's pricing."""
    with pytest.raises(ValueError, match="no verified cost/context metadata"):
        GroqCompletionAdapter(
            api_key="gsk_test_key",
            model="llama-3.1-8b-instant",  # a real Groq model, just not one this file verifies
            base_url="https://api.groq.com/openai/v1",
            client=httpx.AsyncClient(),
        )


def test_unrecognized_model_error_names_the_bad_value_and_the_known_models() -> None:
    """The error an operator actually sees at startup must be
    immediately actionable — which value was wrong, and what to set
    instead — not a bare exception type."""
    with pytest.raises(ValueError) as exc_info:
        GroqCompletionAdapter(
            api_key="gsk_test_key",
            model="totally-unknown-model-xyz",
            base_url="https://api.groq.com/openai/v1",
            client=httpx.AsyncClient(),
        )
    message = str(exc_info.value)
    assert "totally-unknown-model-xyz" in message
    assert "openai/gpt-oss-20b" in message
    assert "openai/gpt-oss-120b" in message
