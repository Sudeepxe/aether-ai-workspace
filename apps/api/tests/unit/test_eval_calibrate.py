from __future__ import annotations

import pytest
from evals.harness.calibrate import CalibrationSample, derive_threshold

pytestmark = pytest.mark.unit


def _sample(score: float, case_id: str = "case") -> CalibrationSample:
    return CalibrationSample(case_id=case_id, query="q", top_score=score)


def test_derive_threshold_is_half_the_weakest_positive_score() -> None:
    samples = [_sample(0.05), _sample(0.02), _sample(0.08)]
    assert derive_threshold(samples) == pytest.approx(0.01)


def test_derive_threshold_ignores_the_strongest_score() -> None:
    weak = [_sample(0.02)]
    strong = [_sample(0.02), _sample(100.0)]
    assert derive_threshold(weak) == derive_threshold(strong)


def test_derive_threshold_rejects_an_empty_sample_set() -> None:
    with pytest.raises(ValueError, match="zero positive samples"):
        derive_threshold([])
