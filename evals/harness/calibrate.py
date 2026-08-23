"""Retrieval threshold calibration (ADR-6.4, issue #73) — a real,
data-driven derivation from the golden set's actual retrieval score
distribution. No LLM judge needed: this is pure statistics over real
hybrid-retrieval output.

Honest scope (documented here, not hidden): this environment's embedder
is ``LocalHashEmbeddingAdapter`` (non-semantic, hash-based) — the
calibrated threshold below is correct *for this embedder*, not a
universal constant. ADR-6.4 anticipates exactly this: "threshold is a
calibrated artifact versioned alongside embedding_version, recalibrated
as part of the... embedding migration procedure." A real embedding
migration must rerun this script, not reuse its output.

Also honest: v1's golden set (issue #70) has no "off-topic query against
a populated knowledge base" cases (documented in evals/golden/v1/README.md
— a single-chunk corpus makes the vector leg's rank-1-of-1 always clear
any realistic threshold, so that class of case isn't yet discriminating).
That means this calibration can't run a real precision/recall sweep
across positive and negative examples the way a full ROC analysis would —
it can only derive a *floor*: comfortably below the weakest real
positive match, definitively above zero. That's still a real, data-
derived number, replacing an admitted placeholder — not the full
statistical rigor a v2 golden set with negative-but-populated cases
would enable.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.chunk_search import PooledChunkSearch
from aether.adapters.postgres.pool import _init_connection
from aether.app.retrieval.hybrid_search import HybridSearch
from aether.config import get_settings
from evals.harness.corpus import ingest_corpus_files
from evals.harness.schema import GoldenCase, load_golden_set

_SAFETY_MARGIN = 0.5
"""The calibrated threshold is this fraction of the weakest observed
real positive-match score — comfortably below every known-good match in
the golden set (so a real answer is never refused), while still firmly
above zero (an empty or genuinely irrelevant retrieval, whose top score
is 0.0, is always refused). A fraction rather than a fixed offset scales
correctly if a future recalibration's score distribution shifts."""


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    case_id: str
    query: str
    top_score: float


async def _collect_positive_scores(
    cases: list[GoldenCase],
    *,
    bootstrap_pool: asyncpg.Pool,
    worker_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> list[CalibrationSample]:
    """One real fused RRF top-score per turn expected to ground — a
    fresh ingested workspace per case, direct HybridSearch call (not
    through the chat HTTP surface, which doesn't expose the raw score at
    all — only the boolean Gate 1 outcome)."""
    hybrid_search = HybridSearch(
        chunk_search=PooledChunkSearch(bootstrap_pool), embedder=LocalHashEmbeddingAdapter()
    )
    samples: list[CalibrationSample] = []
    for case in cases:
        if not case.corpus_files:
            continue
        workspace_id = uuid.uuid4()
        async with bootstrap_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO workspaces (id, name, slug) VALUES ($1, $2, $3)",
                workspace_id,
                f"calibration-{case.id}",
                f"calibration-{workspace_id}",
            )
        await ingest_corpus_files(
            workspace_id=workspace_id,
            filenames=case.corpus_files,
            bootstrap_pool=bootstrap_pool,
            worker_pool=worker_pool,
            object_storage=object_storage,
            clamav_endpoint=clamav_endpoint,
        )
        for turn in case.turns:
            if not turn.expect_grounded:
                continue
            result = await hybrid_search.search(workspace_id, query=turn.query)
            top_score = result.chunks[0].fused_score if result.chunks else 0.0
            samples.append(
                CalibrationSample(case_id=case.id, query=turn.query, top_score=top_score)
            )
    return samples


def derive_threshold(samples: list[CalibrationSample]) -> float:
    if not samples:
        raise ValueError("cannot calibrate a threshold with zero positive samples")
    weakest = min(s.top_score for s in samples)
    return weakest * _SAFETY_MARGIN


async def _main() -> None:
    settings = get_settings()
    golden_dir = Path(__file__).resolve().parents[1] / "golden" / "v1"
    cases = load_golden_set(golden_dir)

    bootstrap_pool = await asyncpg.create_pool(
        settings.database_migrator_url, min_size=1, max_size=4, init=_init_connection
    )
    worker_pool = await asyncpg.create_pool(
        settings.database_worker_url, min_size=1, max_size=4, init=_init_connection
    )
    object_storage = MinioObjectStorage(
        endpoint=settings.object_storage_endpoint,
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        secure=settings.object_storage_secure,
        bucket=settings.object_storage_bucket,
    )
    clamav_endpoint = (settings.clamav_host, settings.clamav_port)

    try:
        samples = await _collect_positive_scores(
            cases,
            bootstrap_pool=bootstrap_pool,
            worker_pool=worker_pool,
            object_storage=object_storage,
            clamav_endpoint=clamav_endpoint,
        )
    finally:
        await bootstrap_pool.close()
        await worker_pool.close()

    scores = sorted(s.top_score for s in samples)
    threshold = derive_threshold(samples)
    print(
        f"embedder: {LocalHashEmbeddingAdapter.model} (embedding_version={LocalHashEmbeddingAdapter.embedding_version})"
    )
    print(f"positive samples: {len(samples)}")
    print(
        f"score range: min={scores[0]:.4f} max={scores[-1]:.4f} mean={sum(scores) / len(scores):.4f}"
    )
    print(f"calibrated threshold ({_SAFETY_MARGIN:.0%} of weakest positive): {threshold:.4f}")


if __name__ == "__main__":
    asyncio.run(_main())
