"""v2 golden-query schema (Phase 5, docs/REMEDIATION_PLAN.md) — retrieval
only, deliberately not v1's chat-turn schema (schema.py). v1's GoldenCase
drives a full chat turn and grades what the model chose to cite; these
queries instead call HybridSearch directly and grade the raw ranked
chunk list itself, which is what actually lets ranking metrics
(recall@k, MRR, NDCG@k) exist at all — v1's citation-based hit-rate has
no visibility into anything the model didn't end up citing.

Chunk ids are random UUIDs regenerated on every ingestion run, so a
golden query can't name one directly. Each relevant chunk is instead
identified by an anchor: a substring deliberately unique to (or, when a
fact legitimately sits in the chunker's overlap zone, shared by) one or
more real ingested chunks — resolved against actual chunk content at
eval time by retrieval_runner.py, never hardcoded. See queries.json's
"notes" fields and the v2 README for cases where legitimate chunking
overlap means an anchor resolves to more than one real chunk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class QueryClass(StrEnum):
    ANSWERABLE = "answerable"
    NEAR_MISS = "near_miss"
    UNANSWERABLE_POPULATED = "unanswerable_populated"


@dataclass(frozen=True, slots=True)
class RelevantChunk:
    anchor: str
    """A substring that must appear in the real chunk content this
    golden item considers relevant — resolved against the actual
    ingested corpus at eval time, not a hardcoded chunk id."""
    grade: str
    """"primary" or "supporting" — feeds NDCG's graded relevance
    (primary=2, supporting=1); both count equally for recall@k and MRR,
    since either being retrieved is a genuine hit."""


@dataclass(frozen=True, slots=True)
class GoldenQuery:
    id: str
    case_class: QueryClass
    query: str
    relevant_chunks: list[RelevantChunk]
    """Empty for unanswerable_populated — there is no correct chunk to
    find, by design; the query is scored against the refusal threshold
    instead of recall/MRR/NDCG."""
    distractor_anchor: str | None = None
    """near_miss only: identifies the real chunk a plausible-but-wrong
    answer would come from, so the runner can report whether it
    outranks the actual relevant chunk — the specific failure mode
    near-miss queries exist to catch."""
    notes: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldenQuery:
        return cls(
            id=data["id"],
            case_class=QueryClass(data["case_class"]),
            query=data["query"],
            relevant_chunks=[
                RelevantChunk(anchor=r["anchor"], grade=r["grade"])
                for r in data.get("relevant_chunks", [])
            ],
            distractor_anchor=data.get("distractor_anchor"),
            notes=data.get("notes"),
        )


def load_golden_queries(path: Path) -> list[GoldenQuery]:
    """Loads the single ``queries.json`` array (not one-file-per-case
    like v1's schema.py) — v2's per-item payload is small enough
    (a query string, a handful of anchor phrases) that one file is more
    practical to author and review at this volume than ~50 tiny files
    would be; a deliberate divergence from v1's convention, not an
    oversight."""
    data = json.loads(path.read_text())
    return sorted((GoldenQuery.from_dict(d) for d in data), key=lambda q: q.id)
