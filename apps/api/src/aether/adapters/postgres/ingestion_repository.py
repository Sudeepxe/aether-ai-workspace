"""Postgres-backed IngestionRepositoryPort (§3.2.7, §8.1). Pool-bound —
see the port's own docstring.
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid5

import asyncpg

from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.ports.ingestion_repository import Chunk, ChunkDraft, DocumentStatus

_MAX_CHUNK_BATCH = 500  # §8's failure-mode guidance: capped so a long-running
# ingestion transaction never blocks autovacuum on the chunks table.


class PostgresIngestionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def update_status(
        self, workspace_id: UUID, document_id: UUID, *, status: DocumentStatus
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            await conn.execute(
                "UPDATE documents SET status = $2, updated_at = now() WHERE id = $1",
                document_id,
                status.value,
            )

    async def mark_failed(
        self, workspace_id: UUID, document_id: UUID, *, stage: str, reason: str
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            await conn.execute(
                "UPDATE documents SET status = 'failed', failure_stage = $2, "
                "failure_reason = $3, updated_at = now() WHERE id = $1",
                document_id,
                stage,
                reason,
            )
            await PostgresOutboxRepository(conn).enqueue_idempotent(
                # Deterministic per-(document, stage) id: mark_failed is
                # only ever called for permanent, deterministic failures
                # (issue #46's docstring) — the same stage+content always
                # fails the same way, so redelivery producing the same
                # event id and ON CONFLICT DO NOTHING silently no-ops the
                # duplicate rather than double-enqueuing.
                id=uuid5(document_id, f"document.failed:{stage}"),
                aggregate_type="document",
                aggregate_id=document_id,
                event_type="document.failed",
                tenant_id=workspace_id,
                payload={"document_id": str(document_id), "stage": stage, "reason": reason},
            )

    async def insert_chunks_and_advance(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        chunks: list[ChunkDraft],
        next_status: DocumentStatus,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            for batch_start in range(0, len(chunks), _MAX_CHUNK_BATCH):
                batch = chunks[batch_start : batch_start + _MAX_CHUNK_BATCH]
                await conn.executemany(
                    """
                    INSERT INTO chunks (
                        id, workspace_id, document_id, section_path, page_start, page_end,
                        char_start, char_end, content, content_sha256, token_count
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    [
                        (
                            _chunk_id(document_id, batch_start + offset),
                            workspace_id,
                            document_id,
                            chunk.section_path,
                            chunk.page_start,
                            chunk.page_end,
                            chunk.char_start,
                            chunk.char_end,
                            chunk.content,
                            hashlib.sha256(chunk.content.encode()).hexdigest(),
                            chunk.token_count,
                        )
                        for offset, chunk in enumerate(batch)
                    ],
                )
            await conn.execute(
                "UPDATE documents SET status = $2, updated_at = now() WHERE id = $1",
                document_id,
                next_status.value,
            )

    async def list_chunks(self, workspace_id: UUID, document_id: UUID) -> list[Chunk]:
        # set_config(..., true) is transaction-local: the SELECT must
        # share the same transaction as the set_config call, or the
        # tenant setting reverts before it runs and RLS hides every row.
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            rows = await conn.fetch(
                """
                SELECT id, workspace_id, document_id, section_path, page_start, page_end,
                       char_start, char_end, content, content_sha256, token_count,
                       embedding, embedding_model, embedding_version, created_at
                FROM chunks WHERE document_id = $1 ORDER BY char_start
                """,
                document_id,
            )
            return [_row_to_chunk(row) for row in rows]

    async def find_cached_embeddings(
        self,
        workspace_id: UUID,
        *,
        content_hashes: list[str],
        embedding_model: str,
        embedding_version: int,
    ) -> dict[str, list[float]]:
        if not content_hashes:
            return {}
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (content_sha256) content_sha256, embedding
                FROM chunks
                WHERE content_sha256 = ANY($1) AND embedding IS NOT NULL
                  AND embedding_model = $2 AND embedding_version = $3
                ORDER BY content_sha256, created_at
                """,
                content_hashes,
                embedding_model,
                embedding_version,
            )
            return {row["content_sha256"]: row["embedding"].to_list() for row in rows}

    async def attach_embeddings_and_advance(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        chunk_embeddings: list[tuple[UUID, list[float]]],
        embedding_model: str,
        embedding_version: int,
        next_status: DocumentStatus,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            for batch_start in range(0, len(chunk_embeddings), _MAX_CHUNK_BATCH):
                batch = chunk_embeddings[batch_start : batch_start + _MAX_CHUNK_BATCH]
                await conn.executemany(
                    """
                    UPDATE chunks SET embedding = $2, embedding_model = $3, embedding_version = $4
                    WHERE id = $1
                    """,
                    [
                        (chunk_id, vector, embedding_model, embedding_version)
                        for chunk_id, vector in batch
                    ],
                )
            await conn.execute(
                "UPDATE documents SET status = $2, updated_at = now() WHERE id = $1",
                document_id,
                next_status.value,
            )
            if next_status == DocumentStatus.READY:
                await PostgresOutboxRepository(conn).enqueue_idempotent(
                    # Deterministic per-document id: a document reaches
                    # READY at most once in its current (pre-Phase-2
                    # versioning) lifecycle, and redelivery after a prior
                    # success re-runs this same call — ON CONFLICT DO
                    # NOTHING keeps that a silent no-op.
                    id=uuid5(document_id, "document.ready"),
                    aggregate_type="document",
                    aggregate_id=document_id,
                    event_type="document.ready",
                    tenant_id=workspace_id,
                    payload={"document_id": str(document_id)},
                )


def _row_to_chunk(row: asyncpg.Record) -> Chunk:
    embedding = row["embedding"]
    return Chunk(
        id=row["id"],
        workspace_id=row["workspace_id"],
        document_id=row["document_id"],
        section_path=row["section_path"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        char_start=row["char_start"],
        char_end=row["char_end"],
        content=row["content"],
        content_sha256=row["content_sha256"],
        token_count=row["token_count"],
        embedding=embedding.to_list() if embedding is not None else None,
        embedding_model=row["embedding_model"],
        embedding_version=row["embedding_version"],
        created_at=row["created_at"],
    )


def _chunk_id(document_id: UUID, index: int) -> UUID:
    """Deterministic per-(document, index) UUID — makes chunk insertion
    idempotent under at-least-once redelivery (issue #45): a redelivered
    message re-running this same chunking result produces the same ids,
    so ON CONFLICT DO NOTHING silently no-ops the duplicate insert
    instead of raising a unique-violation that would otherwise wedge
    this document permanently in CHUNKING status."""
    return uuid5(document_id, str(index))
