"""Entry point: ``PYTHONPATH=../.. uv run python -m evals.harness.cli run``
from ``apps/api`` (or via ``make eval`` once issue #72 adds it) —
``PYTHONPATH=../..`` puts the repo root on the path so ``evals.*``
resolves, while ``aether.*`` resolves from apps/api's own venv as
usual. Connects to whatever real Postgres/Redis/MinIO/ClamAV the
standard ``AETHER_*`` env vars point at (same variables/defaults
``aether.config.Settings`` already reads everywhere else), runs the
golden set through the real HTTP app in-process, and prints a summary.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.pool import _init_connection
from aether.config import get_settings
from evals.harness.metrics import AggregateMetrics, aggregate, score_case
from evals.harness.runner import run_golden_set
from evals.harness.schema import load_golden_set

DEFAULT_GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "v1"


def _print_summary(agg: AggregateMetrics) -> None:
    def fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v * 100:.1f}%"

    print("")
    print(f"cases: {agg.cases_ran_successfully}/{agg.total_cases} ran successfully")
    print(f"refusal correctness:   {fmt(agg.refusal_correctness_rate)}")
    print(f"retrieval hit rate:    {fmt(agg.retrieval_hit_rate)}")
    print(f"citation precision:    {fmt(agg.citation_precision_mean)}")
    print(f"citation recall:       {fmt(agg.citation_recall_mean)}")
    print(f"adversarial safety:    {fmt(agg.adversarial_safety_rate)}")
    print("faithfulness:          not measured (no LLM judge wired — issue #71)")


async def _run(golden_dir: Path) -> int:
    settings = get_settings()
    cases = load_golden_set(golden_dir)
    if not cases:
        print(f"no golden cases found under {golden_dir}", file=sys.stderr)
        return 1

    # asyncpg.create_pool directly (not adapters.postgres.pool.create_pool,
    # which is single-URL) since the harness needs two distinct roles —
    # same pattern apps/api/tests/integration/conftest.py's
    # db_bootstrap_pool/worker_db_pool fixtures already use. init=
    # _init_connection registers the pgvector codec — without it, a
    # chunk's embedding (a Python list) can't bind to the `vector` column
    # asyncpg sees.
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
        from aether.http.app import create_app

        app = create_app()
        async with (
            LifespanManager(app),
            AsyncClient(
                transport=ASGITransport(app=app), base_url="https://eval-harness"
            ) as client,
        ):
            results = await run_golden_set(
                cases,
                client=client,
                bootstrap_pool=bootstrap_pool,
                worker_pool=worker_pool,
                object_storage=object_storage,
                clamav_endpoint=clamav_endpoint,
                log=print,
            )
    finally:
        await bootstrap_pool.close()
        await worker_pool.close()

    case_metrics = [score_case(r) for r in results]
    agg = aggregate(case_metrics)
    _print_summary(agg)

    failed = [c for c in case_metrics if not c.ran_successfully]
    if failed:
        print(f"\n{len(failed)} case(s) failed to run:", file=sys.stderr)
        for c in failed:
            print(f"  {c.case_id}: {c.error}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Aether eval harness golden set")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    args = parser.parse_args()
    exit_code = asyncio.run(_run(args.golden_dir))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
