from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aether.domain.entities import ChunkDraft, DocumentStatus
from aether.ports.malware_scan import ScanResult


class FakeObjectStorage:
    def __init__(self, *, objects: dict[str, bytes] | None = None) -> None:
        self._objects = objects or {}

    async def download(self, *, key: str) -> bytes:
        return self._objects[key]

    async def object_exists(self, *, key: str) -> bool:
        return key in self._objects

    async def delete(self, *, key: str) -> None:
        self._objects.pop(key, None)

    def presign_upload(self, **kwargs: object) -> object:  # pragma: no cover - unused by handler
        raise NotImplementedError

    def presign_download(self, **kwargs: object) -> object:  # pragma: no cover - unused by handler
        raise NotImplementedError


class FakeScanner:
    def __init__(self, *, malicious_keys: set[bytes] | None = None) -> None:
        self._malicious = malicious_keys or set()

    async def scan(self, content: bytes) -> ScanResult:
        if content in self._malicious:
            return ScanResult(clean=False, signature="Fake-Test-Signature")
        return ScanResult(clean=True, signature=None)


@dataclass(frozen=True, slots=True)
class _FailureRecord:
    stage: str
    reason: str


class FakeIngestionRepository:
    def __init__(self) -> None:
        self.status_history: list[DocumentStatus] = []
        self.failures: list[_FailureRecord] = []
        self.inserted_chunks: list[ChunkDraft] = []

    async def update_status(
        self, workspace_id: UUID, document_id: UUID, *, status: DocumentStatus
    ) -> None:
        self.status_history.append(status)

    async def mark_failed(
        self, workspace_id: UUID, document_id: UUID, *, stage: str, reason: str
    ) -> None:
        self.failures.append(_FailureRecord(stage=stage, reason=reason))

    async def insert_chunks_and_advance(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        chunks: list[ChunkDraft],
        next_status: DocumentStatus,
    ) -> None:
        self.inserted_chunks.extend(chunks)
        self.status_history.append(next_status)
