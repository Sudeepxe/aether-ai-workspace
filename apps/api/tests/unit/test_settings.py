"""Settings contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aether.config import Settings

pytestmark = pytest.mark.unit


def test_defaults_are_dev_safe() -> None:
    s = Settings()
    assert s.env == "dev"
    assert s.log_level == "INFO"


def test_settings_are_frozen() -> None:
    s = Settings()
    with pytest.raises(ValidationError):
        s.env = "prod"


def test_env_prefix_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_LOG_LEVEL", "DEBUG")
    assert Settings().log_level == "DEBUG"


def test_groq_defaults_are_unset_key_with_a_real_base_url_and_model() -> None:
    """Empty key by default (same "not a usable key" posture as
    openai_api_key/anthropic_api_key) — composition.py's gate on this
    field is what actually decides whether Groq is ever instantiated
    (see test_llm_composition.py), not this settings class itself."""
    s = Settings()
    assert s.groq_api_key == ""
    assert s.groq_base_url == "https://api.groq.com/openai/v1"
    assert s.groq_model == "openai/gpt-oss-20b"


def test_groq_env_vars_are_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_GROQ_API_KEY", "gsk_from_env")
    monkeypatch.setenv("AETHER_GROQ_BASE_URL", "https://groq.example.internal/v1")
    monkeypatch.setenv("AETHER_GROQ_MODEL", "llama-3.1-8b-instant")
    s = Settings()
    assert s.groq_api_key == "gsk_from_env"
    assert s.groq_base_url == "https://groq.example.internal/v1"
    assert s.groq_model == "llama-3.1-8b-instant"
