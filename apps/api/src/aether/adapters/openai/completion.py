"""OpenAI ProviderAdapterPort implementation (§3.2.4). Raw httpx against
the REST API rather than the official SDK — ADR-3.5's "thin in-house
router" explicitly weighs avoiding dependency churn, and httpx is already
a dependency (ADR-11.1's email adapter). Streaming via
``chat/completions``'s SSE response, not the SDK's stream helper.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from aether.ports.llm import (
    CompletionRequest,
    ProviderCapability,
    ProviderChunk,
    ProviderError,
    ProviderUsage,
)

_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

# Cost per 1K tokens, in microcents (1 microcent = 1e-6 US cent). Prices
# as of this sprint; a real deployment would refresh these from the
# provider's pricing page or a config override — hardcoded here is a
# documented, acceptable simplification for a demo-scale router.
_CAPABILITIES: dict[str, ProviderCapability] = {
    "gpt-4o-mini": ProviderCapability(
        provider="openai",
        model="gpt-4o-mini",
        max_context_tokens=128_000,
        supports_tools=True,
        supports_vision=True,
        cost_per_1k_prompt_microcents=15_000,
        cost_per_1k_completion_microcents=60_000,
    ),
}


class OpenAiCompletionAdapter:
    name = "openai"

    def __init__(self, *, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        # Accepts an injected client so tests never make a real network
        # call — see tests/unit/test_openai_adapter.py.
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def capabilities(self) -> list[ProviderCapability]:
        return list(_CAPABILITIES.values())

    async def stream_completion(self, request: CompletionRequest) -> AsyncIterator[ProviderChunk]:
        body = {
            "model": request.model,
            "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
            "max_tokens": request.max_tokens,
            "stream": True,
            # Without this, OpenAI's streaming response never includes a
            # usage object at all — settlement would have nothing
            # authoritative to read (§3.2.14).
            "stream_options": {"include_usage": True},
        }
        try:
            async with self._client.stream(
                "POST",
                _COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            ) as response:
                if response.status_code >= 400:
                    body_text = (await response.aread()).decode(errors="replace")
                    raise ProviderError(
                        f"openai returned {response.status_code}: {body_text}",
                        retryable=response.status_code >= 500 or response.status_code == 429,
                    )
                async for chunk in _parse_openai_sse(response):
                    yield chunk
        except httpx.TransportError as exc:
            raise ProviderError(f"openai transport error: {exc}", retryable=True) from exc


async def _parse_openai_sse(response: httpx.Response) -> AsyncIterator[ProviderChunk]:
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for line in block.split("\n"):
                if not line.startswith("data: "):
                    continue
                payload = line.removeprefix("data: ")
                if payload == "[DONE]":
                    return
                data = json.loads(payload)
                usage = data.get("usage")
                if usage:
                    yield ProviderUsage(
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                    )
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {}).get("content")
                if delta:
                    yield delta
