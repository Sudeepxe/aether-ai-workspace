"""Postgres-backed ChunkSearchPort implementation(s).

``PostgresChunkSearch`` is connection-bound (see thread_repository.py's
docstring for why) — for a request-scoped caller already inside one
transaction with ``app.tenant_id`` set, so no manual set_config/
conn.transaction() here.

``PooledChunkSearch`` is the pool-bound sibling issue #60's SendMessage
actually uses: chat's orchestrator is a singleton built once in
Container with only pool-bound ports (see ports.chat.MessageStorePort's
docstring for why a streaming request can never hold WorkspaceScope's
one connection open) — retrieval itself, unlike generation, is a
bounded single-shot read, so a fresh short-lived connection per leg is
safe and correct here, same pattern as adapters/postgres/message_store.py.

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


class PooledChunkSearch:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def search_vector(
        self, workspace_id: UUID, *, embedding: list[float], limit: int
    ) -> list[ChunkSearchResult]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            return await PostgresChunkSearch(conn).search_vector(
                workspace_id, embedding=embedding, limit=limit
            )

    async def search_lexical(
        self, workspace_id: UUID, *, query: str, limit: int
    ) -> list[ChunkSearchResult]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            return await PostgresChunkSearch(conn).search_lexical(
                workspace_id, query=query, limit=limit
            )


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
