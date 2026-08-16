"""Hybrid retrieval (§3.2.5, ADR-6.3): vector + lexical search fused
with Reciprocal Rank Fusion, then MMR de-duplication. Plain owned code
per ADR-6.1 ("no orchestration framework for the core pipeline") — the
same pure-algorithm-over-a-port shape as app/ingestion/chunking.py.

RRF constant (k=60) and MMR's diversity weight (lambda=0.5) aren't
specified by ADR-6.3 (it names the techniques, not their parameters) —
these are deliberate, documented MVP defaults, not derived from real
eval data (Sprint 7's golden set doesn't exist yet). k=60 is RRF's
conventional default across the IR literature; lambda=0.5 weights
relevance and diversity equally as a neutral starting point. Both are
module constants, easy to promote to config once Sprint 7 has evidence
to tune them against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from aether.ports.embedding import EmbeddingProviderPort
from aether.ports.retrieval import ChunkSearchPort, ChunkSearchResult

_VECTOR_LEG_LIMIT = 20
_LEXICAL_LEG_LIMIT = 20
_RRF_CONSTANT = 60
_MMR_LAMBDA = 0.5
_MMR_CANDIDATE_POOL = 20  # fused results MMR considers before picking the final k
_DEFAULT_K = 6  # ADR-6.3: "top 6 results enter the prompt"


@dataclass(frozen=True, slots=True)
class RankedChunk:
    chunk_id: UUID
    document_id: UUID
    document_title: str
    section_path: str
    page_start: int | None
    page_end: int | None
    content: str
    fused_score: float


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunks: list[RankedChunk]
    degraded: bool
    """True when the vector leg failed and results are lexical-only
    (§3.2.5's documented degraded mode) — the caller surfaces this to
    the user rather than silently returning a weaker answer."""


class HybridSearch:
    def __init__(self, *, chunk_search: ChunkSearchPort, embedder: EmbeddingProviderPort) -> None:
        self._chunk_search = chunk_search
        self._embedder = embedder

    async def search(
        self,
        workspace_id: UUID,
        *,
        query: str,
        lexical_queries: list[str] | None = None,
        k: int = _DEFAULT_K,
    ) -> RetrievalResult:
        """``query`` feeds the vector leg. ``lexical_queries`` feeds the
        lexical leg — defaults to ``[query]`` when not given (a single
        query feeding both legs, issue #56's original behavior);
        dual-feed query rewriting (issue #57) passes both the raw and
        rewritten queries here so the lexical leg sees both while the
        vector leg only ever sees the (already condensed) ``query``."""
        (query_embedding,) = await self._embedder.embed_batch([query])

        degraded = False
        try:
            vector_results = await self._chunk_search.search_vector(
                workspace_id, embedding=query_embedding, limit=_VECTOR_LEG_LIMIT
            )
        except Exception:
            # §3.2.5's documented degraded mode: a vector-index failure
            # falls back to lexical-only rather than failing the whole
            # retrieval (and, transitively, the chat turn).
            vector_results = []
            degraded = True

        lexical_result_lists = [
            await self._chunk_search.search_lexical(
                workspace_id, query=lexical_query, limit=_LEXICAL_LEG_LIMIT
            )
            for lexical_query in (lexical_queries or [query])
        ]

        fused = _reciprocal_rank_fusion(vector_results, *lexical_result_lists)
        selected = _mmr_select(fused[:_MMR_CANDIDATE_POOL], k=k)
        return RetrievalResult(chunks=selected, degraded=degraded)


def _reciprocal_rank_fusion(
    *result_lists: list[ChunkSearchResult],
) -> list[tuple[ChunkSearchResult, float]]:
    """RRF fuses by rank position, not raw score — the only way to
    combine two incommensurable scoring systems (cosine similarity,
    ts_rank) without inventing a normalization between them."""
    scores: dict[UUID, float] = {}
    first_seen: dict[UUID, ChunkSearchResult] = {}
    for results in result_lists:
        for rank, result in enumerate(results, start=1):
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1.0 / (
                _RRF_CONSTANT + rank
            )
            first_seen.setdefault(result.chunk_id, result)
    ranked_ids = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
    return [(first_seen[chunk_id], scores[chunk_id]) for chunk_id in ranked_ids]


def _mmr_select(fused: list[tuple[ChunkSearchResult, float]], *, k: int) -> list[RankedChunk]:
    if not fused:
        return []
    relevance = _normalize([score for _, score in fused])
    candidates = list(zip((r for r, _ in fused), relevance, strict=True))
    selected: list[tuple[ChunkSearchResult, float]] = []

    while candidates and len(selected) < k:
        best_index = 0
        best_mmr_score = float("-inf")
        for index, (result, result_relevance) in enumerate(candidates):
            diversity_penalty = max(
                (_cosine_similarity(result.embedding, chosen.embedding) for chosen, _ in selected),
                default=0.0,
            )
            mmr_score = _MMR_LAMBDA * result_relevance - (1 - _MMR_LAMBDA) * diversity_penalty
            if mmr_score > best_mmr_score:
                best_mmr_score = mmr_score
                best_index = index
        selected.append(candidates.pop(best_index))

    return [
        RankedChunk(
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            document_title=result.document_title,
            section_path=result.section_path,
            page_start=result.page_start,
            page_end=result.page_end,
            content=result.content,
            fused_score=relevance_score,
        )
        for result, relevance_score in selected
    ]


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _cosine_similarity(a: list[float] | None, b: list[float] | None) -> float:
    """A missing embedding (a lexical-only hit whose chunk hasn't
    finished embedding yet) is treated as maximally diverse from
    everything else, not an error — MMR still needs to rank it."""
    if a is None or b is None:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
