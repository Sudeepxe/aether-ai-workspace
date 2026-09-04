"""Groq ProviderAdapterPort implementation (§3.2.4, ADR-3.5). Groq
publishes an OpenAI-compatible Chat Completions surface at
``https://api.groq.com/openai/v1`` — this adapter is a thin wrapper over
the same shared streaming/SSE-parsing logic
(``adapters/openai_compatible/completion.py``) ``OpenAiCompletionAdapter``
itself uses, rather than a second hand-copied implementation of the same
wire format (see that module's own docstring).

Unlike OpenAI/Anthropic's static per-model capability registries, this
adapter's registry holds exactly the one model config.py's
``groq_model`` names — Groq's catalog and pricing change independently
of this router's release cadence, so a fuller hardcoded table here would
silently go stale faster than the credential itself does. The
placeholder cost figures below follow the same "hardcoded, acceptable
simplification for a demo-scale router" posture the OpenAI/Anthropic
adapters' own registries already document; refresh from Groq's real
pricing page before relying on this for real budget enforcement.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from aether.adapters.openai_compatible.completion import stream_openai_compatible_completion
from aether.ports.llm import CompletionRequest, ProviderCapability, ProviderChunk

_DEFAULT_MAX_CONTEXT_TOKENS = 128_000
_COST_PER_1K_PROMPT_MICROCENTS = 5_900
_COST_PER_1K_COMPLETION_MICROCENTS = 7_900


class GroqCompletionAdapter:
    name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        # Accepts an injected client so tests never make a real network
        # call — mirrors OpenAiCompletionAdapter/AnthropicCompletionAdapter.
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def capabilities(self) -> list[ProviderCapability]:
        return [
            ProviderCapability(
                provider="groq",
                model=self._model,
                max_context_tokens=_DEFAULT_MAX_CONTEXT_TOKENS,
                supports_tools=True,
                supports_vision=False,
                cost_per_1k_prompt_microcents=_COST_PER_1K_PROMPT_MICROCENTS,
                cost_per_1k_completion_microcents=_COST_PER_1K_COMPLETION_MICROCENTS,
            )
        ]

    def stream_completion(self, request: CompletionRequest) -> AsyncIterator[ProviderChunk]:
        return stream_openai_compatible_completion(
            client=self._client,
            base_url=self._base_url,
            api_key=self._api_key,
            provider_name="groq",
            request=request,
        )
