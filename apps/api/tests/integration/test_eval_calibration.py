"""A calibration-drift guard (issue #73): the configured
``retrieval_refusal_threshold`` must stay safely below the golden set's
real, weakest positive-match RRF score — if a future change to the
retrieval pipeline (RRF constant, MMR lambda, chunking) shifts real
scores enough that the *current* configured threshold would start
refusing a genuine golden-set answer, this test catches it, rather than
letting a stale calibrated number silently drift out of validity.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest
from evals.harness.calibrate import _collect_positive_scores
from evals.harness.schema import load_golden_set

from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.config import get_settings

pytestmark = pytest.mark.integration

_GOLDEN_DIR = Path(__file__).resolve().parents[4] / "evals" / "golden" / "v1"


async def test_the_configured_threshold_stays_below_every_real_golden_set_positive_score(
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    cases = load_golden_set(_GOLDEN_DIR)

    samples = await _collect_positive_scores(
        cases,
        bootstrap_pool=db_bootstrap_pool,
        worker_pool=worker_db_pool,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )

    assert samples, "the golden set must have at least one grounded turn to calibrate against"
    weakest = min(s.top_score for s in samples)
    threshold = get_settings().retrieval_refusal_threshold
    assert threshold < weakest, (
        f"configured threshold {threshold} is not safely below the weakest real "
        f"golden-set positive score {weakest} — rerun evals/harness/calibrate.py"
    )
