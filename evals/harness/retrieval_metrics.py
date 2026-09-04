"""Real ranking metrics over HybridSearch's raw ranked chunk list —
Phase 5 of docs/REMEDIATION_PLAN.md, closing the audit's finding that
v1's only retrieval signal (citation-based hit-rate) is document-level
and can't demonstrate ranking quality: a chunk cited by the model isn't
the same thing as a chunk that ranked well, and a document-level "hit"
says nothing about how many other documents' chunks it beat to get
there. These metrics need no LLM at all — they're computed directly
from HybridSearch.search()'s output against the golden set's graded
relevance labels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from evals.harness.retrieval_schema import GoldenQuery, QueryClass

_GRADE_WEIGHT = {"primary": 2.0, "supporting": 1.0}


@dataclass(frozen=True, slots=True)
class RankedResult:
    chunk_id: UUID
    fused_score: float


@dataclass(frozen=True, slots=True)
class QueryMetrics:
    query_id: str
    case_class: QueryClass
    recall_at_5: float | None
    """Fraction of this query's relevant chunks found in the top 5.
    None for unanswerable_populated (nothing to recall)."""
    recall_at_10: float | None
    mrr: float | None
    """Reciprocal rank of the first relevant chunk found, over the
    full ranked list returned. None for unanswerable_populated."""
    ndcg_at_10: float | None
    """Graded (primary=2, supporting=1) NDCG over the top 10. None for
    unanswerable_populated, and None (not 0) for a query whose relevant
    chunks never appeared at all in the *candidate pool* the ranked
    list was drawn from — that's a resolution/corpus problem the
    caller should investigate, not a legitimate zero score."""
    top_fused_score: float
    """The real, un-normalized top RRF score — what Gate 1 actually
    compares against the refusal threshold."""
    would_refuse: bool
    """top_fused_score < the configured refusal threshold — Gate 1's
    real decision, computed here mechanically rather than by actually
    calling the router (Phase 5 has no LLM in the loop at all)."""
    distractor_rank: int | None
    """near_miss only: 1-indexed rank of the distractor chunk in the
    returned list, or None if it didn't appear at all."""
    relevant_ranks: list[int]
    """1-indexed ranks at which any relevant chunk was actually found,
    in the order they appear in the ranked list — empty if none were
    found. Used for both scoring and for a human reading the report to
    see exactly what happened, not just the summary number."""


def _relevant_chunk_ids(query: GoldenQuery, anchor_index: dict[str, list[UUID]]) -> dict[UUID, str]:
    """Every real chunk id any of this query's anchors resolved to,
    mapped to the grade of the anchor that resolved it — highest grade
    wins if a chunk satisfies more than one anchor."""
    grades: dict[UUID, str] = {}
    for rel in query.relevant_chunks:
        for chunk_id in anchor_index.get(rel.anchor, []):
            if chunk_id not in grades or _GRADE_WEIGHT[rel.grade] > _GRADE_WEIGHT[grades[chunk_id]]:
                grades[chunk_id] = rel.grade
    return grades


def _dcg(grades_in_rank_order: list[float]) -> float:
    return sum(rel / math.log2(rank + 1) for rank, rel in enumerate(grades_in_rank_order, start=1))


