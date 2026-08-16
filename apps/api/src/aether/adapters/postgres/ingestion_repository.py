"""Postgres-backed IngestionRepositoryPort (§3.2.7, §8.1). Pool-bound —
see the port's own docstring.
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid5

import asyncpg

from aether.ports.ingestion_repository import ChunkDraft, DocumentStatus

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


def _chunk_id(document_id: UUID, index: int) -> UUID:
    """Deterministic per-(document, index) UUID — makes chunk insertion
    idempotent under at-least-once redelivery (issue #45): a redelivered
    message re-running this same chunking result produces the same ids,
    so ON CONFLICT DO NOTHING silently no-ops the duplicate insert
    instead of raising a unique-violation that would otherwise wedge
    this document permanently in CHUNKING status."""
    return uuid5(document_id, str(index))
