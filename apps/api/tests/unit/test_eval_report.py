from __future__ import annotations

import pytest
from evals.harness.judge import FaithfulnessStatus, FaithfulnessVerdict
from evals.harness.metrics import AggregateMetrics, CaseMetrics, TurnMetrics
from evals.harness.report import render_report
from evals.harness.schema import CaseClass

pytestmark = pytest.mark.unit


def _agg(**overrides: object) -> AggregateMetrics:
    defaults: dict[str, object] = {
        "total_cases": 1,
        "cases_ran_successfully": 1,
        "refusal_correctness_rate": 1.0,
        "retrieval_hit_rate": 1.0,
        "citation_precision_mean": 1.0,
        "citation_recall_mean": 1.0,
        "adversarial_safety_rate": None,
    }
    defaults.update(overrides)
    return AggregateMetrics(**defaults)  # type: ignore[arg-type]


def _case(case_id: str = "case-1") -> CaseMetrics:
    return CaseMetrics(
        case_id=case_id,
        case_class=CaseClass.ANSWERABLE,
        ran_successfully=True,
        error=None,
        turns=[
            TurnMetrics(
                refusal_correct=True,
                retrieval_hit=True,
                citation_precision=1.0,
                citation_recall=1.0,
                adversarial_safe=None,
            )
        ],
    )


def test_north_star_is_reported_as_not_yet_determinable_without_faithfulness_data() -> None:
    report = render_report(
        agg=_agg(), case_metrics=[_case()], faithfulness=[], generated_at="2026-01-01T00:00:00Z"
    )
    assert "Not yet determinable" in report
    assert "no LLM provider key configured" in report


def test_north_star_reports_met_when_both_targets_clear_the_bar() -> None:
    faithfulness = [
        FaithfulnessVerdict(
            status=FaithfulnessStatus.MEASURED, faithful=True, reasoning="ok", judge_model="m"
        )
        for _ in range(10)
    ]
    report = render_report(
        agg=_agg(refusal_correctness_rate=1.0),
        case_metrics=[_case()],
        faithfulness=faithfulness,
        generated_at="2026-01-01T00:00:00Z",
    )
    assert "**met**" in report
    assert "NOT met" not in report


def test_north_star_reports_not_met_when_faithfulness_is_below_target() -> None:
    faithfulness = [
        FaithfulnessVerdict(
            status=FaithfulnessStatus.MEASURED,
            faithful=(i < 5),
            reasoning="ok",
            judge_model="m",
        )
        for i in range(10)
    ]
    report = render_report(
        agg=_agg(refusal_correctness_rate=1.0),
        case_metrics=[_case()],
        faithfulness=faithfulness,
        generated_at="2026-01-01T00:00:00Z",
    )
    assert "NOT met" in report


def test_adversarial_safety_explains_the_echo_generator_limitation_when_not_applicable() -> None:
    report = render_report(
        agg=_agg(adversarial_safety_rate=None),
        case_metrics=[_case()],
        faithfulness=[],
        generated_at="2026-01-01T00:00:00Z",
    )
    assert "Not applicable under EchoGenerator" in report


def test_known_gaps_section_always_present() -> None:
    report = render_report(
        agg=_agg(), case_metrics=[_case()], faithfulness=[], generated_at="2026-01-01T00:00:00Z"
    )
    assert "Known gaps" in report
    assert "No real LLM provider key" in report


def test_a_failed_case_is_reported_with_its_error_not_silently_dropped() -> None:
    failed_case = CaseMetrics(
        case_id="broken-case",
        case_class=CaseClass.UNANSWERABLE,
        ran_successfully=False,
        error="RuntimeError: fixture missing",
        turns=[],
    )
    report = render_report(
        agg=_agg(cases_ran_successfully=0),
        case_metrics=[failed_case],
        faithfulness=[],
        generated_at="2026-01-01T00:00:00Z",
    )
    assert "broken-case" in report
    assert "RuntimeError: fixture missing" in report
