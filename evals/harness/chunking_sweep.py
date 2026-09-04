"""Phase 6, priority 2 (docs/REMEDIATION_PLAN.md): chunking parameter
sweep against the v2 golden set — the one sweep in this phase that is
genuinely embedder-independent on the lexical leg (full-text search
ranks by real term matches regardless of what the vector leg's
embeddings mean), unlike the RRF/MMR sweep.

Each configuration is applied by monkey-patching
aether.app.ingestion.chunking's module-level constants for the duration
of one real ingestion, then restored — never a permanent source change.
This is safe because chunk_document() and its helpers read these names
as module globals at call time, not baked in at import time. A fresh
workspace is ingested per configuration (chunk boundaries genuinely
differ), then every golden query is scored the same way Phase 5's
run does, at the *current* RRF/MMR/k defaults, so only chunking varies.

Golden-set anchors need no changes between configurations: they are
resolved against whatever real chunk content exists after each
ingestion (retrieval_runner.build_anchor_index), not against fixed
chunk ids or boundaries — this is exactly the portability the anchor
design was built for.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import asyncpg

import aether.app.ingestion.chunking as chunking_module
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
class ChunkingConfig:
    label: str
    target_tokens: int
    hard_max_tokens: int
    overlap_ratio: float


CONFIGS = [
    ChunkingConfig(
        "baseline (current default)", target_tokens=512, hard_max_tokens=800, overlap_ratio=0.125
    ),
    ChunkingConfig("smaller chunks", target_tokens=256, hard_max_tokens=400, overlap_ratio=0.125),
    ChunkingConfig("larger chunks", target_tokens=768, hard_max_tokens=1000, overlap_ratio=0.125),
    ChunkingConfig(
        "baseline size, minimal overlap", target_tokens=512, hard_max_tokens=800, overlap_ratio=0.02
    ),
]


@contextlib.contextmanager
def _patched_chunking(config: ChunkingConfig) -> Iterator[None]:
    original = (
        chunking_module._TARGET_TOKENS,
        chunking_module._HARD_MAX_TOKENS,
        chunking_module._OVERLAP_RATIO,
    )
    chunking_module._TARGET_TOKENS = config.target_tokens
    chunking_module._HARD_MAX_TOKENS = config.hard_max_tokens
    chunking_module._OVERLAP_RATIO = config.overlap_ratio
    try:
        yield
    finally:
        (
            chunking_module._TARGET_TOKENS,
            chunking_module._HARD_MAX_TOKENS,
            chunking_module._OVERLAP_RATIO,
        ) = original


async def run_one_config(config: ChunkingConfig) -> dict[str, object]:
    settings = get_settings()
    queries = load_golden_queries(QUERIES_PATH)
    embedder = build_embedder(settings)

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

    try:
        with _patched_chunking(config):
            await ingest_v2_corpus(
                workspace_id=workspace_id,
                corpora_dir=CORPORA_DIR,
                bootstrap_pool=bootstrap_pool,
                worker_pool=worker_pool,
                object_storage=object_storage,
                clamav_endpoint=clamav_endpoint,
                embedder=embedder,
            )
        async with bootstrap_pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            counts = await conn.fetch(
                "SELECT count(c.id) AS n FROM documents d JOIN chunks c ON c.document_id = d.id "
                "WHERE d.workspace_id = $1",
                workspace_id,
            )
        total_chunks = counts[0]["n"]

        anchor_index = await build_anchor_index(
            workspace_id=workspace_id, queries=queries, bootstrap_pool=bootstrap_pool
        )
        unresolved = sum(1 for ids in anchor_index.values() if not ids)

        per_query = await run_retrieval_eval(
            queries,
            workspace_id=workspace_id,
            db_pool=db_pool,
            anchor_index=anchor_index,
            refusal_threshold=settings.retrieval_refusal_threshold,
            embedder=embedder,
        )
    finally:
        await bootstrap_pool.close()
        await worker_pool.close()
        await db_pool.close()

    agg = aggregate(per_query)
    return {
        "config": config.label,
        "total_chunks": total_chunks,
        "unresolved_anchors": unresolved,
        "answerable_recall_at_5": agg.answerable_recall_at_5,
        "answerable_recall_at_10": agg.answerable_recall_at_10,
        "answerable_mrr": agg.answerable_mrr,
        "answerable_ndcg_at_10": agg.answerable_ndcg_at_10,
        "near_miss_recall_at_10": agg.near_miss_recall_at_10,
        "near_miss_distractor_beats_truth_rate": agg.near_miss_distractor_beats_truth_rate,
    }


def _fmt(v: object) -> str:
    return "n/a" if v is None else (f"{v * 100:.1f}%" if isinstance(v, float) else str(v))


async def main() -> None:
    results = []
    for config in CONFIGS:
        print(
            f"running: {config.label} (target={config.target_tokens}, "
            f"hard_max={config.hard_max_tokens}, overlap={config.overlap_ratio})"
        )
        result = await run_one_config(config)
        results.append(result)
        print(
            f"  -> {result['total_chunks']} chunks, "
            f"unresolved anchors: {result['unresolved_anchors']}, "
            f"recall@5={_fmt(result['answerable_recall_at_5'])}, "
            f"recall@10={_fmt(result['answerable_recall_at_10'])}"
        )

    print("\n=== full comparison table ===")
    cols = [
        "config",
        "total_chunks",
        "unresolved_anchors",
        "answerable_recall_at_5",
        "answerable_recall_at_10",
        "answerable_mrr",
        "answerable_ndcg_at_10",
        "near_miss_recall_at_10",
        "near_miss_distractor_beats_truth_rate",
    ]
    print(" | ".join(cols))
    for r in results:
        print(" | ".join(_fmt(r[c]) for c in cols))


if __name__ == "__main__":
    asyncio.run(main())