def score_query(
    query: GoldenQuery,
    ranked: list[RankedResult],
    *,
    anchor_index: dict[str, list[UUID]],
    refusal_threshold: float,
) -> QueryMetrics:
    ranked_ids = [r.chunk_id for r in ranked]
    top_fused_score = ranked[0].fused_score if ranked else 0.0

    distractor_rank: int | None = None
    if query.distractor_anchor is not None:
        distractor_ids = set(anchor_index.get(query.distractor_anchor, []))
        distractor_rank = next(
            (i for i, cid in enumerate(ranked_ids, start=1) if cid in distractor_ids), None
        )

    if query.case_class is QueryClass.UNANSWERABLE_POPULATED:
        return QueryMetrics(
            query_id=query.id,
            case_class=query.case_class,
            recall_at_5=None,
            recall_at_10=None,
            mrr=None,
            ndcg_at_10=None,
            top_fused_score=top_fused_score,
            would_refuse=top_fused_score < refusal_threshold,
            distractor_rank=distractor_rank,
            relevant_ranks=[],
        )

    relevant = _relevant_chunk_ids(query, anchor_index)
    relevant_ranks = [i for i, cid in enumerate(ranked_ids, start=1) if cid in relevant]

    def _recall_at(k: int) -> float:
        if not relevant:
            return 0.0
        found = len({cid for cid in ranked_ids[:k] if cid in relevant})
        return found / len(relevant)

    mrr = (1.0 / relevant_ranks[0]) if relevant_ranks else 0.0

    top10_grades = [
        _GRADE_WEIGHT[relevant[cid]] if cid in relevant else 0.0 for cid in ranked_ids[:10]
    ]
    ideal_grades = sorted(
        [_GRADE_WEIGHT[g] for g in relevant.values()] + [0.0] * max(0, 10 - len(relevant)),
        reverse=True,
    )[:10]
    idcg = _dcg(ideal_grades)
    ndcg = (_dcg(top10_grades) / idcg) if idcg > 0 else None

    return QueryMetrics(
        query_id=query.id,
        case_class=query.case_class,
        recall_at_5=_recall_at(5),
        recall_at_10=_recall_at(10),
        mrr=mrr,
        ndcg_at_10=ndcg,
        top_fused_score=top_fused_score,
        would_refuse=top_fused_score < refusal_threshold,
        distractor_rank=distractor_rank,
        relevant_ranks=relevant_ranks,
    )


@dataclass(frozen=True, slots=True)
class AggregateRetrievalMetrics:
    total_queries: int
    answerable_recall_at_5: float | None
    answerable_recall_at_10: float | None
    answerable_mrr: float | None
    answerable_ndcg_at_10: float | None
    near_miss_recall_at_10: float | None
    near_miss_distractor_beats_truth_rate: float | None
    """Fraction of near_miss queries where the distractor chunk ranked
    ABOVE every relevant chunk — the specific failure mode near-miss
    queries exist to catch, not just whether the truth was found at all."""
    unanswerable_refusal_correct_rate: float | None
    """Fraction of unanswerable_populated queries where the real fused
    score correctly fell below the refusal threshold."""


def aggregate(metrics: list[QueryMetrics]) -> AggregateRetrievalMetrics:
    def _mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    answerable = [m for m in metrics if m.case_class is QueryClass.ANSWERABLE]
    near_miss = [m for m in metrics if m.case_class is QueryClass.NEAR_MISS]
    unanswerable = [m for m in metrics if m.case_class is QueryClass.UNANSWERABLE_POPULATED]

    distractor_beats_truth = [
        (
            m.distractor_rank is not None
            and (not m.relevant_ranks or m.distractor_rank < m.relevant_ranks[0])
        )
        for m in near_miss
        if m.distractor_rank is not None or m.relevant_ranks
    ]

    return AggregateRetrievalMetrics(
        total_queries=len(metrics),
        answerable_recall_at_5=_mean(
            [m.recall_at_5 for m in answerable if m.recall_at_5 is not None]
        ),
        answerable_recall_at_10=_mean(
            [m.recall_at_10 for m in answerable if m.recall_at_10 is not None]
        ),
        answerable_mrr=_mean([m.mrr for m in answerable if m.mrr is not None]),
        answerable_ndcg_at_10=_mean([m.ndcg_at_10 for m in answerable if m.ndcg_at_10 is not None]),
        near_miss_recall_at_10=_mean(
            [m.recall_at_10 for m in near_miss if m.recall_at_10 is not None]
        ),
        near_miss_distractor_beats_truth_rate=(
            sum(distractor_beats_truth) / len(distractor_beats_truth)
            if distractor_beats_truth
            else None
        ),
        unanswerable_refusal_correct_rate=_mean(
            [1.0 if m.would_refuse else 0.0 for m in unanswerable]
        ),
    )
