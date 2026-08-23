"""Drives one golden case through the real, deployed HTTP surface —
register -> workspace -> thread -> real ingestion (corpus.py) -> real
chat turns (issue #60's SendMessage via the real SSE endpoint) -> the
real GET .../messages read path (issue #61's citations). No shortcuts
through the app/ports layer: this exercises exactly what a real client
would see, the same posture test_grounded_chat_e2e.py already proved
for a single case.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx

from evals.harness.corpus import ingest_corpus_files
from evals.harness.schema import GoldenCase

_PASSWORD = "eval-harness-s3cret!!"


@dataclass(frozen=True, slots=True)
class TurnResult:
    query: str
    grounded: bool
    content: str
    citations: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: GoldenCase
    turns: list[TurnResult]
    error: str | None = None


def _parse_sse_frames(text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in text.strip("\n").split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event_type = data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_type = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if event_type is not None:
            frames.append({"event": event_type, "data": json.loads(data) if data else None})
    return frames


async def run_case(
    case: GoldenCase,
    *,
    client: httpx.AsyncClient,
    bootstrap_pool: asyncpg.Pool,
    worker_pool: asyncpg.Pool,
    object_storage: Any,
    clamav_endpoint: tuple[str, int],
) -> CaseResult:
    try:
        email = f"eval-{case.id}-{uuid.uuid4().hex[:8]}@example.com"
        await client.post(
            "/v1/auth/register",
            json={"email": email, "password": _PASSWORD, "display_name": case.id},
        )
        login = await client.post("/v1/auth/login", json={"email": email, "password": _PASSWORD})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        ws_resp = await client.post("/v1/workspaces", json={"name": case.id}, headers=headers)
        workspace_id = ws_resp.json()["id"]
        thread_resp = await client.post(
            f"/v1/workspaces/{workspace_id}/threads", json={"title": case.id}, headers=headers
        )
        thread_id = thread_resp.json()["id"]

        if case.corpus_files:
            await ingest_corpus_files(
                workspace_id=uuid.UUID(workspace_id),
                filenames=case.corpus_files,
                bootstrap_pool=bootstrap_pool,
                worker_pool=worker_pool,
                object_storage=object_storage,
                clamav_endpoint=clamav_endpoint,
            )

        turn_results: list[TurnResult] = []
        for i, turn in enumerate(case.turns):
            resp = await client.post(
                f"/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
                json={"content": turn.query, "client_message_id": f"{case.id}-turn-{i}"},
                headers={**headers, "Accept": "text/event-stream"},
                timeout=30.0,
            )
            frames = _parse_sse_frames(resp.text)
            meta = next(f["data"] for f in frames if f["event"] == "meta")
            token_deltas = "".join(f["data"]["delta"] for f in frames if f["event"] == "token")
            citation_frames = [f["data"] for f in frames if f["event"] == "citation"]
            turn_results.append(
                TurnResult(
                    query=turn.query,
                    grounded=meta["grounded"],
                    content=token_deltas,
                    citations=citation_frames,
                )
            )
        return CaseResult(case=case, turns=turn_results)
    except Exception as exc:
        # A broken case (bad fixture, ingestion failure, unexpected
        # response shape) must not kill the whole run — it's reported as
        # a failed case, not an uncaught exception mid-golden-set.
        return CaseResult(case=case, turns=[], error=f"{type(exc).__name__}: {exc}")


async def run_golden_set(
    cases: list[GoldenCase],
    *,
    client: httpx.AsyncClient,
    bootstrap_pool: asyncpg.Pool,
    worker_pool: asyncpg.Pool,
    object_storage: Any,
    clamav_endpoint: tuple[str, int],
    log: Any = None,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        started = time.monotonic()
        result = await run_case(
            case,
            client=client,
            bootstrap_pool=bootstrap_pool,
            worker_pool=worker_pool,
            object_storage=object_storage,
            clamav_endpoint=clamav_endpoint,
        )
        results.append(result)
        if log is not None:
            elapsed = time.monotonic() - started
            status = "ERROR" if result.error else "ran"
            log(
                f"[{status}] {case.id} ({elapsed:.1f}s)"
                + (f": {result.error}" if result.error else "")
            )
    return results
