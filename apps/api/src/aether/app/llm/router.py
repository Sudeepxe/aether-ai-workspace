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

import asyncio
from collections.abc import AsyncIterator

from aether.app.llm.circuit_breaker import CircuitBreaker
from aether.domain.entities import Message, MessageRole
from aether.domain.errors import NoProviderAvailableError
from aether.ports.chat import GenerationUsage, GeneratorChunk, RetrievedContext
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
# ADR-6.4's Gate 2: the generation-side half of two-gate refusal — a
# real provider's actual adherence to this instruction is the eval
# suite's job (Sprint 7), not something this prompt alone can guarantee,
# but the protocol itself must be unambiguous and machine-checkable in
# principle (the exact refusal wording an eval can grep for).
_GROUNDED_SYSTEM_PROMPT = (
    "You are Aether, a helpful AI assistant answering questions using only "
    "the context provided below. Answer strictly from this context — never "
    "from outside knowledge. If the context does not contain the answer, "
    "reply with exactly: \"I don't have information about that in the "
    'knowledge base." and nothing else.\n\nContext:\n{context}'
)
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_MAX_CONCURRENT_PER_PROVIDER = 4
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
        max_concurrent_per_provider: int = _DEFAULT_MAX_CONCURRENT_PER_PROVIDER,
    ) -> None:
        """``model_chain`` is an ordered list of (provider_name, model)
        pairs — the fallback order. ``providers``/``breakers`` must have
        an entry for every provider_name that appears in it.

        ``max_concurrent_per_provider`` bounds how many generations may
        be in flight against any one provider at once (issue #36): one
        tenant's burst must not be able to saturate a provider's own
        connection pool for every other tenant sharing it. A semaphore
        acquired for a request's *entire* streaming duration, not just
        its initial call — the thing being bounded is concurrent
        in-flight generations, not concurrent call starts."""
        self._providers = providers
        self._breakers = breakers
        self._model_chain = model_chain
        self._max_tokens = max_tokens
        self._semaphores = {
            provider_name: asyncio.Semaphore(max_concurrent_per_provider)
            for provider_name in providers
        }
        self._capabilities: dict[tuple[str, str], ProviderCapability] = {
            (provider_name, cap.model): cap
            for provider_name, provider in providers.items()
            for cap in provider.capabilities()
        }

    @property
    def primary_model(self) -> str:
        return self._model_chain[0][1]

    async def generate(
        self,
        *,
        thread_history: list[Message],
        user_content: str,
        context: RetrievedContext | None = None,
    ) -> AsyncIterator[GeneratorChunk]:
        messages = _build_messages(thread_history, user_content, context)
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
                # Held for the whole streaming duration, not just the
                # call's start — bounds concurrent in-flight generations
                # against this provider, not concurrent call attempts.
                async with self._semaphores[provider_name]:
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


def _build_messages(
    thread_history: list[Message], user_content: str, context: RetrievedContext | None
) -> list[LlmMessage]:
    history = [
        LlmMessage(role=_HISTORY_ROLE_MAP[m.role], content=m.content) for m in thread_history
    ]
    system_prompt = _SYSTEM_PROMPT if context is None else _render_grounded_prompt(context)
    return [
        LlmMessage(role=LlmMessageRole.SYSTEM, content=system_prompt),
        *history,
        LlmMessage(role=LlmMessageRole.USER, content=user_content),
    ]


def _render_grounded_prompt(context: RetrievedContext) -> str:
    context_text = (
        "\n\n".join(
            f"[{chunk.document_title} > {chunk.section_path}]\n{chunk.content}"
            for chunk in context.chunks
        )
        if context.chunks
        else "(no relevant context was found)"
    )
    return _GROUNDED_SYSTEM_PROMPT.format(context=context_text)
