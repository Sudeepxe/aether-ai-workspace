"""Drives the v2 golden query set directly against HybridSearch (Phase 5,
docs/REMEDIATION_PLAN.md) — no HTTP layer, no LLM, no chat turn. This is
deliberately lower-level than runner.py's real-HTTP-surface approach:
ranking metrics need the raw ranked chunk list HybridSearch.search()
returns, which the chat API never exposes (it only ever returns
citations the model chose to surface). Ingestion still goes through the
real pipeline (corpus.py, reused as-is) — only the query side skips
the HTTP/chat layer.
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from uuid import UUID

import asyncpg

from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.postgres.chunk_search import PooledChunkSearch
from aether.app.retrieval.hybrid_search import HybridSearch
from evals.harness.corpus import ingest_corpus_files
from evals.harness.retrieval_metrics import QueryMetrics, RankedResult, score_query
from evals.harness.retrieval_schema import GoldenQuery

_RETRIEVAL_K = 10
"""Upper bound on how many ranked results we ask HybridSearch for — the
highest k any of the golden set's recall@k cutoffs need. Bounded above
by HybridSearch's own _MMR_CANDIDATE_POOL (20), so this is safely within
range without needing to touch retrieval internals."""


async def ingest_v2_corpus(
    *,
    workspace_id: UUID,
    corpora_dir: Any,
    bootstrap_pool: asyncpg.Pool,
    worker_pool: asyncpg.Pool,
    object_storage: Any,
    clamav_endpoint: tuple[str, int],
    log: Any = None,
) -> None:
    """Ingests every .md file under corpora_dir into one shared
    workspace — deliberately one populated workspace for the whole
    golden set, not per-query isolation like v1's per-case workspaces:
    near-miss and unanswerable_populated queries specifically need to
    see the real, full, competing corpus, not an artificially narrowed
    one."""
    filenames = sorted(p.name for p in corpora_dir.glob("*.md"))
    if not filenames:
        raise RuntimeError(f"no corpus files found under {corpora_dir}")
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'eval-v2-retrieval', $2)",
            workspace_id,
            f"eval-v2-retrieval-{workspace_id}",
        )
    for filename in filenames:
        started = time.monotonic()
        await ingest_corpus_files(
            workspace_id=workspace_id,
            filenames=[filename],
            bootstrap_pool=bootstrap_pool,
            worker_pool=worker_pool,
            object_storage=object_storage,
            clamav_endpoint=clamav_endpoint,
            corpora_dir=corpora_dir,
        )
        if log is not None:
            log(f"ingested {filename} ({time.monotonic() - started:.1f}s)")


async def build_anchor_index(
    *, workspace_id: UUID, queries: list[GoldenQuery], bootstrap_pool: asyncpg.Pool
) -> dict[str, list[UUID]]:
    """Resolves every anchor phrase in the golden set against the real,
    just-ingested chunk content — not a hardcoded chunk id anywhere.
    An anchor matching more than one chunk is legitimate (the chunker's
    real 10-15% overlap can duplicate a sentence across two adjacent
    chunks) and is not treated as an error here; retrieval_cli.py
    reports the count so it stays visible, not hidden."""
    async with bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        rows = await conn.fetch(
            "SELECT c.id, c.content FROM documents d JOIN chunks c ON c.document_id = d.id "
            "WHERE d.workspace_id = $1",
            workspace_id,
        )
    chunks = [(row["id"], row["content"]) for row in rows]

    anchors: set[str] = set()
    for q in queries:
        anchors.update(rel.anchor for rel in q.relevant_chunks)
        if q.distractor_anchor:
            anchors.add(q.distractor_anchor)

    return {
        anchor: [chunk_id for chunk_id, content in chunks if anchor in content]
        for anchor in anchors
    }


async def run_retrieval_eval(
    queries: list[GoldenQuery],
    *,
    workspace_id: UUID,
    db_pool: asyncpg.Pool,
    anchor_index: dict[str, list[UUID]],
    refusal_threshold: float,
) -> list[QueryMetrics]:
    """Runs every golden query directly through a real HybridSearch
    instance (real PooledChunkSearch against Postgres, real RRF/MMR,
    the real LocalHashEmbeddingAdapter this dev environment actually
    uses — no provider key is configured here, so this measures the
    real pipeline honestly, not a semantic embedding model's quality)."""
    chunk_search = PooledChunkSearch(db_pool)
    embedder = LocalHashEmbeddingAdapter()
    hybrid_search = HybridSearch(chunk_search=chunk_search, embedder=embedder)

    results = []
    for query in queries:
        # No explicit set_config here: PooledChunkSearch.search_vector/
        # search_lexical each acquire their own connection and set
        # app.tenant_id on it per call — nothing to do at this level.
        retrieval = await hybrid_search.search(workspace_id, query=query.query, k=_RETRIEVAL_K)
        ranked = [
            RankedResult(chunk_id=c.chunk_id, fused_score=c.fused_score) for c in retrieval.chunks
        ]
        results.append(
            score_query(
                query,
                ranked,
                anchor_index=anchor_index,
                refusal_threshold=refusal_threshold,
            )
        )
    return results


def new_eval_workspace_id() -> UUID:
    return uuid.uuid4()
