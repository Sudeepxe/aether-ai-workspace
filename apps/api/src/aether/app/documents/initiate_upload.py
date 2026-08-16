"""InitiateDocumentUpload use case (§4.3: POST /workspaces/{ws}/documents:initiate).

Deliberately writes nothing to the database — a presigned POST is local
HMAC signing (ports.storage's docstring), and the ``documents`` row
can't exist yet anyway: the schema's ``content_sha256``/``object_key``
are meant to be genuinely content-addressed (ADR-3.8), which requires
the client to already know the file's hash before any row is created.
documents:confirm (after the client has actually uploaded the bytes) is
what creates the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.ports.security import IdPort
from aether.ports.storage import ObjectStoragePort

_PRESIGN_TTL_SECONDS = 900  # 15 min — plenty for a direct browser upload


@dataclass(frozen=True, slots=True)
class InitiateDocumentUploadCommand:
    workspace_id: UUID
    mime: str
    size_bytes: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class InitiateDocumentUploadResult:
    document_id: UUID
    object_key: str
    upload_url: str
    upload_fields: dict[str, str]


class InitiateDocumentUpload:
    def __init__(self, *, object_storage: ObjectStoragePort, ids: IdPort) -> None:
        self._object_storage = object_storage
        self._ids = ids

    async def execute(self, command: InitiateDocumentUploadCommand) -> InitiateDocumentUploadResult:
        document_id = self._ids.new_id()
        # Content-addressed key (ADR-3.8): re-uploading identical bytes
        # to the same workspace lands on the same object-storage key,
        # a natural storage-layer dedupe for free.
        object_key = f"{command.workspace_id}/{command.content_sha256}"
        presigned = self._object_storage.presign_upload(
            key=object_key,
            content_type=command.mime,
            max_size_bytes=command.size_bytes,
            expires_seconds=_PRESIGN_TTL_SECONDS,
        )
        return InitiateDocumentUploadResult(
            document_id=document_id,
            object_key=object_key,
            upload_url=presigned.url,
            upload_fields=presigned.fields,
        )
