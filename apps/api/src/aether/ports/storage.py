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
