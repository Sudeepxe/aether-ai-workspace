"""Phase 6, priorities 3 and 4 (docs/REMEDIATION_PLAN.md): k/candidate-
limit sweep and RRF-constant/MMR-lambda sweep against the v2 golden
set. Both are query-time-only parameters (they don't change what got
ingested or how chunks are bounded), so this ingests the baseline
corpus exactly once and re-runs retrieval per configuration against the
same already-ingested workspace — no re-ingestion needed between runs,
unlike chunking_sweep.py.

Constants are monkey-patched on the real modules for the duration of
each configuration, then restored — same mechanism and same "never a
permanent source change" posture as chunking_sweep.py.

Priority 4 (RRF/MMR) results are measured under this environment's real
constraint: LocalHashEmbeddingAdapter, non-semantic. The vector leg's
contribution to RRF fusion and to MMR's diversity penalty is dense
noise, not a real relevance signal, under this embedder — these results
say what happens to hybrid retrieval when one whole leg is noise, not
what these constants should be for a real deployment with a real
embedding model. Do not read them as provider-representative.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import asyncpg

import aether.app.retrieval.hybrid_search as hybrid_search_module
import evals.harness.retrieval_runner as retrieval_runner_module
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.pool import _init_connection
from aether.config import get_settings
from aether.embedding_selection import build_embedder
from evals.harness.retrieval_metrics import aggregate
from evals.harness.retrieval_runner import (
    build_anchor_index,
    ingest_v2_corpus,
    new_eval_workspace_id,
    run_retrieval_eval,
)
from evals.harness.retrieval_schema import load_golden_queries

CORPORA_DIR = Path(__file__).resolve().parents[1] / "corpora" / "v2"
QUERIES_PATH = Path(__file__).resolve().parents[1] / "golden" / "v2" / "queries.json"


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    label: str
    group: str  # "candidate_limits" or "rrf_mmr"
    vector_leg_limit: int = 20
    lexical_leg_limit: int = 20
    mmr_candidate_pool: int = 20
    retrieval_k: int = 10
    rrf_constant: int = 60
    mmr_lambda: float = 0.5


CONFIGS = [
    RetrievalConfig("baseline (current defaults)", group="candidate_limits"),
    RetrievalConfig(
        "tight candidate limits (5)",
        group="candidate_limits",
        vector_leg_limit=5,
        lexical_leg_limit=5,
        mmr_candidate_pool=5,
        retrieval_k=5,
    ),
    RetrievalConfig(
        "medium candidate limits (10)",
        group="candidate_limits",
        vector_leg_limit=10,
        lexical_leg_limit=10,
        mmr_candidate_pool=10,
        retrieval_k=10,
    ),
    RetrievalConfig("wider retrieval k (15)", group="candidate_limits", retrieval_k=15),
    RetrievalConfig("baseline (repeated for table grouping)", group="rrf_mmr"),
    RetrievalConfig("low RRF constant (10)", group="rrf_mmr", rrf_constant=10),
    RetrievalConfig("high RRF constant (100)", group="rrf_mmr", rrf_constant=100),
    RetrievalConfig("MMR pure relevance (lambda=1.0)", group="rrf_mmr", mmr_lambda=1.0),
    RetrievalConfig("MMR pure diversity (lambda=0.0)", group="rrf_mmr", mmr_lambda=0.0),
]


@contextlib.contextmanager
def _patched_retrieval(config: RetrievalConfig) -> Iterator[None]:
    hs = hybrid_search_module
    rr = retrieval_runner_module
    original = (
        hs._VECTOR_LEG_LIMIT,
        hs._LEXICAL_LEG_LIMIT,
        hs._MMR_CANDIDATE_POOL,
        hs._RRF_CONSTANT,
        hs._MMR_LAMBDA,
        rr._RETRIEVAL_K,
    )
    hs._VECTOR_LEG_LIMIT = config.vector_leg_limit
    hs._LEXICAL_LEG_LIMIT = config.lexical_leg_limit
    hs._MMR_CANDIDATE_POOL = config.mmr_candidate_pool
    hs._RRF_CONSTANT = config.rrf_constant
    hs._MMR_LAMBDA = config.mmr_lambda
    rr._RETRIEVAL_K = config.retrieval_k
    try:
        yield
    finally:
        (
            hs._VECTOR_LEG_LIMIT,
            hs._LEXICAL_LEG_LIMIT,
            hs._MMR_CANDIDATE_POOL,
            hs._RRF_CONSTANT,
            hs._MMR_LAMBDA,
            rr._RETRIEVAL_K,
        ) = original


def _fmt(v: object) -> str:
    return "n/a" if v is None else (f"{v * 100:.1f}%" if isinstance(v, float) else str(v))


async def main() -> None:
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

    results = []
    try:
        print("ingesting baseline corpus once (query-time params only vary below)...")
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

        for config in CONFIGS:
            with _patched_retrieval(config):
                per_query = await run_retrieval_eval(
                    queries,
                    workspace_id=workspace_id,
                    db_pool=db_pool,
                    anchor_index=anchor_index,
                    refusal_threshold=settings.retrieval_refusal_threshold,
                    embedder=embedder,
                )
            agg = aggregate(per_query)
            result = {
                "group": config.group,
                "config": config.label,
                "answerable_recall_at_5": agg.answerable_recall_at_5,
                "answerable_recall_at_10": agg.answerable_recall_at_10,
                "answerable_mrr": agg.answerable_mrr,
                "answerable_ndcg_at_10": agg.answerable_ndcg_at_10,
                "near_miss_recall_at_10": agg.near_miss_recall_at_10,
                "near_miss_distractor_beats_truth_rate": agg.near_miss_distractor_beats_truth_rate,
            }
            results.append(result)
            print(
                f"{config.label}: recall@5={_fmt(result['answerable_recall_at_5'])}, "
                f"recall@10={_fmt(result['answerable_recall_at_10'])}, "
                f"mrr={_fmt(result['answerable_mrr'])}"
            )
    finally:
        await bootstrap_pool.close()
        await worker_pool.close()
        await db_pool.close()

    cols = [
        "group",
        "config",
        "answerable_recall_at_5",
        "answerable_recall_at_10",
        "answerable_mrr",
        "answerable_ndcg_at_10",
        "near_miss_recall_at_10",
        "near_miss_distractor_beats_truth_rate",
    ]
    print("\n=== full comparison table ===")
    print(" | ".join(cols))
    for r in results:
        print(" | ".join(_fmt(r[c]) for c in cols))


if __name__ == "__main__":
    asyncio.run(main())
