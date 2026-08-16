"""IngestionRepositoryPort (§3.2.7, §8.1's invariant transactions):
pool-bound, not connection-bound — the ingestion pipeline runs in the
worker process, one short-lived connection per call, the same shape as
``ports.chat.MessageStorePort`` and for the same reason (no per-request
connection to bind to; a pipeline run's lifetime has nothing to do with
an HTTP request's).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aether.domain.entities import ChunkDraft, DocumentStatus

__all__ = ["ChunkDraft", "DocumentStatus", "IngestionRepositoryPort"]


class IngestionRepositoryPort(Protocol):
    async def update_status(
        self, workspace_id: UUID, document_id: UUID, *, status: DocumentStatus
    ) -> None: ...

    async def mark_failed(
        self, workspace_id: UUID, document_id: UUID, *, stage: str, reason: str
    ) -> None: ...

    async def insert_chunks_and_advance(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        chunks: list[ChunkDraft],
        next_status: DocumentStatus,
    ) -> None:
        """One ACID transaction (§8's invariant-transactions list):
        every chunk row plus the document's status advance together —
        a mid-batch failure must never leave a document in EMBEDDING
        status with only some of its chunks present."""
        ...
