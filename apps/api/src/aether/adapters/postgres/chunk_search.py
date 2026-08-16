"""Postgres-backed ChunkSearchPort implementation.

Connection-bound (see thread_repository.py's docstring for why) — used
within SendMessage's existing per-request WorkspaceScope transaction,
so no manual set_config/conn.transaction() here (the request already
has app.tenant_id set and is inside one transaction).

Both legs join to ``documents`` for the filename (FR-KB-3's "doc name"
provenance field) and restrict to READY, non-deleted documents — a
document mid-pipeline or soft-deleted must never leak partial or
removed content into a retrieval result.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from aether.ports.retrieval import ChunkSearchResult


class PostgresChunkSearch:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def search_vector(
        self, workspace_id: UUID, *, embedding: list[float], limit: int
    ) -> list[ChunkSearchResult]:
        rows = await self._conn.fetch(
            """
            SELECT c.id, c.document_id, d.filename AS document_title, c.section_path,
                   c.page_start, c.page_end, c.content, c.embedding,
                   1 - (c.embedding <=> $1) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.workspace_id = $2 AND c.embedding IS NOT NULL
              AND d.status = 'ready' AND d.deleted_at IS NULL
            ORDER BY c.embedding <=> $1
            LIMIT $3
            """,
            embedding,
            workspace_id,
            limit,
        )
        return [_row_to_result(row) for row in rows]

    async def search_lexical(
        self, workspace_id: UUID, *, query: str, limit: int
    ) -> list[ChunkSearchResult]:
        rows = await self._conn.fetch(
            """
            SELECT c.id, c.document_id, d.filename AS document_title, c.section_path,
                   c.page_start, c.page_end, c.content, c.embedding,
                   ts_rank(c.content_tsv, websearch_to_tsquery('english', $1)) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.workspace_id = $2
              AND c.content_tsv @@ websearch_to_tsquery('english', $1)
              AND d.status = 'ready' AND d.deleted_at IS NULL
            ORDER BY score DESC
            LIMIT $3
            """,
            query,
            workspace_id,
            limit,
        )
        return [_row_to_result(row) for row in rows]


def _row_to_result(row: asyncpg.Record) -> ChunkSearchResult:
    embedding = row["embedding"]
    return ChunkSearchResult(
        chunk_id=row["id"],
        document_id=row["document_id"],
        document_title=row["document_title"],
        section_path=row["section_path"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        content=row["content"],
        embedding=embedding.to_list() if embedding is not None else None,
        score=row["score"],
    )
