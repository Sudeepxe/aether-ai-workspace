"""Phase 7, RUN 2 (docs/REMEDIATION_PLAN.md): the v2 golden set's 35
answerable and 10 unanswerable_populated queries, run through real
retrieval (HybridSearch, same as Phase 5/6) and real generation
(GroqCompletionAdapter, openai/gpt-oss-20b) — near_miss queries are
deliberately excluded, matching what was actually asked (ranking
quality against a distractor is near_miss's own concern, not Gate 2's).

The important measurement, stated up front rather than buried: Phase 6
established that Gate 1 (the retrieval-score threshold) cannot separate
answerable from unanswerable_populated queries at this corpus scale/
embedder — raising it breaks more real queries than it fixes. Gate 2
(the LLM's own system-prompt instruction to refuse when the context
doesn't answer the question) was, until this run, never actually tested
against a real model. This is that test.

Faithfulness, honestly: this environment has exactly one provider
family configured (Groq). evals/harness/judge.py's real
judge_faithfulness() is called for every answerable reply — not
bypassed or reimplemented — and is expected to (and, if it does,
genuinely does) return NOT_MEASURED, since ADR-6.5 refuses same-family
judging by design and no second provider family exists here to judge
against. This is NOT cross-family validation and is never described as
such. What IS reported for the 35 answerable queries: real generated
replies (spot-checked in the summary), and the false-refusal rate — a
real, mechanically-checkable, directly relevant number: how often Gate
2 incorrectly refuses a question that genuinely has an answer.

Requires AETHER_GROQ_API_KEY in the process environment. Never logs,
prints, or otherwise touches the key value itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import asyncpg

from aether.adapters.groq.completion import GroqCompletionAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.chunk_search import PooledChunkSearch
from aether.adapters.postgres.pool import _init_connection
from aether.app.llm.router import _build_messages
from aether.app.retrieval.hybrid_search import HybridSearch
from aether.config import get_settings
from aether.embedding_selection import build_embedder
from aether.ports.chat import NOT_IN_KNOWLEDGE_BASE_REPLY, RetrievedContext, RetrievedContextChunk
from aether.ports.llm import CompletionRequest
from evals.harness.judge import FaithfulnessStatus, judge_faithfulness
from evals.harness.live_provider import call_with_backoff_and_reasoning_retry
from evals.harness.retrieval_runner import ingest_v2_corpus, new_eval_workspace_id
from evals.harness.retrieval_schema import QueryClass, load_golden_queries

_MODEL = "openai/gpt-oss-20b"
_MAX_TOKENS = 1024
_FOLLOWUP_MAX_TOKENS = 4000
_RETRIEVAL_K = 10  # matches Phase 5/6's retrieval_runner._RETRIEVAL_K

CORPORA_DIR = Path(__file__).resolve().parents[1] / "corpora" / "v2"
QUERIES_PATH = Path(__file__).resolve().parents[1] / "golden" / "v2" / "queries.json"


@dataclass(frozen=True, slots=True)
class LiveResult:
    query_id: str
    case_class: str
    query: str
    reply: str
    retrieved_chunk_titles: list[str]
    completion_tokens: int | None
    retried_at_larger_budget: bool
    # unanswerable_populated only:
    refused_exactly: bool | None
    refused_leniently: bool | None
    # answerable only:
    faithfulness_status: str | None
    incorrectly_refused: bool | None


async def main(*, report_json: Path | None) -> None:
    settings = get_settings()
    if not settings.groq_api_key:
        print("AETHER_GROQ_API_KEY is not set in the process environment.", file=sys.stderr)
        raise SystemExit(1)

    all_queries = load_golden_queries(QUERIES_PATH)
    queries = [
        q
        for q in all_queries
        if q.case_class in (QueryClass.ANSWERABLE, QueryClass.UNANSWERABLE_POPULATED)
    ]
    answerable_count = sum(1 for q in queries if q.case_class is QueryClass.ANSWERABLE)
    unanswerable_count = len(queries) - answerable_count
    print(f"model: {_MODEL}")
    print(
        f"queries: {len(queries)} ({answerable_count} answerable, {unanswerable_count} unanswerable_populated)"
    )
    print(
        "faithfulness judge: evals.harness.judge.judge_faithfulness (real call, cross-family only)"
    )
    print("cross-family validation: NOT POSSIBLE in this environment — only Groq is configured.\n")

    embedder = build_embedder(settings)
    adapter = GroqCompletionAdapter(
        api_key=settings.groq_api_key, model=_MODEL, base_url=settings.groq_base_url
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

    results: list[LiveResult] = []
    could_not_run: list[str] = []
    try:
        print("ingesting v2 corpus...")
        await ingest_v2_corpus(
            workspace_id=workspace_id,
            corpora_dir=CORPORA_DIR,
            bootstrap_pool=bootstrap_pool,
            worker_pool=worker_pool,
            object_storage=object_storage,
            clamav_endpoint=clamav_endpoint,
            embedder=embedder,
            log=print,
        )
        hybrid_search = HybridSearch(chunk_search=PooledChunkSearch(db_pool), embedder=embedder)

        for i, query in enumerate(queries, start=1):
            print(f"\n[{i}/{len(queries)}] {query.id} ({query.case_class.value})")
            retrieval = await hybrid_search.search(workspace_id, query=query.query, k=_RETRIEVAL_K)
            context = RetrievedContext(
                chunks=[
                    RetrievedContextChunk(
                        content=c.content,
                        document_title=c.document_title,
                        section_path=c.section_path,
                    )
                    for c in retrieval.chunks
                ]
            )
            messages = _build_messages([], query.query, context, None)
            request = CompletionRequest(messages=messages, model=_MODEL, max_tokens=_MAX_TOKENS)

            reply, usage, error, retried = await call_with_backoff_and_reasoning_retry(
                adapter, request, followup_max_tokens=_FOLLOWUP_MAX_TOKENS
            )
            if error is not None:
                print(f"  COULD NOT RUN: {error}")
                could_not_run.append(f"{query.id}: {error}")
                continue

            refused_exactly = refused_leniently = None
            faithfulness_status = incorrectly_refused = None

            if query.case_class is QueryClass.UNANSWERABLE_POPULATED:
                refused_exactly = reply.strip() == NOT_IN_KNOWLEDGE_BASE_REPLY
                refused_leniently = NOT_IN_KNOWLEDGE_BASE_REPLY.lower() in reply.lower()
                print(f"  refused_exactly={refused_exactly} refused_leniently={refused_leniently}")
            else:
                incorrectly_refused = NOT_IN_KNOWLEDGE_BASE_REPLY.lower() in reply.lower()
                verdict = await judge_faithfulness(
                    query=query.query,
                    context_chunks=[c.content for c in retrieval.chunks],
                    reply=reply,
                    rubric=None,
                    generator_model=_MODEL,
                    settings=settings,
                )
                faithfulness_status = verdict.status.value
                print(
                    f"  incorrectly_refused={incorrectly_refused} "
                    f"faithfulness_status={faithfulness_status}"
                )
                if verdict.status != FaithfulnessStatus.NOT_MEASURED:
                    print(
                        f"  UNEXPECTED: faithfulness was actually measured "
                        f"(judge_model={verdict.judge_model}) — a second provider family "
                        "must have become available.",
                        file=sys.stderr,
                    )

            print(f"  reply: {reply[:200]}{'...' if len(reply) > 200 else ''}")
            results.append(
                LiveResult(
                    query_id=query.id,
                    case_class=query.case_class.value,
                    query=query.query,
                    reply=reply,
                    retrieved_chunk_titles=[c.document_title for c in retrieval.chunks],
                    completion_tokens=usage.completion_tokens if usage else None,
                    retried_at_larger_budget=retried,
                    refused_exactly=refused_exactly,
                    refused_leniently=refused_leniently,
                    faithfulness_status=faithfulness_status,
                    incorrectly_refused=incorrectly_refused,
                )
            )
    finally:
        await bootstrap_pool.close()
        await worker_pool.close()
        await db_pool.close()

    unanswerable_results = [r for r in results if r.case_class == "unanswerable_populated"]
    answerable_results = [r for r in results if r.case_class == "answerable"]
    refused_exactly_count = sum(1 for r in unanswerable_results if r.refused_exactly)
    refused_leniently_count = sum(1 for r in unanswerable_results if r.refused_leniently)
    false_refusal_count = sum(1 for r in answerable_results if r.incorrectly_refused)

    print("\n=== SUMMARY ===")
    print(f"ran successfully: {len(results)}/{len(queries)}")
    if could_not_run:
        print(f"could not run: {len(could_not_run)}")
        for line in could_not_run:
            print(f"  {line}")
    print(f"\nGate 2 refusal correctness (unanswerable_populated, n={len(unanswerable_results)}):")
    print(
        f"  exact match to canonical refusal string: {refused_exactly_count}/{len(unanswerable_results)}"
    )
    print(
        f"  contains the canonical refusal string:   {refused_leniently_count}/{len(unanswerable_results)}"
    )
    print(f"\nAnswerable queries (n={len(answerable_results)}):")
    print(
        f"  false refusals (should have answered, refused instead): {false_refusal_count}/{len(answerable_results)}"
    )
    print(
        "  faithfulness: "
        f"{sum(1 for r in answerable_results if r.faithfulness_status == 'not_measured')}/"
        f"{len(answerable_results)} not_measured (expected — no cross-family judge available)"
    )

    if report_json is not None:
        report_json.write_text(json.dumps([asdict(r) for r in results], indent=2))
        print(f"\nwrote {report_json}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 7 RUN 2: live faithfulness/refusal run")
    parser.add_argument("--report-json", type=Path, default=None)
    args = parser.parse_args()
    asyncio.run(main(report_json=args.report_json))
