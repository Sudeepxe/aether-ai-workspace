"""Anthropic ProviderAdapterPort implementation (§3.2.4). Raw httpx
against the Messages API — see openai/completion.py's docstring for why
(ADR-3.5, avoid dependency churn; httpx is already a dependency).

Anthropic's wire protocol differs from OpenAI's in two ways this adapter
translates: system prompts are a separate top-level ``system`` field, not
a message with role "system"; and streaming uses named SSE events
(``content_block_delta`` carrying the actual text), not one uniform
``data:`` shape.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from aether.ports.llm import (
    CompletionRequest,
    LlmMessageRole,
    ProviderCapability,
    ProviderChunk,
    ProviderError,
    ProviderUsage,
)

_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

_CAPABILITIES: dict[str, ProviderCapability] = {
    "claude-haiku-4-5": ProviderCapability(
        provider="anthropic",
        model="claude-haiku-4-5",
        max_context_tokens=200_000,
        supports_tools=True,
        supports_vision=True,
        cost_per_1k_prompt_microcents=10_000,
        cost_per_1k_completion_microcents=50_000,
    ),
}


class AnthropicCompletionAdapter:
    name = "anthropic"

    def __init__(self, *, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def capabilities(self) -> list[ProviderCapability]:
        return list(_CAPABILITIES.values())

    async def stream_completion(self, request: CompletionRequest) -> AsyncIterator[ProviderChunk]:
        system_parts = [m.content for m in request.messages if m.role is LlmMessageRole.SYSTEM]
        turns = [m for m in request.messages if m.role is not LlmMessageRole.SYSTEM]
        body = {
            "model": request.model,
            "messages": [{"role": m.role.value, "content": m.content} for m in turns],
            "max_tokens": request.max_tokens,
            "stream": True,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)

        try:
            async with self._client.stream(
                "POST",
                _MESSAGES_URL,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                },
                json=body,
            ) as response:
                if response.status_code >= 400:
                    body_text = (await response.aread()).decode(errors="replace")
                    raise ProviderError(
                        f"anthropic returned {response.status_code}: {body_text}",
                        retryable=response.status_code >= 500 or response.status_code == 429,
                    )
                async for chunk in _parse_anthropic_sse(response):
                    yield chunk
        except httpx.TransportError as exc:
            raise ProviderError(f"anthropic transport error: {exc}", retryable=True) from exc


async def _parse_anthropic_sse(response: httpx.Response) -> AsyncIterator[ProviderChunk]:
    # Input tokens arrive in message_start; output tokens only in the
    # (possibly repeated) message_delta events, with the final one
    # holding the authoritative total — both are needed to yield one
    # complete ProviderUsage once the stream ends.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            event_type: str | None = None
            data_line: str | None = None
            for line in block.split("\n"):
                if line.startswith("event: "):
                    event_type = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data_line = line.removeprefix("data: ")
            if data_line is None:
                continue
            data = json.loads(data_line)
            if event_type == "message_start":
                usage = data.get("message", {}).get("usage", {})
                prompt_tokens = usage.get("input_tokens")
            elif event_type == "message_delta":
                usage = data.get("usage", {})
                if "output_tokens" in usage:
                    completion_tokens = usage["output_tokens"]
            elif event_type == "content_block_delta":
                delta = data.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text")
                    if text:
                        yield text
            elif event_type == "message_stop":
                if prompt_tokens is not None and completion_tokens is not None:
                    yield ProviderUsage(
                        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                    )
                return
