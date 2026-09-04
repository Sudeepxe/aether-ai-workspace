"""Entry point: ``PYTHONPATH=../.. uv run python -m evals.harness.retrieval_cli run``
from ``apps/api`` — same connection pattern as cli.py (real Postgres/
Redis/MinIO/ClamAV via the standard AETHER_* env vars, not testcontainers),
against a running dev stack (``make dev``). Ingests evals/corpora/v2/ into
one fresh, shared workspace, runs every query in evals/golden/v2/queries.json
directly through HybridSearch, and prints real ranking metrics.

Deliberately does NOT exit non-zero on a low score: this is Phase 5's job
(establish a real, honest v2 baseline), not a regression gate — unlike
cli.py's v1 golden set, there is no verified "should be 100%" baseline
for these numbers yet to regress against. Wiring this into eval-smoke/
eval-nightly as an actual gate is a follow-up decision, not assumed here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

import asyncpg

from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.pool import _init_connection
from aether.config import get_settings
from aether.embedding_selection import build_embedder
from evals.harness.retrieval_metrics import AggregateRetrievalMetrics, QueryMetrics, aggregate
from evals.harness.retrieval_runner import (
    build_anchor_index,
    ingest_v2_corpus,
    new_eval_workspace_id,
    run_retrieval_eval,
)
from evals.harness.retrieval_schema import QueryClass, load_golden_queries

DEFAULT_CORPORA_DIR = Path(__file__).resolve().parents[1] / "corpora" / "v2"
DEFAULT_QUERIES_PATH = Path(__file__).resolve().parents[1] / "golden" / "v2" / "queries.json"


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _print_summary(
    agg: AggregateRetrievalMetrics, anchor_index: dict[str, list[UUID]], *, embedder_label: str
) -> None:
    multi_match = {a: ids for a, ids in anchor_index.items() if len(ids) > 1}
    print("")
    print(f"embedder: {embedder_label}")
    print(f"total queries: {agg.total_queries}")
    print(f"anchors resolving to >1 real chunk (legitimate chunking overlap): {len(multi_match)}")
    print("")
    print("--- answerable ---")
    print(f"recall@5:   {_fmt(agg.answerable_recall_at_5)}")
    print(f"recall@10:  {_fmt(agg.answerable_recall_at_10)}")
    print(f"MRR:        {_fmt(agg.answerable_mrr)}")
    print(f"NDCG@10:    {_fmt(agg.answerable_ndcg_at_10)}")
    print("")
    print("--- near-miss ---")
    print(f"recall@10:                 {_fmt(agg.near_miss_recall_at_10)}")
    print(f"distractor beats truth:    {_fmt(agg.near_miss_distractor_beats_truth_rate)}")
    print("")
    print("--- unanswerable, populated KB ---")
    print(f"refusal-threshold correct: {_fmt(agg.unanswerable_refusal_correct_rate)}")


def _write_report_json(
    path: Path,
    *,
    per_query: list[QueryMetrics],
    agg: AggregateRetrievalMetrics,
    embedder_label: str,
) -> None:
    payload = {
        "embedder": embedder_label,
        "per_query": [asdict(m) | {"case_class": m.case_class.value} for m in per_query],
        "aggregate": asdict(agg),
    }
    path.write_text(json.dumps(payload, indent=2, default=str))


async def _run(
    corpora_dir: Path, queries_path: Path, report_json: Path | None, verbose: bool
) -> int:
    settings = get_settings()
    queries = load_golden_queries(queries_path)
    if not queries:
        print(f"no golden queries found at {queries_path}", file=sys.stderr)
        return 1
    print(
        f"loaded {len(queries)} queries: "
        f"{sum(1 for q in queries if q.case_class is QueryClass.ANSWERABLE)} answerable, "
        f"{sum(1 for q in queries if q.case_class is QueryClass.NEAR_MISS)} near-miss, "
        f"{sum(1 for q in queries if q.case_class is QueryClass.UNANSWERABLE_POPULATED)} "
        "unanswerable-populated"
    )

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
    # Built once here and threaded through both ingestion and query-time
    # embedding below — the same instance, not just the same class, so
    # the two can never silently diverge. Its own model/embedding_version
    # is what gets printed/reported, never a hardcoded string.
    embedder = build_embedder(settings)
    print(f"embedder: {embedder.model} (embedding_version={embedder.embedding_version})")

    try:
        await ingest_v2_corpus(
            workspace_id=workspace_id,
            corpora_dir=corpora_dir,
            bootstrap_pool=bootstrap_pool,
            worker_pool=worker_pool,
            object_storage=object_storage,
            clamav_endpoint=clamav_endpoint,
            embedder=embedder,
            log=print,
        )
        async with bootstrap_pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            counts = await conn.fetch(
                "SELECT d.filename, count(c.id) AS n FROM documents d "
                "JOIN chunks c ON c.document_id = d.id WHERE d.workspace_id = $1 "
                "GROUP BY d.filename ORDER BY d.filename",
                workspace_id,
            )
        print("\nreal chunk-count distribution:")
        total_chunks = 0
        for row in counts:
            print(f"  {row['filename']}: {row['n']} chunks")
            total_chunks += row["n"]
        print(f"  TOTAL: {total_chunks} chunks")

        anchor_index = await build_anchor_index(
            workspace_id=workspace_id, queries=queries, bootstrap_pool=bootstrap_pool
        )
        unresolved = [a for a, ids in anchor_index.items() if not ids]
        if unresolved:
            print(f"\n{len(unresolved)} anchor(s) matched NO real chunk:", file=sys.stderr)
            for a in unresolved:
                print(f"  {a[:80]!r}", file=sys.stderr)

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

    embedder_label = f"{embedder.model} (embedding_version={embedder.embedding_version})"
    agg = aggregate(per_query)
    _print_summary(agg, anchor_index, embedder_label=embedder_label)

    if verbose:
        print("\n--- per-query detail ---")
        for m in per_query:
            print(
                f"{m.query_id} [{m.case_class.value}] "
                f"recall@5={m.recall_at_5} recall@10={m.recall_at_10} mrr={m.mrr} "
                f"ndcg@10={m.ndcg_at_10} top_score={m.top_fused_score:.5f} "
                f"would_refuse={m.would_refuse} distractor_rank={m.distractor_rank} "
                f"relevant_ranks={m.relevant_ranks}"
            )

    if report_json is not None:
        _write_report_json(report_json, per_query=per_query, agg=agg, embedder_label=embedder_label)
        print(f"\nwrote {report_json}")

    return 1 if unresolved else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Aether's v2 retrieval-ranking eval")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--corpora-dir", type=Path, default=DEFAULT_CORPORA_DIR)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES_PATH)
    parser.add_argument("--report-json", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    exit_code = asyncio.run(_run(args.corpora_dir, args.queries, args.report_json, args.verbose))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
