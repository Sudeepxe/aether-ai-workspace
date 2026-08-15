"""The LLM Router (§3.2.4, ADR-3.5): implements GeneratorPort so it's a
drop-in replacement for EchoGenerator in the orchestrator (issue #38) —
SendMessage's persistence, SSE contract, buffering, and cross-replica
resume/cancel (S3) are already provider-agnostic by construction.

Routing policy resolution is a fixed default chain for this sprint (no
per-workspace model_policy UI yet, though Workspace.model_policy already
exists as a field from Sprint 1 — wiring the router to actually consult
it is a tracked enhancement, not required by issue #38's acceptance
criterion). Retry only ever happens *before* the first streamed token
(a mid-stream retry would duplicate output) — see the docstring on the
loop below for the exact rule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from aether.app.llm.circuit_breaker import CircuitBreaker
from aether.domain.entities import Message, MessageRole
from aether.domain.errors import NoProviderAvailableError
from aether.ports.chat import GenerationUsage, GeneratorChunk
from aether.ports.llm import (
    CompletionRequest,
    LlmMessage,
    LlmMessageRole,
    ProviderAdapterPort,
    ProviderCapability,
    ProviderError,
    ProviderUsage,
)

_SYSTEM_PROMPT = "You are Aether, a helpful AI assistant."
_DEFAULT_MAX_TOKENS = 1024
_HISTORY_ROLE_MAP = {
    MessageRole.USER: LlmMessageRole.USER,
    MessageRole.ASSISTANT: LlmMessageRole.ASSISTANT,
    MessageRole.SYSTEM: LlmMessageRole.SYSTEM,
}


class LlmRouter:
    def __init__(
        self,
        *,
        providers: dict[str, ProviderAdapterPort],
        breakers: dict[str, CircuitBreaker],
        model_chain: list[tuple[str, str]],
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        """``model_chain`` is an ordered list of (provider_name, model)
        pairs — the fallback order. ``providers``/``breakers`` must have
        an entry for every provider_name that appears in it."""
        self._providers = providers
        self._breakers = breakers
        self._model_chain = model_chain
        self._max_tokens = max_tokens
        self._capabilities: dict[tuple[str, str], ProviderCapability] = {
            (provider_name, cap.model): cap
            for provider_name, provider in providers.items()
            for cap in provider.capabilities()
        }

    @property
    def primary_model(self) -> str:
        return self._model_chain[0][1]

    async def generate(
        self, *, thread_history: list[Message], user_content: str
    ) -> AsyncIterator[GeneratorChunk]:
        messages = _build_messages(thread_history, user_content)
        last_error: Exception | None = None

        for provider_name, model in self._model_chain:
            breaker = self._breakers[provider_name]
            if breaker.is_open():
                continue
            provider = self._providers[provider_name]
            request = CompletionRequest(messages=messages, model=model, max_tokens=self._max_tokens)

            # "Retry only before the first streamed token": once any
            # text delta has reached the caller (and therefore the
            # buffer + the connected client), a mid-stream provider
            # failure must surface as a real error, never a silent retry
            # on a different provider — that would duplicate or garble
            # already-delivered output.
            responded = False
            text_sent = False
            try:
                async for chunk in provider.stream_completion(request):
                    if not responded:
                        breaker.record_success()
                        responded = True
                    if isinstance(chunk, ProviderUsage):
                        capability = self._capabilities[(provider_name, model)]
                        yield GenerationUsage(
                            prompt_tokens=chunk.prompt_tokens,
                            completion_tokens=chunk.completion_tokens,
                            cost_microcents=_cost_microcents(
                                capability,
                                prompt_tokens=chunk.prompt_tokens,
                                completion_tokens=chunk.completion_tokens,
                            ),
                            model=model,
                        )
                    else:
                        text_sent = True
                        yield chunk
                return
            except ProviderError as exc:
                if text_sent:
                    raise
                breaker.record_failure()
                last_error = exc
                if not exc.retryable:
                    raise NoProviderAvailableError(str(exc)) from exc
                continue

        raise NoProviderAvailableError(
            str(last_error)
            if last_error is not None
            else "no provider available (all breakers open)"
        )


def _cost_microcents(
    capability: ProviderCapability, *, prompt_tokens: int, completion_tokens: int
) -> int:
    prompt_cost = prompt_tokens * capability.cost_per_1k_prompt_microcents // 1000
    completion_cost = completion_tokens * capability.cost_per_1k_completion_microcents // 1000
    return prompt_cost + completion_cost


def _build_messages(thread_history: list[Message], user_content: str) -> list[LlmMessage]:
    history = [
        LlmMessage(role=_HISTORY_ROLE_MAP[m.role], content=m.content) for m in thread_history
    ]
    return [
        LlmMessage(role=LlmMessageRole.SYSTEM, content=_SYSTEM_PROMPT),
        *history,
        LlmMessage(role=LlmMessageRole.USER, content=user_content),
    ]
