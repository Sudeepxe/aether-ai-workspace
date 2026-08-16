"""Live-provider smoke tests (issue #35's literal acceptance criterion:
"at least one real provider round-trips a completion"). Skipped whenever
real credentials aren't available — every other Sprint 4 test proves
correctness against fakes/MockTransport, which needs no network access
and always runs; these are the one exception, gated behind real secrets
so they never run unauthenticated or in ordinary CI without the SOPS
bundle decrypted (see infra/secrets/dev.enc.yaml, ADR-7.5).
"""

from __future__ import annotations

import os

import httpx
import pytest

from aether.adapters.anthropic.completion import AnthropicCompletionAdapter
from aether.adapters.openai.completion import OpenAiCompletionAdapter
from aether.ports.llm import CompletionRequest, LlmMessage, LlmMessageRole, ProviderUsage

pytestmark = pytest.mark.integration

_PROMPT = [LlmMessage(role=LlmMessageRole.USER, content="Reply with exactly one word: hello")]


@pytest.mark.skipif(
    not os.environ.get("AETHER_OPENAI_API_KEY"), reason="AETHER_OPENAI_API_KEY not set"
)
async def test_openai_round_trips_a_real_completion() -> None:
    adapter = OpenAiCompletionAdapter(
        api_key=os.environ["AETHER_OPENAI_API_KEY"], client=httpx.AsyncClient(timeout=30.0)
    )
    request = CompletionRequest(messages=_PROMPT, model="gpt-4o-mini", max_tokens=16)

    text = ""
    usage: ProviderUsage | None = None
    async for chunk in adapter.stream_completion(request):
        if isinstance(chunk, ProviderUsage):
            usage = chunk
        else:
            text += chunk

    assert text.strip() != ""
    assert usage is not None
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens > 0


@pytest.mark.skipif(
    not os.environ.get("AETHER_ANTHROPIC_API_KEY"), reason="AETHER_ANTHROPIC_API_KEY not set"
)
async def test_anthropic_round_trips_a_real_completion() -> None:
    adapter = AnthropicCompletionAdapter(
        api_key=os.environ["AETHER_ANTHROPIC_API_KEY"], client=httpx.AsyncClient(timeout=30.0)
    )
    request = CompletionRequest(messages=_PROMPT, model="claude-haiku-4-5", max_tokens=16)

    text = ""
    usage: ProviderUsage | None = None
    async for chunk in adapter.stream_completion(request):
        if isinstance(chunk, ProviderUsage):
            usage = chunk
        else:
            text += chunk

    assert text.strip() != ""
    assert usage is not None
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens > 0
