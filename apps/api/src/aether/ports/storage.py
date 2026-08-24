"""ObjectStoragePort (§3.2.13, ADR-3.8): file bytes never transit the
API tier. The API only ever issues short-TTL, single-operation presigned
URLs and checks/removes objects by key — it never reads or writes file
bytes itself.

``presign_upload``/``presign_download`` are plain (non-async) methods:
generating a presigned URL is local HMAC signing against already-held
credentials, not a network call — the same reasoning
``PasswordHasherPort``'s (CPU-bound, also synchronous) methods rely on.
``object_exists``/``delete`` *do* make a real network call to the
storage backend, so they're async.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """A presigned POST (not a bare PUT): S3-compatible presigned PUT
    URLs can't themselves constrain content-type/size (ADR-3.8's
    "content-type-and-size-constrained" requirement) — only the POST
    policy mechanism can. The caller submits ``fields`` as multipart
    form fields alongside the file bytes to ``url``."""

    url: str
    fields: dict[str, str]


class ObjectStoragePort(Protocol):
    def presign_upload(
        self, *, key: str, content_type: str, max_size_bytes: int, expires_seconds: int
    ) -> PresignedUpload:
        """A single-operation, short-TTL presigned POST — the upload
        succeeds only if the actual request matches ``content_type``
        exactly and body size falls within ``(0, max_size_bytes]``."""
        ...

    def presign_download(self, *, key: str, expires_seconds: int) -> str:
        """A single-operation, short-TTL presigned GET URL."""
        ...

    async def object_exists(self, *, key: str) -> bool: ...

    async def delete(self, *, key: str) -> None:
        """Idempotent: deleting an already-absent key is not an error
        (matches FR-KB-5's provable-deletion posture — a delete must be
        safely retryable, never blocked by "it's already gone")."""
        ...

    async def download(self, *, key: str) -> bytes:
        """Reads an object's bytes directly — worker-only (ADR-3.8's
        "file bytes never transit the API tier" is about the API tier
        specifically; the ingestion pipeline's worker process is the one
        place that legitimately needs the actual bytes to scan/parse
        them)."""
        ...

    async def upload(self, *, key: str, content: bytes, content_type: str) -> None:
        """Writes bytes directly — worker-only, the server-side
        counterpart to ``presign_upload``'s client-driven flow. The
        workspace-export saga (issue #85) is the one place the worker
        itself produces a new object (an assembled archive) rather than
        just reading or removing one that a client uploaded."""
        ...

    async def list_prefix(self, *, prefix: str) -> list[str]:
        """Every key currently under ``prefix``, worker-only. The
        deletion-verification saga's (issue #86) one genuinely
        independent check: unlike a DB residue sweep, which can only
        ever prove "no row references this key", listing storage
        directly proves the *bytes themselves* are gone — the real
        counterpart to a bug where the purge step's own key list was
        wrong or incomplete."""
        ...
