"""The real ingestion-pipeline handler (§3.2.7): fetch -> scan ->
detect type -> parse -> chunk -> embed -> persist, passed as
``run_ingestion_consumer``'s handler (issue #45).

Failure handling follows one rule: a *permanent* failure (the content
itself is malicious, unparseable, an unsupported type, or an embedding
call that fails for a non-retryable reason — retrying would fail
identically every time) is caught here and written straight to the
document's failed status, then the handler returns normally so the
queue acks it — no point burning through issue #45's bounded retries on
something that can never succeed. Anything else (storage unreachable,
scanner connection refused, embedding quota exhaustion, an unexpected
bug) is left to propagate, so the queue's own retry-then-DLQ mechanism
handles it — issue #45's per-tenant fair queuing already means one
tenant's throttled retries can't starve another's pending work, so no
separate backoff mechanism is needed here (§3.2.7's failure-scenario
note).

The ``document.uploaded`` payload contract this handler expects (issue
#48, not yet built, must produce events matching this shape):
``{"document_id": str(UUID), "object_key": str, "mime": str}``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

from aether.app.ingestion.chunking import chunk_document
from aether.app.ingestion.document_tree import DocumentNode
from aether.app.ingestion.parsers.docx_parser import DocxParseError, parse_docx
from aether.app.ingestion.parsers.html_parser import HtmlParseError, parse_html
from aether.app.ingestion.parsers.markdown_parser import MarkdownParseError, parse_markdown
from aether.app.ingestion.parsers.pdf_parser import PdfParseError, parse_pdf
from aether.app.ingestion.parsers.text_parser import TextParseError, parse_text
from aether.app.ingestion.type_detection import (
    DetectedDocumentType,
    UnsupportedDocumentTypeError,
    detect_document_type,
)
from aether.domain.entities import Chunk, DocumentStatus
from aether.observability.metrics import INGESTION_STAGE_DURATION_SECONDS
from aether.ports.embedding import EmbeddingProviderError, EmbeddingProviderPort
from aether.ports.ingestion_queue import QueuedMessage
from aether.ports.ingestion_repository import IngestionRepositoryPort
from aether.ports.malware_scan import MalwareScanPort
from aether.ports.storage import ObjectStoragePort

_PARSERS: dict[DetectedDocumentType, Callable[[bytes], list[DocumentNode]]] = {
    DetectedDocumentType.PDF: parse_pdf,
    DetectedDocumentType.DOCX: parse_docx,
    DetectedDocumentType.MARKDOWN: parse_markdown,
    DetectedDocumentType.HTML: parse_html,
    DetectedDocumentType.TEXT: parse_text,
}

_PERMANENT_PARSE_ERRORS = (
    UnsupportedDocumentTypeError,
    PdfParseError,
    DocxParseError,
    MarkdownParseError,
    HtmlParseError,
    TextParseError,
)


@contextmanager
def _stage_timer(stage: str) -> Iterator[None]:
    """Ingestion dashboard's "per-stage timing" panel (§10.4) — one
    histogram observation per stage per document, regardless of
    success/failure (a slow failing scan is exactly as dashboard-
    relevant as a slow successful one)."""
    started = time.perf_counter()
    try:
        yield
    finally:
        INGESTION_STAGE_DURATION_SECONDS.labels(stage=stage).observe(time.perf_counter() - started)


class DocumentProcessor:
    def __init__(
        self,
        *,
        object_storage: ObjectStoragePort,
        scanner: MalwareScanPort,
        repository: IngestionRepositoryPort,
        embedder: EmbeddingProviderPort,
    ) -> None:
        self._object_storage = object_storage
        self._scanner = scanner
        self._repository = repository
        self._embedder = embedder

    async def __call__(self, message: QueuedMessage) -> None:
        workspace_id = message.tenant_id
        document_id = UUID(message.payload["document_id"])
        object_key = message.payload["object_key"]
        declared_mime = message.payload["mime"]

        await self._repository.update_status(
            workspace_id, document_id, status=DocumentStatus.SCANNING
        )
        content = await self._object_storage.download(key=object_key)

        with _stage_timer("scanning"):
            scan_result = await self._scanner.scan(content)
        if not scan_result.clean:
            await self._repository.mark_failed(
                workspace_id,
                document_id,
                stage="scanning",
                reason=f"malware detected: {scan_result.signature}",
            )
            return

        await self._repository.update_status(
            workspace_id, document_id, status=DocumentStatus.PARSING
        )
        try:
            with _stage_timer("parsing"):
                doc_type = detect_document_type(content, declared_mime=declared_mime)
                nodes = _PARSERS[doc_type](content)
        except _PERMANENT_PARSE_ERRORS as exc:
            await self._repository.mark_failed(
                workspace_id, document_id, stage="parsing", reason=str(exc)
            )
            return

        await self._repository.update_status(
            workspace_id, document_id, status=DocumentStatus.CHUNKING
        )
        with _stage_timer("chunking"):
            chunks = chunk_document(nodes)

        await self._repository.insert_chunks_and_advance(
            workspace_id, document_id, chunks=chunks, next_status=DocumentStatus.EMBEDDING
        )

        try:
            with _stage_timer("embedding"):
                await self._embed_and_finalize(workspace_id, document_id)
        except EmbeddingProviderError as exc:
            if exc.retryable:
                raise
            await self._repository.mark_failed(
                workspace_id, document_id, stage="embedding", reason=str(exc)
            )

    async def _embed_and_finalize(self, workspace_id: UUID, document_id: UUID) -> None:
        persisted = await self._repository.list_chunks(workspace_id, document_id)

        pending_hashes = list({c.content_sha256 for c in persisted if c.embedding is None})
        cached = await self._repository.find_cached_embeddings(
            workspace_id,
            content_hashes=pending_hashes,
            embedding_model=self._embedder.model,
            embedding_version=self._embedder.embedding_version,
        )

        to_embed = [c for c in persisted if c.embedding is None and c.content_sha256 not in cached]
        fresh = await self._embed_missing(to_embed)

        chunk_embeddings = [(c.id, _resolve_embedding(c, cached, fresh)) for c in persisted]
        await self._repository.attach_embeddings_and_advance(
            workspace_id,
            document_id,
            chunk_embeddings=chunk_embeddings,
            embedding_model=self._embedder.model,
            embedding_version=self._embedder.embedding_version,
            next_status=DocumentStatus.READY,
        )

    async def _embed_missing(self, to_embed: list[Chunk]) -> dict[UUID, list[float]]:
        if not to_embed:
            return {}
        vectors = await self._embedder.embed_batch([c.content for c in to_embed])
        return dict(zip((c.id for c in to_embed), vectors, strict=True))


def _resolve_embedding(
    chunk: Chunk, cached: dict[str, list[float]], fresh: dict[UUID, list[float]]
) -> list[float]:
    if chunk.embedding is not None:
        return chunk.embedding
    if chunk.content_sha256 in cached:
        return cached[chunk.content_sha256]
    return fresh[chunk.id]
