"""Condensing query rewrite + dual-feed assembly (§3.2.5, ADR-6.3).

Dual-feed is the load-bearing property (ADR-6.3's own self-review
flags the alternative as a real bug class): both the raw and rewritten
queries feed the lexical leg; only the rewritten query feeds the
vector leg. Feeding only the rewritten query to both legs would
silently defeat hybrid's purpose by discarding exact terms (IDs,
acronyms, names) the lexical leg exists to catch.

A rewrite is skipped entirely on a thread's first turn (no history to
condense against) and on any rewrite failure — timeout past the 150ms
budget, or the provider call itself failing — falls back to the raw
query rather than ever blocking or failing retrieval. This mirrors
HybridSearch's degraded-mode philosophy (issue #56): a missing
optimization is a designed fallback, not an error.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

from aether.domain.entities import Message
from aether.ports.query_rewrite import QueryRewritePort

log = structlog.get_logger(__name__)

_REWRITE_TIMEOUT_SECONDS = 0.150


@dataclass(frozen=True, slots=True)
class DualFeedQuery:
    vector_query: str
    """The query that feeds the vector leg — the rewritten, standalone
    form when a rewrite happened, otherwise the raw query."""
    lexical_queries: list[str]
    """The quer(ies) that feed the lexical leg — always includes the
    raw query; also includes the rewritten query when one was produced
    and differs from the raw query."""


class QueryRewriter:
    def __init__(self, *, rewriter: QueryRewritePort) -> None:
        self._rewriter = rewriter

    async def build_dual_feed_query(
        self, *, history: list[Message], raw_query: str
    ) -> DualFeedQuery:
        if not history:
            # First turn: nothing to condense against — a rewrite here
            # would have no prior context to draw from.
            return DualFeedQuery(vector_query=raw_query, lexical_queries=[raw_query])

        try:
            rewritten = await asyncio.wait_for(
                self._rewriter.rewrite(history=history, raw_query=raw_query),
                timeout=_REWRITE_TIMEOUT_SECONDS,
            )
        except Exception:
            # Timeout past the 150ms budget, or the rewrite call itself
            # failing — either way, retrieval must never block or fail
            # because an optimization didn't complete in time.
            log.warning("query_rewrite_failed_falling_back_to_raw_query")
            return DualFeedQuery(vector_query=raw_query, lexical_queries=[raw_query])

        if rewritten == raw_query:
            return DualFeedQuery(vector_query=rewritten, lexical_queries=[raw_query])
        return DualFeedQuery(vector_query=rewritten, lexical_queries=[raw_query, rewritten])
