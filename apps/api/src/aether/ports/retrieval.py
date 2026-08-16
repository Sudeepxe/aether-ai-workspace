"""ChunkSearchPort (§3.2.5): the two raw search primitives hybrid
retrieval fuses. Deliberately I/O-only — RRF fusion and MMR
de-duplication are pure algorithm (no I/O) and live in
app/retrieval/hybrid_search.py per ADR-6.1 ("no orchestration
framework... own plumbing"), the same adapter-does-I/O /
app-does-algorithm split ADR-6.2's chunking already established.

Connection-bound (not pool-bound): called from within SendMessage's
existing per-request WorkspaceScope transaction (issue #60), the same
lifecycle as ThreadRepositoryPort/DocumentRepositoryPort, not the
worker's pool-per-call pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ChunkSearchResult:
    chunk_id: UUID
    document_id: UUID
    document_title: str
    section_path: str
    page_start: int | None
    page_end: int | None
    content: str
    embedding: list[float] | None
    """None when a lexical-only hit's chunk hasn't finished embedding
    yet — MMR treats a missing embedding as maximally diverse from
    every other candidate rather than failing."""
    score: float
    """Leg-native score (cosine similarity for the vector leg, ts_rank
    for the lexical leg) — never compared directly across legs; RRF
    fuses by rank, not by this raw value."""


class ChunkSearchPort(Protocol):
    async def search_vector(
        self, workspace_id: UUID, *, embedding: list[float], limit: int
    ) -> list[ChunkSearchResult]:
        """HNSW cosine-similarity search. Only chunks that have finished
        embedding, belong to a READY (not soft-deleted, not still
        mid-pipeline) document, and belong to this workspace are
        candidates — enforced by an explicit WHERE clause in addition
        to RLS (§3.2.5: "tenant filter is a mandatory, type-enforced
        parameter" on top of RLS, not instead of it)."""
        ...

    async def search_lexical(
        self, workspace_id: UUID, *, query: str, limit: int
    ) -> list[ChunkSearchResult]:
        """Postgres full-text search (`websearch_to_tsquery` against the
        `content_tsv` generated column) — same READY/not-deleted/
        tenant-scoped constraints as the vector leg."""
        ...
