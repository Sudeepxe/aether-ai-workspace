from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from aether.app.llm.circuit_breaker import CircuitBreaker
from aether.app.llm.router import LlmRouter
from aether.domain.errors import NoProviderAvailableError
from aether.ports.chat import GenerationUsage
from aether.ports.llm import (
    CompletionRequest,
    ProviderCapability,
    ProviderChunk,
    ProviderError,
    ProviderUsage,
)
from tests.unit.fakes.auth import FakeClock

pytestmark = pytest.mark.unit

_CAPABILITY = ProviderCapability(
    provider="fake",
    model="fake-model",
    max_context_tokens=8_000,
    supports_tools=False,
    supports_vision=False,
    cost_per_1k_prompt_microcents=10_000,
    cost_per_1k_completion_microcents=20_000,
)
_DEFAULT_USAGE = ProviderUsage(prompt_tokens=10, completion_tokens=5)
_USAGE_UNSET = object()


class FakeProviderAdapter:
    """A ProviderAdapterPort test double: yields a fixed script of chunks,
    or raises a given error after ``fail_after`` chunks."""

    def __init__(
        self,
        *,
        name: str,
        model: str = "fake-model",
        chunks: list[str] | None = None,
        usage: ProviderUsage | object = _USAGE_UNSET,
        error: ProviderError | None = None,
        fail_after: int = 0,
        capability: ProviderCapability = _CAPABILITY,
    ) -> None:
        self.name = name
        self._model = model
        self._chunks = chunks or []
        self._usage = _DEFAULT_USAGE if usage is _USAGE_UNSET else usage
        self._error = error
        self._fail_after = fail_after
        self._capability = capability
        self.calls: list[CompletionRequest] = []

    def capabilities(self) -> list[ProviderCapability]:
        return [self._capability]

    async def stream_completion(self, request: CompletionRequest) -> AsyncIterator[ProviderChunk]:
        self.calls.append(request)
        for i, chunk in enumerate(self._chunks):
            if self._error is not None and i == self._fail_after:
                raise self._error
            yield chunk
        if self._error is not None and self._fail_after >= len(self._chunks):
            raise self._error
        if self._usage is not None:
            yield self._usage


def _router(
    *, providers: dict[str, FakeProviderAdapter], model_chain: list[tuple[str, str]]
) -> tuple[LlmRouter, dict[str, CircuitBreaker]]:
    clock = FakeClock(start=datetime.now(UTC))
    breakers = {name: CircuitBreaker(clock=clock) for name in providers}
    router = LlmRouter(providers=providers, breakers=breakers, model_chain=model_chain)
    return router, breakers


async def test_single_provider_success_yields_deltas_then_costed_usage() -> None:
    provider = FakeProviderAdapter(
        name="fake",
        chunks=["hello", " world"],
        usage=ProviderUsage(prompt_tokens=100, completion_tokens=50),
    )
    router, _ = _router(providers={"fake": provider}, model_chain=[("fake", "fake-model")])

    chunks = [c async for c in router.generate(thread_history=[], user_content="hi")]

    assert chunks[:2] == ["hello", " world"]
    usage = chunks[2]
    assert isinstance(usage, GenerationUsage)
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50
    assert usage.model == "fake-model"
    # (100 * 10_000 // 1000) + (50 * 20_000 // 1000) = 1_000 + 1_000
    assert usage.cost_microcents == 2_000


async def test_primary_model_is_first_entry_in_the_chain() -> None:
    router, _ = _router(
        providers={
            "a": FakeProviderAdapter(name="a", chunks=[]),
            "b": FakeProviderAdapter(name="b", chunks=[]),
        },
        model_chain=[("a", "model-a"), ("b", "model-b")],
    )
    assert router.primary_model == "model-a"


async def test_retryable_failure_before_any_token_falls_back_to_next_provider() -> None:
    failing = FakeProviderAdapter(
        name="primary", chunks=[], error=ProviderError("down", retryable=True), fail_after=0
    )
    backup = FakeProviderAdapter(name="backup", chunks=["fallback reply"])
    router, breakers = _router(
        providers={"primary": failing, "backup": backup},
        model_chain=[("primary", "fake-model"), ("backup", "fake-model")],
    )

    chunks = [c async for c in router.generate(thread_history=[], user_content="hi")]

    assert chunks[0] == "fallback reply"
    assert len(failing.calls) == 1
    assert len(backup.calls) == 1
    # The failed provider's breaker recorded the failure; the successful
    # one's recorded success (both observable via state, not just chunks).
    assert breakers["primary"].state.value == "closed"  # 1 failure, below threshold=3
    assert breakers["backup"].state.value == "closed"


async def test_non_retryable_failure_raises_immediately_without_trying_fallback() -> None:
    failing = FakeProviderAdapter(
        name="primary",
        chunks=[],
        error=ProviderError("bad request", retryable=False),
        fail_after=0,
    )
    backup = FakeProviderAdapter(name="backup", chunks=["should never run"])
    router, _ = _router(
        providers={"primary": failing, "backup": backup},
        model_chain=[("primary", "fake-model"), ("backup", "fake-model")],
    )

    with pytest.raises(NoProviderAvailableError):
        async for _ in router.generate(thread_history=[], user_content="hi"):
            pass

    assert len(backup.calls) == 0


async def test_failure_after_first_token_propagates_without_fallback() -> None:
    """Retry only before the first streamed token (§3.2.4): once real
    output has reached the caller, a mid-stream failure must surface as
    a real error, never a silent retry that would duplicate/garble
    already-delivered content."""
    failing = FakeProviderAdapter(
        name="primary",
        chunks=["partial", "never sent"],
        error=ProviderError("connection reset", retryable=True),
        fail_after=1,
    )
    backup = FakeProviderAdapter(name="backup", chunks=["should never run"])
    router, _ = _router(
        providers={"primary": failing, "backup": backup},
        model_chain=[("primary", "fake-model"), ("backup", "fake-model")],
    )

    received: list[str] = []
    with pytest.raises(ProviderError):
        async for chunk in router.generate(thread_history=[], user_content="hi"):
            assert isinstance(chunk, str)
            received.append(chunk)

    assert received == ["partial"]
    assert len(backup.calls) == 0


async def test_open_breaker_skips_provider_without_calling_it() -> None:
    primary = FakeProviderAdapter(name="primary", chunks=["should be skipped"])
    backup = FakeProviderAdapter(name="backup", chunks=["backup reply"])
    router, breakers = _router(
        providers={"primary": primary, "backup": backup},
        model_chain=[("primary", "fake-model"), ("backup", "fake-model")],
    )
    for _ in range(3):
        breakers["primary"].record_failure()  # trip the breaker before any call

    chunks = [c async for c in router.generate(thread_history=[], user_content="hi")]

    assert chunks[0] == "backup reply"
    assert len(primary.calls) == 0
    assert len(backup.calls) == 1


async def test_all_providers_unavailable_raises_no_provider_available() -> None:
    router, breakers = _router(
        providers={
            "primary": FakeProviderAdapter(name="primary", chunks=[]),
            "backup": FakeProviderAdapter(name="backup", chunks=[]),
        },
        model_chain=[("primary", "fake-model"), ("backup", "fake-model")],
    )
    for breaker in breakers.values():
        for _ in range(3):
            breaker.record_failure()

    with pytest.raises(NoProviderAvailableError):
        async for _ in router.generate(thread_history=[], user_content="hi"):
            pass
