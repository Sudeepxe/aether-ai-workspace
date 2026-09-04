"""Groq ProviderAdapterPort implementation (§3.2.4, ADR-3.5). Groq
publishes an OpenAI-compatible Chat Completions surface at
``https://api.groq.com/openai/v1`` — this adapter is a thin wrapper over
the same shared streaming/SSE-parsing logic
(``adapters/openai_compatible/completion.py``) ``OpenAiCompletionAdapter``
itself uses, rather than a second hand-copied implementation of the same
wire format (see that module's own docstring).

Per-model metadata (context window, real per-token pricing) is an
explicit table below, not one fixed value applied to whatever model
happens to be configured — Phase 4 of docs/REMEDIATION_PLAN.md, closing
a real audit finding: the previous single hardcoded cost pair
(5,900/7,900 microcents per 1K prompt/completion tokens) was silently
applied to any AETHER_GROQ_MODEL value, and per this repo's own
microcents convention (1 microcent = 1e-8 USD, config.py) actually
worked out to $0.059/$0.079 per 1M tokens — not this file's own default
model's real price, and (recalculated the same way) not the $0.59/$0.79
of Llama 3.3 70B Versatile pricing either, despite the resemblance in
the digits. Wherever that number came from, it wasn't a verified price
for any model this file has ever defaulted to.

An AETHER_GROQ_MODEL absent from _MODEL_METADATA now fails loudly at
construction time (composition.py builds this adapter eagerly at
process startup, so this is a startup failure, not a first-request
one) — silently reusing another model's pricing for cost accounting
would produce wrong-but-plausible-looking budget numbers, which is
worse than a startup crash that names exactly what's missing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from aether.adapters.openai_compatible.completion import stream_openai_compatible_completion
from aether.ports.llm import CompletionRequest, ProviderCapability, ProviderChunk


@dataclass(frozen=True, slots=True)
class _GroqModelMetadata:
    max_context_tokens: int
    cost_per_1k_prompt_microcents: int
    cost_per_1k_completion_microcents: int


# Verified against Groq's published model docs
# (console.groq.com/docs/models) on 2026-09-04 — re-verify (and update
# this date) before trusting these figures for real budget enforcement;
# Groq's catalog and pricing change independently of this router's
# release cadence. This freshness marker is the actual fix for the
# original bug: the stale 5,900/7,900 values had no verified-on date at
# all, so nothing made their staleness visible.
#
# $ figures below are Groq's own per-1M-token prices; microcents here =
# USD_per_1M * 100_000 (1 microcent = 1e-8 USD, config.py's convention),
# cross-checked against this repo's existing, unflagged gpt-4o-mini
# entry as a calibration point: $0.15/$0.60 per 1M ->
# 15,000/60,000 microcents per 1K (adapters/openai/completion.py).
_MODEL_METADATA: dict[str, _GroqModelMetadata] = {
    "openai/gpt-oss-20b": _GroqModelMetadata(
        max_context_tokens=131_072,
        cost_per_1k_prompt_microcents=7_500,  # $0.075 / 1M input tokens
        cost_per_1k_completion_microcents=30_000,  # $0.30 / 1M output tokens
    ),
    "openai/gpt-oss-120b": _GroqModelMetadata(
        max_context_tokens=131_072,
        cost_per_1k_prompt_microcents=15_000,  # $0.15 / 1M input tokens
        cost_per_1k_completion_microcents=60_000,  # $0.60 / 1M output tokens
    ),
}


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
        if model not in _MODEL_METADATA:
            raise ValueError(
                f"AETHER_GROQ_MODEL={model!r} has no verified cost/context metadata in "
                "GroqCompletionAdapter._MODEL_METADATA. Refusing to start rather than "
                "silently reuse another model's pricing, which would produce "
                "wrong-but-plausible-looking budget numbers. Known models: "
                f"{', '.join(sorted(_MODEL_METADATA))}."
            )
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        # Accepts an injected client so tests never make a real network
        # call — mirrors OpenAiCompletionAdapter/AnthropicCompletionAdapter.
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def capabilities(self) -> list[ProviderCapability]:
        metadata = _MODEL_METADATA[self._model]
        return [
            ProviderCapability(
                provider="groq",
                model=self._model,
                max_context_tokens=metadata.max_context_tokens,
                supports_tools=True,
                supports_vision=False,
                cost_per_1k_prompt_microcents=metadata.cost_per_1k_prompt_microcents,
                cost_per_1k_completion_microcents=metadata.cost_per_1k_completion_microcents,
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
