from __future__ import annotations

import pytest
from evals.harness.judge import _generator_family, _parse_verdict, _select_judge, compute_agreement

from aether.config import Settings

pytestmark = pytest.mark.unit


def _settings(*, openai_api_key: str = "", anthropic_api_key: str = "") -> Settings:
    return Settings(openai_api_key=openai_api_key, anthropic_api_key=anthropic_api_key)


def test_generator_family_recognizes_openai_models() -> None:
    assert _generator_family("gpt-4o-mini") == "openai"


def test_generator_family_recognizes_anthropic_models() -> None:
    assert _generator_family("claude-haiku-4-5") == "anthropic"


def test_generator_family_recognizes_the_echo_placeholder() -> None:
    assert _generator_family("echo-v1") == "echo"


def test_generator_family_is_none_for_an_unrecognized_model() -> None:
    assert _generator_family("some-local-llama-build") is None


def test_select_judge_is_none_when_no_provider_is_configured() -> None:
    assert _select_judge("echo-v1", _settings()) is None


def test_select_judge_picks_anthropic_when_generator_is_openai() -> None:
    selected = _select_judge("gpt-4o-mini", _settings(anthropic_api_key="sk-ant-test"))
    assert selected is not None
    _provider, judge_model = selected
    assert judge_model == "claude-haiku-4-5"


def test_select_judge_picks_openai_when_generator_is_anthropic() -> None:
    selected = _select_judge("claude-haiku-4-5", _settings(openai_api_key="sk-oai-test"))
    assert selected is not None
    _provider, judge_model = selected
    assert judge_model == "gpt-4o-mini"


def test_select_judge_refuses_a_same_family_judge() -> None:
    """The real, code-enforced cross-family check (ADR-6.5): only an
    OpenAI key is configured and the generator is also OpenAI — no
    valid judge exists, this must not silently fall back to judging
    itself."""
    assert _select_judge("gpt-4o-mini", _settings(openai_api_key="sk-oai-test")) is None


def test_select_judge_prefers_the_cross_family_option_when_both_keys_exist() -> None:
    selected = _select_judge(
        "gpt-4o-mini", _settings(openai_api_key="sk-oai-test", anthropic_api_key="sk-ant-test")
    )
    assert selected is not None
    _provider, judge_model = selected
    assert judge_model == "claude-haiku-4-5"


def test_parse_verdict_recognizes_faithful() -> None:
    faithful, reasoning = _parse_verdict(
        "VERDICT: faithful\nREASONING: The reply matches the context exactly."
    )
    assert faithful is True
    assert "matches the context" in reasoning


def test_parse_verdict_recognizes_unfaithful() -> None:
    faithful, reasoning = _parse_verdict(
        "VERDICT: unfaithful\nREASONING: The reply invents a number not in the context."
    )
    assert faithful is False
    assert "invents a number" in reasoning


def test_parse_verdict_falls_back_to_the_raw_text_as_reasoning_if_unparseable() -> None:
    faithful, reasoning = _parse_verdict("the model didn't follow the format")
    assert faithful is False
    assert reasoning == "the model didn't follow the format"


def test_compute_agreement_full_agreement() -> None:
    assert compute_agreement([True, False, True], [True, False, True]) == 1.0


def test_compute_agreement_partial_agreement() -> None:
    assert compute_agreement([True, False, True, False], [True, True, True, True]) == 0.5


def test_compute_agreement_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        compute_agreement([True], [True, False])


def test_compute_agreement_rejects_an_empty_slice() -> None:
    with pytest.raises(ValueError, match="empty"):
        compute_agreement([], [])
