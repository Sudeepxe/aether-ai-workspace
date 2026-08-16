"""The real ingestion-pipeline handler (§3.2.7): fetch -> scan ->
detect type -> parse -> chunk -> persist, passed as ``run_ingestion_
consumer``'s handler (issue #45). Stops at CHUNKING -> EMBEDDING;
issue #47 owns the embedding call itself and the final EMBEDDING ->
READY transition.

Failure handling follows one rule: a *permanent* failure (the content
itself is malicious, unparseable, or an unsupported type — retrying
would fail identically every time) is caught here and written straight
to the document's failed status, then the handler returns normally so
the queue acks it — no point burning through issue #45's bounded
retries on something that can never succeed. Anything else (storage
unreachable, scanner connection refused, an unexpected bug) is left to
propagate, so the queue's own retry-then-DLQ mechanism handles it —
those might genuinely succeed on a later attempt.

The ``document.uploaded`` payload contract this handler expects (issue
#48, not yet built, must produce events matching this shape):
``{"document_id": str(UUID), "object_key": str, "mime": str}``.
"""

from __future__ import annotations

from collections.abc import Callable
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
from aether.domain.entities import DocumentStatus
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


class DocumentProcessor:
    def __init__(
        self,
        *,
        object_storage: ObjectStoragePort,
        scanner: MalwareScanPort,
        repository: IngestionRepositoryPort,
    ) -> None:
        self._object_storage = object_storage
        self._scanner = scanner
        self._repository = repository

    async def __call__(self, message: QueuedMessage) -> None:
        workspace_id = message.tenant_id
        document_id = UUID(message.payload["document_id"])
        object_key = message.payload["object_key"]
        declared_mime = message.payload["mime"]

        await self._repository.update_status(
            workspace_id, document_id, status=DocumentStatus.SCANNING
        )
        content = await self._object_storage.download(key=object_key)

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
        chunks = chunk_document(nodes)

        await self._repository.insert_chunks_and_advance(
            workspace_id, document_id, chunks=chunks, next_status=DocumentStatus.EMBEDDING
        )
