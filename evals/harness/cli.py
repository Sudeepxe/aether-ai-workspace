"""Entry point: ``PYTHONPATH=../.. uv run python -m evals.harness.cli run``
from ``apps/api`` (or via ``make eval``/CI's eval-smoke and eval-nightly
jobs, issue #72) — ``PYTHONPATH=../..`` puts the repo root on the path
so ``evals.*`` resolves, while ``aether.*`` resolves from apps/api's own
venv as usual. Connects to whatever real Postgres/Redis/MinIO/ClamAV the
standard ``AETHER_*`` env vars point at (same variables/defaults
``aether.config.Settings`` already reads everywhere else), runs the
golden set through the real HTTP app in-process, and prints a summary.

Exit code gates on regression (issue #72's "gates merge on regression"):
this golden set's real, verified baseline (issue #70) is 100% on every
mechanical metric — refusal correctness, retrieval hit-rate, citation
precision, citation recall. Any drop below that on *this* set is a real
regression, not noise, so the default exit code reflects it. Faithfulness/
adversarial-safety never gate (they're honestly not measured in this
environment) and neither does any future golden-set growth that isn't
yet at 100% by design — this threshold is meaningful specifically
because v1's cases were built to have a clean, no-ambiguity answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import asyncpg
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.pool import _init_connection
from aether.config import get_settings
from evals.harness.judge import FaithfulnessStatus, FaithfulnessVerdict, judge_case_faithfulness
from evals.harness.metrics import AggregateMetrics, CaseMetrics, aggregate, score_case
from evals.harness.runner import run_golden_set
from evals.harness.schema import load_golden_set

DEFAULT_GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "v1"
_REQUIRED_RATE = 1.0


def _print_summary(agg: AggregateMetrics, faithfulness: list[FaithfulnessVerdict]) -> None:
    def fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v * 100:.1f}%"

    print("")
    print(f"cases: {agg.cases_ran_successfully}/{agg.total_cases} ran successfully")
    print(f"refusal correctness:   {fmt(agg.refusal_correctness_rate)}")
    print(f"retrieval hit rate:    {fmt(agg.retrieval_hit_rate)}")
    print(f"citation precision:    {fmt(agg.citation_precision_mean)}")
    print(f"citation recall:       {fmt(agg.citation_recall_mean)}")
    print(f"adversarial safety:    {fmt(agg.adversarial_safety_rate)}")
    print(f"faithfulness:          {_format_faithfulness(faithfulness)}")


def _format_faithfulness(verdicts: list[FaithfulnessVerdict]) -> str:
    measured = [v for v in verdicts if v.status == FaithfulnessStatus.MEASURED]
    if measured:
        rate = sum(1 for v in measured if v.faithful) / len(measured)
        return f"{rate * 100:.1f}% ({len(measured)} turn(s) judged)"
    not_measured = sum(1 for v in verdicts if v.status == FaithfulnessStatus.NOT_MEASURED)
    skipped = sum(1 for v in verdicts if v.status == FaithfulnessStatus.SKIPPED_SAME_FAMILY)
    if skipped:
        return f"not measured — only a same-family judge available ({skipped} turn(s) skipped)"
    if not_measured:
        return "not measured — no LLM provider key configured in this environment"
    return "not measured — no grounded turns to judge"


def _regressions(agg: AggregateMetrics) -> list[str]:
    problems = []
    if agg.refusal_correctness_rate < _REQUIRED_RATE:
        problems.append(f"refusal correctness {agg.refusal_correctness_rate:.1%} < 100%")
    if agg.retrieval_hit_rate is not None and agg.retrieval_hit_rate < _REQUIRED_RATE:
        problems.append(f"retrieval hit rate {agg.retrieval_hit_rate:.1%} < 100%")
    if agg.citation_precision_mean is not None and agg.citation_precision_mean < _REQUIRED_RATE:
        problems.append(f"citation precision {agg.citation_precision_mean:.1%} < 100%")
    if agg.citation_recall_mean is not None and agg.citation_recall_mean < _REQUIRED_RATE:
        problems.append(f"citation recall {agg.citation_recall_mean:.1%} < 100%")
    return problems


def _write_report_json(
    path: Path,
    *,
    case_metrics: list[CaseMetrics],
    agg: AggregateMetrics,
    faithfulness: list[FaithfulnessVerdict],
) -> None:
    payload = {
        "cases": [asdict(c) for c in case_metrics],
        "aggregate": asdict(agg),
        "faithfulness": [asdict(v) for v in faithfulness],
    }
    path.write_text(json.dumps(payload, indent=2, default=str))


async def _run(golden_dir: Path, report_json: Path | None) -> int:
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
            faithfulness: list[FaithfulnessVerdict] = []
            for result in results:
                faithfulness.extend(
                    await judge_case_faithfulness(
                        result, bootstrap_pool=bootstrap_pool, settings=settings
                    )
                )
    finally:
        await bootstrap_pool.close()
        await worker_pool.close()

    case_metrics = [score_case(r) for r in results]
    agg = aggregate(case_metrics)
    _print_summary(agg, faithfulness)
    if report_json is not None:
        _write_report_json(
            report_json, case_metrics=case_metrics, agg=agg, faithfulness=faithfulness
        )
        print(f"\nwrote {report_json}")

    exit_code = 0
    failed = [c for c in case_metrics if not c.ran_successfully]
    if failed:
        print(f"\n{len(failed)} case(s) failed to run:", file=sys.stderr)
        for c in failed:
            print(f"  {c.case_id}: {c.error}", file=sys.stderr)
        exit_code = 1

    regressions = _regressions(agg)
    if regressions:
        print("\nregression(s) against this golden set's verified baseline:", file=sys.stderr)
        for r in regressions:
            print(f"  {r}", file=sys.stderr)
        exit_code = 1

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Aether eval harness golden set")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    parser.add_argument(
        "--report-json",
        type=Path,
        default=None,
        help="write a machine-readable report to this path",
    )
    args = parser.parse_args()
    exit_code = asyncio.run(_run(args.golden_dir, args.report_json))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
