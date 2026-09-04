"""_build_generator's provider-instantiation wiring (§3.2.4, ADR-3.5) —
which real adapters get constructed, in what fallback order, and that a
missing credential never instantiates a live provider. Complements
test_llm_router.py (the router's own fallback/breaker/concurrency
behavior, provider-agnostic) and test_groq_adapter.py (Groq's own wire
behavior) — this file is specifically about composition.py's gating.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aether.adapters.echo.generator import EchoGenerator
from aether.adapters.groq.completion import GroqCompletionAdapter
from aether.app.llm.router import LlmRouter
from aether.config import Settings
from aether.http.composition import _build_generator
from tests.unit.fakes.auth import FakeClock

pytestmark = pytest.mark.unit


def _clock() -> FakeClock:
    return FakeClock(start=datetime.now(UTC))


def test_no_provider_keys_falls_back_to_echo_generator() -> None:
    settings = Settings()
    generator = _build_generator(settings, clock=_clock())
    assert isinstance(generator, EchoGenerator)


def test_missing_groq_key_does_not_instantiate_a_groq_provider() -> None:
    """Groq absent, OpenAI/Anthropic also absent -> still EchoGenerator,
    not a router with a live-but-unkeyed Groq adapter."""
    settings = Settings(groq_api_key="")
    generator = _build_generator(settings, clock=_clock())
    assert isinstance(generator, EchoGenerator)


def test_openai_configured_without_groq_still_builds_a_router_with_only_openai() -> None:
    """Existing single-provider behavior is unaffected by Groq's
    existence in the codebase when Groq itself isn't configured."""
    settings = Settings(openai_api_key="sk-test")
    generator = _build_generator(settings, clock=_clock())
    assert isinstance(generator, LlmRouter)
    assert generator.primary_model == "gpt-4o-mini"


def test_groq_key_present_instantiates_a_real_groq_provider_in_the_router() -> None:
    settings = Settings(groq_api_key="gsk_test", groq_model="openai/gpt-oss-20b")
    generator = _build_generator(settings, clock=_clock())
    assert isinstance(generator, LlmRouter)
    assert generator.primary_model == "openai/gpt-oss-20b"
    # The router's internal capability map is keyed by (provider, model)
    # — reaching it via the same lookup LlmRouter.generate() uses for
    # cost accounting proves a real GroqCompletionAdapter was wired in,
    # not just that *something* is configured.
    assert ("groq", "openai/gpt-oss-20b") in generator._capabilities


def test_groq_uses_the_configured_base_url_and_model() -> None:
    settings = Settings(
        groq_api_key="gsk_test",
        groq_model="llama-3.1-8b-instant",
        groq_base_url="https://custom.groq.example/openai/v1",
    )
    generator = _build_generator(settings, clock=_clock())
    assert isinstance(generator, LlmRouter)
    groq_adapter = generator._providers["groq"]
    assert isinstance(groq_adapter, GroqCompletionAdapter)
    assert groq_adapter._model == "llama-3.1-8b-instant"
    assert groq_adapter._base_url == "https://custom.groq.example/openai/v1"


def test_all_three_providers_configured_preserves_openai_first_fallback_order() -> None:
    settings = Settings(
        openai_api_key="sk-test",
        anthropic_api_key="sk-ant-test",
        groq_api_key="gsk_test",
    )
    generator = _build_generator(settings, clock=_clock())
    assert isinstance(generator, LlmRouter)
    assert generator.primary_model == "gpt-4o-mini"  # OpenAI stays first
    assert set(generator._providers.keys()) == {"openai", "anthropic", "groq"}


def test_groq_only_configured_becomes_the_sole_and_primary_provider() -> None:
    settings = Settings(groq_api_key="gsk_test")
    generator = _build_generator(settings, clock=_clock())
    assert isinstance(generator, LlmRouter)
    assert generator.primary_model == settings.groq_model
    assert set(generator._providers.keys()) == {"groq"}


def test_groq_default_model_and_base_url_match_documented_defaults() -> None:
    settings = Settings(groq_api_key="gsk_test")
    generator = _build_generator(settings, clock=_clock())
    assert isinstance(generator, LlmRouter)
    groq_adapter = generator._providers["groq"]
    assert isinstance(groq_adapter, GroqCompletionAdapter)
    assert groq_adapter._base_url == "https://api.groq.com/openai/v1"
    assert groq_adapter._model == "openai/gpt-oss-20b"
