"""ConfirmDocumentUpload use case (§4.3: POST /workspaces/{ws}/documents:confirm).

Creates the ``documents`` row and enqueues the ``document.uploaded``
outbox event (§8's invariant transactions) — the real producer issue
#46/#47's DocumentProcessor payload contract has been waiting on:
``{"document_id": str(UUID), "object_key": str, "mime": str}``.

Idempotent under a retried :confirm for the same document_id:
``DocumentRepositoryPort.create_if_absent`` no-ops on conflict (returns
None), so a repeat call fetches and returns the existing row without a
second document.uploaded event — no ON CONFLICT DO NOTHING dance needed
on the outbox side (unlike the worker-plane's enqueue_idempotent,
issue #47) since the *document row itself* is the idempotency guard
here, and app_api's outbox grant is INSERT-only (no SELECT), so an
outbox-side conflict check isn't even available to this role.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import Document
from aether.domain.errors import DocumentUploadIncompleteError
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.repositories import DocumentRepositoryPort
from aether.ports.security import IdPort
from aether.ports.storage import ObjectStoragePort

DOCUMENT_UPLOADED_EVENT_TYPE = "document.uploaded"


@dataclass(frozen=True, slots=True)
class ConfirmDocumentUploadCommand:
    workspace_id: UUID
    document_id: UUID
    filename: str
    mime: str
    size_bytes: int
    content_sha256: str
    object_key: str


class ConfirmDocumentUpload:
    def __init__(
        self,
        *,
        documents: DocumentRepositoryPort,
        object_storage: ObjectStoragePort,
        outbox: OutboxRepositoryPort,
        ids: IdPort,
    ) -> None:
        self._documents = documents
        self._object_storage = object_storage
        self._outbox = outbox
        self._ids = ids

    async def execute(self, command: ConfirmDocumentUploadCommand) -> Document:
        if not await self._object_storage.object_exists(key=command.object_key):
            raise DocumentUploadIncompleteError(command.object_key)

        created = await self._documents.create_if_absent(
            id=command.document_id,
            workspace_id=command.workspace_id,
            filename=command.filename,
            content_sha256=command.content_sha256,
            mime=command.mime,
            size_bytes=command.size_bytes,
            object_key=command.object_key,
        )
        if created is None:
            existing = await self._documents.get(command.workspace_id, command.document_id)
            assert existing is not None  # noqa: S101 — create_if_absent conflicted, so it exists
            return existing

        await self._outbox.enqueue(
            id=self._ids.new_id(),
            aggregate_type="document",
            aggregate_id=created.id,
            event_type=DOCUMENT_UPLOADED_EVENT_TYPE,
            tenant_id=command.workspace_id,
            payload={
                "document_id": str(created.id),
                "object_key": command.object_key,
                "mime": command.mime,
            },
        )
        return created
