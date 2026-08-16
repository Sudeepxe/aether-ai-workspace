"""GetDocument use case (§4.3: GET /workspaces/{ws}/documents/{document}) —
the status-detail read FR-KB-2's visible pipeline is built on."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Document
from aether.domain.errors import DocumentNotFoundError
from aether.ports.repositories import DocumentRepositoryPort


@dataclass(frozen=True, slots=True)
class GetDocumentCommand:
    workspace_id: UUID
    document_id: UUID


class GetDocument:
    def __init__(self, *, documents: DocumentRepositoryPort) -> None:
        self._documents = documents

    async def execute(self, command: GetDocumentCommand) -> Document:
        document = await self._documents.get(command.workspace_id, command.document_id)
        if document is None:
            raise DocumentNotFoundError(str(command.document_id))
        return document
