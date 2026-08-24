from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from aether.domain.entities import Document, DocumentStatus
from aether.ports.storage import PresignedUpload


class FakeDocumentRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, Document] = {}

    async def create_if_absent(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        filename: str,
        content_sha256: str,
        mime: str,
        size_bytes: int,
        object_key: str,
    ) -> Document | None:
        if id in self._rows:
            return None
        now = datetime.now().astimezone()
        document = Document(
            id=id,
            workspace_id=workspace_id,
            filename=filename,
            content_sha256=content_sha256,
            mime=mime,
            size_bytes=size_bytes,
            object_key=object_key,
            status=DocumentStatus.QUEUED,
            failure_stage=None,
            failure_reason=None,
            version=1,
            superseded_by=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._rows[id] = document
        return document

    async def get(self, workspace_id: UUID, document_id: UUID) -> Document | None:
        document = self._rows.get(document_id)
        if (
            document is None
            or document.workspace_id != workspace_id
            or document.deleted_at is not None
        ):
            return None
        return document

    async def list_by_workspace(
        self, workspace_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[Document]:
        items = [
            d
            for d in self._rows.values()
            if d.workspace_id == workspace_id and d.deleted_at is None
        ]
        items.sort(key=lambda d: (d.created_at, d.id), reverse=True)
        if after is not None:
            items = [d for d in items if (d.created_at, d.id) < after]
        return items[:limit]

    async def delete(self, workspace_id: UUID, document_id: UUID, *, deleted_at: datetime) -> bool:
        document = self._rows.get(document_id)
        if (
            document is None
            or document.workspace_id != workspace_id
            or document.deleted_at is not None
        ):
            return False
        self._rows[document_id] = replace(document, deleted_at=deleted_at, updated_at=deleted_at)
        return True


class FakeDocumentObjectStorage:
    """A minimal ObjectStoragePort fake for the documents use cases —
    distinct from tests/unit/fakes/ingestion.py's FakeObjectStorage
    (which deliberately raises NotImplementedError on presign_upload/
    presign_download, since the ingestion pipeline handler never calls
    them). This one exercises exactly the two methods
    InitiateDocumentUpload/ConfirmDocumentUpload actually use."""

    def __init__(self, *, existing_keys: set[str] | None = None) -> None:
        self._existing_keys = existing_keys or set()

    def presign_upload(
        self, *, key: str, content_type: str, max_size_bytes: int, expires_seconds: int
    ) -> PresignedUpload:
        return PresignedUpload(
            url="https://storage.example/bucket",
            fields={"key": key, "Content-Type": content_type},
        )

    def presign_download(self, *, key: str, expires_seconds: int) -> str:
        raise NotImplementedError  # pragma: no cover - unused by these use cases

    async def object_exists(self, *, key: str) -> bool:
        return key in self._existing_keys

    async def delete(self, *, key: str) -> None:  # pragma: no cover - unused by these use cases
        self._existing_keys.discard(key)

    async def download(self, *, key: str) -> bytes:  # pragma: no cover - unused by these use cases
        raise NotImplementedError

    async def upload(
        self, *, key: str, content: bytes, content_type: str
    ) -> None:  # pragma: no cover - unused by these use cases
        raise NotImplementedError

    async def list_prefix(
        self, *, prefix: str
    ) -> list[str]:  # pragma: no cover - unused by these use cases
        raise NotImplementedError
