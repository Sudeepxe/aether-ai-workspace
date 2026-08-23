"""Issue #69's acceptance criterion: the harness runs a handful of real
cases end to end (real ingestion, real HTTP chat, real retrieval/Gate 1)
and its mechanical metrics compute correctly against that real output —
not mocked. The full golden set (issue #70) doesn't exist yet; this
proves the harness machinery itself against two minimal cases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest
import redis.asyncio as redis_asyncio
from asgi_lifespan import LifespanManager
from evals.harness.metrics import aggregate, score_case
from evals.harness.runner import run_golden_set
from evals.harness.schema import CaseClass, GoldenCase, GoldenTurn
from httpx import ASGITransport, AsyncClient

from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.config import get_settings

pytestmark = pytest.mark.integration


def _as_app_api_url(bootstrap_url: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://app_api:app-api-dev-only@{hostpart}"


@pytest.fixture()
async def app_client(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("AETHER_DATABASE_URL", _as_app_api_url(postgres_url))
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        app = create_app()
        async with (
            LifespanManager(app),
            AsyncClient(
                transport=ASGITransport(app=app), base_url="https://eval-harness"
            ) as client,
        ):
            yield client
    finally:
        get_settings.cache_clear()


async def test_the_harness_scores_a_real_answerable_and_a_real_unanswerable_case(
    app_client: AsyncClient,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    cases = [
        GoldenCase(
            id="smoke-answerable-refund",
            case_class=CaseClass.ANSWERABLE,
            corpus_files=["acme-refund-policy.md"],
            turns=[
                GoldenTurn(
                    query="How many days does Acme's refund window last?",
                    expect_grounded=True,
                    expect_gold_document="acme-refund-policy.md",
                    expect_gold_section_contains="Refund",
                )
            ],
        ),
        GoldenCase(
            id="smoke-unanswerable",
            case_class=CaseClass.UNANSWERABLE,
            corpus_files=[],
            turns=[
                GoldenTurn(query="What is the boiling point of tungsten?", expect_grounded=False)
            ],
        ),
    ]

    results = await run_golden_set(
        cases,
        client=app_client,
        bootstrap_pool=db_bootstrap_pool,
        worker_pool=worker_db_pool,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )
    assert [r.error for r in results] == [None, None], [r.error for r in results]

    answerable_result, unanswerable_result = results
    assert answerable_result.turns[0].grounded is True
    assert any(
        c["document_title"] == "acme-refund-policy.md" for c in answerable_result.turns[0].citations
    )
    assert unanswerable_result.turns[0].grounded is False
    assert unanswerable_result.turns[0].citations == []

    case_metrics = [score_case(r) for r in results]
    agg = aggregate(case_metrics)

    assert agg.cases_ran_successfully == 2
    assert agg.refusal_correctness_rate == 1.0
    assert agg.retrieval_hit_rate == 1.0
    assert agg.citation_precision_mean == 1.0
    assert agg.citation_recall_mean == 1.0
