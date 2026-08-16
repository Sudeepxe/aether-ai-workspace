"""ListDocuments use case (§4.3: GET /workspaces/{ws}/documents, cursor-
paginated on (created_at, id) per ADR-4.4). Thin pass-through — the HTTP
layer owns cursor encoding/decoding, matching ListThreads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aether.domain.entities import Document
from aether.ports.repositories import DocumentRepositoryPort


@dataclass(frozen=True, slots=True)
class ListDocumentsCommand:
    workspace_id: UUID
    after: tuple[datetime, UUID] | None
    limit: int


class ListDocuments:
    def __init__(self, *, documents: DocumentRepositoryPort) -> None:
        self._documents = documents

    async def execute(self, command: ListDocumentsCommand) -> list[Document]:
        return await self._documents.list_by_workspace(
            command.workspace_id, after=command.after, limit=command.limit
        )
