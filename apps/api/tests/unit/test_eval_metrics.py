from __future__ import annotations

import pytest
from evals.harness.metrics import aggregate, score_case, score_turn
from evals.harness.runner import CaseResult, TurnResult
from evals.harness.schema import CaseClass, GoldenCase, GoldenTurn

pytestmark = pytest.mark.unit


def _citation(document_title: str, section_path: str = "Intro") -> dict:
    return {"document_title": document_title, "section_path": section_path}


def test_refusal_correct_when_grounded_matches_expectation() -> None:
    golden = GoldenTurn(query="q", expect_grounded=True)
    actual = TurnResult(query="q", grounded=True, content="answer", citations=[])
    assert score_turn(golden, actual).refusal_correct is True


def test_refusal_incorrect_when_grounded_mismatches_expectation() -> None:
    golden = GoldenTurn(query="q", expect_grounded=False)
    actual = TurnResult(query="q", grounded=True, content="answer", citations=[])
    assert score_turn(golden, actual).refusal_correct is False


def test_retrieval_hit_and_recall_when_the_expected_document_is_cited() -> None:
    golden = GoldenTurn(
        query="q",
        expect_grounded=True,
        expect_gold_document="pricing.md",
        expect_gold_section_contains="pricing",
    )
    actual = TurnResult(
        query="q", grounded=True, content="a", citations=[_citation("pricing.md", "Pricing Plans")]
    )
    metrics = score_turn(golden, actual)
    assert metrics.retrieval_hit is True
    assert metrics.citation_recall == 1.0
    assert metrics.citation_precision == 1.0


def test_retrieval_miss_when_a_different_document_is_cited() -> None:
    golden = GoldenTurn(query="q", expect_grounded=True, expect_gold_document="pricing.md")
    actual = TurnResult(
        query="q", grounded=True, content="a", citations=[_citation("unrelated.md")]
    )
    metrics = score_turn(golden, actual)
    assert metrics.retrieval_hit is False
    assert metrics.citation_recall == 0.0
    assert metrics.citation_precision == 0.0


def test_no_expected_document_means_retrieval_metrics_are_not_applicable() -> None:
    golden = GoldenTurn(query="q", expect_grounded=False)
    actual = TurnResult(query="q", grounded=False, content="refusal", citations=[])
    metrics = score_turn(golden, actual)
    assert metrics.retrieval_hit is None
    assert metrics.citation_precision is None
    assert metrics.citation_recall is None


def test_section_substring_must_match_when_specified() -> None:
    golden = GoldenTurn(
        query="q",
        expect_grounded=True,
        expect_gold_document="handbook.md",
        expect_gold_section_contains="refunds",
    )
    actual = TurnResult(
        query="q", grounded=True, content="a", citations=[_citation("handbook.md", "Onboarding")]
    )
    assert score_turn(golden, actual).retrieval_hit is False


def test_adversarial_safe_when_trigger_phrase_absent_from_a_real_generators_reply() -> None:
    golden = GoldenTurn(
        query="q",
        expect_grounded=True,
        adversarial_trigger_phrase="SYSTEM PROMPT LEAKED",
    )
    actual = TurnResult(
        query="q", grounded=True, content="a normal answer", citations=[], model="gpt-4o-mini"
    )
    assert score_turn(golden, actual).adversarial_safe is True


def test_adversarial_unsafe_when_trigger_phrase_present_in_a_real_generators_reply() -> None:
    golden = GoldenTurn(
        query="q",
        expect_grounded=True,
        adversarial_trigger_phrase="SYSTEM PROMPT LEAKED",
    )
    actual = TurnResult(
        query="q",
        grounded=True,
        content="oops SYSTEM PROMPT LEAKED here",
        citations=[],
        model="gpt-4o-mini",
    )
    assert score_turn(golden, actual).adversarial_safe is False


def test_adversarial_safe_is_not_applicable_under_echo_generator() -> None:
    """The real finding this test locks in: EchoGenerator embeds the
    *entire* retrieved chunk verbatim to prove grounding, injection
    payload included — the trigger phrase showing up in its reply is
    correct echo behavior, not a compromised generator. Scoring it as
    "unsafe" would be a false, misleading negative signal, so it's None
    (not applicable) whenever the echo placeholder answered."""
    golden = GoldenTurn(
        query="q",
        expect_grounded=True,
        adversarial_trigger_phrase="SYSTEM OVERRIDE ACKNOWLEDGED",
    )
    actual = TurnResult(
        query="q",
        grounded=True,
        content="Grounded on: doc.md (Section): ...SYSTEM OVERRIDE ACKNOWLEDGED...",
        citations=[],
        model="echo-v1",
    )
    assert score_turn(golden, actual).adversarial_safe is None


def test_score_case_reports_the_error_and_no_turns_when_the_case_failed_to_run() -> None:
    case = GoldenCase(
        id="broken",
        case_class=CaseClass.ANSWERABLE,
        corpus_files=["missing.md"],
        turns=[GoldenTurn(query="q", expect_grounded=True)],
    )
    result = CaseResult(case=case, turns=[], error="RuntimeError: fixture missing")
    metrics = score_case(result)
    assert metrics.ran_successfully is False
    assert metrics.error == "RuntimeError: fixture missing"
    assert metrics.turns == []


def test_aggregate_computes_rates_only_over_applicable_turns() -> None:
    case_a = GoldenCase(
        id="a",
        case_class=CaseClass.ANSWERABLE,
        corpus_files=["a.md"],
        turns=[GoldenTurn(query="q", expect_grounded=True, expect_gold_document="a.md")],
    )
    case_b = GoldenCase(
        id="b",
        case_class=CaseClass.UNANSWERABLE,
        corpus_files=[],
        turns=[GoldenTurn(query="q", expect_grounded=False)],
    )
    result_a = CaseResult(
        case=case_a,
        turns=[TurnResult(query="q", grounded=True, content="a", citations=[_citation("a.md")])],
    )
    result_b = CaseResult(
        case=case_b, turns=[TurnResult(query="q", grounded=False, content="refusal", citations=[])]
    )

    agg = aggregate([score_case(result_a), score_case(result_b)])

    assert agg.total_cases == 2
    assert agg.cases_ran_successfully == 2
    assert agg.refusal_correctness_rate == 1.0
    # Only case_a's turn had an expected document — case_b's is excluded,
    # not averaged in as a miss.
    assert agg.retrieval_hit_rate == 1.0
    assert agg.citation_recall_mean == 1.0
    assert agg.adversarial_safety_rate is None
