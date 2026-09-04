"""Phase 6, priority 1 (docs/REMEDIATION_PLAN.md): real precision/recall
trade-off across candidate refusal thresholds, derived from the v2
golden set's real fused RRF scores — not a single chosen value asserted
without showing its cost.

Label convention for this analysis: "positive" means Gate 1 should NOT
refuse (a real answer exists and generation should proceed) —
`answerable` and `near_miss` queries both belong here, since a near-miss
query's correct chunk genuinely is in the corpus even though a
distractor might outrank it; ranking quality is near-miss's own
concern, not Gate 1's. "Negative" means Gate 1 SHOULD refuse:
`unanswerable_populated` queries only.

Ingests once (chunking/RRF/MMR held at their current defaults — this
analysis is only about where to draw the score cutoff, not about the
retrieval parameters that produce the score), runs all 53 queries, then
sweeps every distinct observed top_fused_score as a candidate threshold
(the standard way to get an exact precision/recall curve with no
resolution loss from an arbitrary grid).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.pool import _init_connection
from aether.config import get_settings
from aether.embedding_selection import build_embedder
from evals.harness.retrieval_runner import (
    build_anchor_index,
    ingest_v2_corpus,
    new_eval_workspace_id,
    run_retrieval_eval,
)
from evals.harness.retrieval_schema import QueryClass, load_golden_queries

CORPORA_DIR = Path(__file__).resolve().parents[1] / "corpora" / "v2"
QUERIES_PATH = Path(__file__).resolve().parents[1] / "golden" / "v2" / "queries.json"
_POSITIVE_CLASSES = {QueryClass.ANSWERABLE, QueryClass.NEAR_MISS}


@dataclass(frozen=True, slots=True)
class ThresholdPoint:
    threshold: float
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int

    @property
    def precision(self) -> float | None:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else None

    @property
    def specificity(self) -> float | None:
        denom = self.true_negative + self.false_positive
        return self.true_negative / denom if denom else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)


def sweep_thresholds(scores: list[tuple[float, bool]]) -> list[ThresholdPoint]:
    """``scores`` is (top_fused_score, is_positive) per query. A
    candidate threshold t means: "score >= t proceeds to generation,
    score < t refuses" — matching RetrievalGate's real `<` comparison
    (config.py's retrieval_refusal_threshold docstring/router.py usage)."""
    candidates = sorted({s for s, _ in scores})
    # Include one point just above the max so "refuse everything" is a
    # representable candidate, and rely on the smallest real score as
    # the floor for "let everything through".
    candidates = [*candidates, candidates[-1] + 1e-6]
    points = []
    for t in candidates:
        tp = sum(1 for s, pos in scores if pos and s >= t)
        fp = sum(1 for s, pos in scores if not pos and s >= t)
        tn = sum(1 for s, pos in scores if not pos and s < t)
        fn = sum(1 for s, pos in scores if pos and s < t)
        points.append(
            ThresholdPoint(
                threshold=t,
                true_positive=tp,
                false_positive=fp,
                true_negative=tn,
                false_negative=fn,
            )
        )
    return points


async def _collect_scores() -> tuple[list[tuple[float, bool, str]], asyncpg.Pool]:
    settings = get_settings()
    queries = load_golden_queries(QUERIES_PATH)
    embedder = build_embedder(settings)
    print(f"embedder: {embedder.model} (embedding_version={embedder.embedding_version})")

    bootstrap_pool = await asyncpg.create_pool(
        settings.database_migrator_url, min_size=1, max_size=4, init=_init_connection
    )
    worker_pool = await asyncpg.create_pool(
        settings.database_worker_url, min_size=1, max_size=4, init=_init_connection
    )
    db_pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=4, init=_init_connection
    )
    object_storage = MinioObjectStorage(
        endpoint=settings.object_storage_endpoint,
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        secure=settings.object_storage_secure,
        bucket=settings.object_storage_bucket,
    )
    clamav_endpoint = (settings.clamav_host, settings.clamav_port)
    workspace_id = new_eval_workspace_id()

    await ingest_v2_corpus(
        workspace_id=workspace_id,
        corpora_dir=CORPORA_DIR,
        bootstrap_pool=bootstrap_pool,
        worker_pool=worker_pool,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
        embedder=embedder,
    )
    anchor_index = await build_anchor_index(
        workspace_id=workspace_id, queries=queries, bootstrap_pool=bootstrap_pool
    )
    per_query = await run_retrieval_eval(
        queries,
        workspace_id=workspace_id,
        db_pool=db_pool,
        anchor_index=anchor_index,
        refusal_threshold=settings.retrieval_refusal_threshold,
        embedder=embedder,
    )
    await worker_pool.close()
    await db_pool.close()

    scores = [
        (m.top_fused_score, m.case_class in _POSITIVE_CLASSES, m.case_class.value)
        for m in per_query
    ]
    return scores, bootstrap_pool


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


async def main() -> None:
    scores_with_class, bootstrap_pool = await _collect_scores()
    try:
        scores = [(s, pos) for s, pos, _ in scores_with_class]
        positives = sum(1 for _, pos in scores if pos)
        negatives = len(scores) - positives
        print(
            f"\n{len(scores)} queries: {positives} positive (answerable+near_miss), "
            f"{negatives} negative (unanswerable_populated)"
        )

        current_threshold = get_settings().retrieval_refusal_threshold
        points = sweep_thresholds(scores)

        print("\n--- full precision/recall trade-off, every distinct observed score ---")
        print(
            f"{'threshold':>12} {'TP':>4} {'FP':>4} {'TN':>4} {'FN':>4} "
            f"{'precision':>10} {'recall':>8} {'specificity':>12} {'F1':>8}"
        )
        for p in points:
            marker = (
                " <-- current config.py default (0.0082)"
                if abs(p.threshold - current_threshold) < 1e-6
                else ""
            )
            print(
                f"{p.threshold:>12.6f} {p.true_positive:>4} {p.false_positive:>4} "
                f"{p.true_negative:>4} {p.false_negative:>4} "
                f"{_fmt(p.precision):>10} {_fmt(p.recall):>8} {_fmt(p.specificity):>12} "
                f"{_fmt(p.f1):>8}{marker}"
            )
    finally:
        await bootstrap_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
