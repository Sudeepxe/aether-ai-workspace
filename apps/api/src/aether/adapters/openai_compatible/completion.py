"""Shared OpenAI-wire-format streaming completion logic (§3.2.4,
ADR-3.5): POST ``{base_url}/chat/completions``, ``Authorization: Bearer``,
``data: {...}`` SSE frames terminated by ``data: [DONE]``, with a final
``usage`` object requested via ``stream_options.include_usage``.

Extracted here once a second real provider (Groq — its own stated
"OpenAI-compatible" Chat Completions surface) needed the exact same wire
format ``adapters/openai/completion.py`` already implemented, rather than
hand-copying the SSE parser a second time. Both
``OpenAiCompletionAdapter`` and ``GroqCompletionAdapter`` are thin
wrappers over this one function, differing only in base URL, API key,
and the static capability registry each reports.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from aether.ports.llm import CompletionRequest, ProviderChunk, ProviderError, ProviderUsage


async def stream_openai_compatible_completion(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    provider_name: str,
    request: CompletionRequest,
) -> AsyncIterator[ProviderChunk]:
    """Yields text deltas, then exactly one ``ProviderUsage`` once the
    provider's own usage accounting arrives — the same
    ``ProviderAdapterPort.stream_completion`` contract every adapter
    implements. ``provider_name`` only shapes error messages (never
    logged/included with the API key — see the two ``ProviderError``
    sites below, neither of which ever references ``api_key``); the
    wire format itself is identical across OpenAI-compatible providers.
    """
    body = {
        "model": request.model,
        "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
        "max_tokens": request.max_tokens,
        "stream": True,
        # Without this, an OpenAI-compatible streaming response never
        # includes a usage object at all — settlement would have
        # nothing authoritative to read (§3.2.14).
        "stream_options": {"include_usage": True},
    }
    try:
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        ) as response:
            if response.status_code >= 400:
                body_text = (await response.aread()).decode(errors="replace")
                raise ProviderError(
                    f"{provider_name} returned {response.status_code}: {body_text}",
                    retryable=response.status_code >= 500 or response.status_code == 429,
                )
            async for chunk in _parse_openai_compatible_sse(response):
                yield chunk
    except httpx.TransportError as exc:
        raise ProviderError(f"{provider_name} transport error: {exc}", retryable=True) from exc


async def _parse_openai_compatible_sse(response: httpx.Response) -> AsyncIterator[ProviderChunk]:
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
