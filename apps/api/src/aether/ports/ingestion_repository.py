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

from aether.domain.entities import Chunk, ChunkDraft, DocumentStatus

__all__ = ["Chunk", "ChunkDraft", "DocumentStatus", "IngestionRepositoryPort"]


class IngestionRepositoryPort(Protocol):
    async def update_status(
        self, workspace_id: UUID, document_id: UUID, *, status: DocumentStatus
    ) -> None: ...

    async def mark_failed(
        self, workspace_id: UUID, document_id: UUID, *, stage: str, reason: str
    ) -> None:
        """Sets status=FAILED with the given failure_stage/reason and
        writes the ``document.failed{stage, reason}`` outbox event
        (FR-KB-2's visible pipeline) in the same transaction — the one
        chokepoint every pipeline stage's permanent-failure path already
        calls (issue #46), so this covers scanning/parsing/chunking/
        embedding failures alike, not just the stage issue #47 owns."""
        ...

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

    async def list_chunks(self, workspace_id: UUID, document_id: UUID) -> list[Chunk]:
        """Every chunk row for this document, in char-offset order —
        the embedding stage's read side, distinguishing already-embedded
        rows (redelivery after a prior success) from ones still needing
        a vector."""
        ...

    async def find_cached_embeddings(
        self,
        workspace_id: UUID,
        *,
        content_hashes: list[str],
        embedding_model: str,
        embedding_version: int,
    ) -> dict[str, list[float]]:
        """The content-hash dedupe cache (§3.2.7's cost note): existing
        embedded chunks in this tenant whose content_sha256 matches one
        of the given hashes, keyed by hash — a hit here skips a
        redundant embedding-API call for re-uploaded/duplicate
        content."""
        ...

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
        """One ACID transaction (§8's invariant-transactions list):
        every chunk's vector plus the document's status advance
        together — a mid-batch failure must never leave a document
        marked READY with only some of its chunks embedded. Also writes
        the ``document.ready`` outbox event (FR-KB-2's visible pipeline)
        in the same transaction when ``next_status`` is READY."""
        ...
