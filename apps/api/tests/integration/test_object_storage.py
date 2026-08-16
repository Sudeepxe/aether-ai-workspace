"""Real-MinIO proof for ObjectStoragePort (issue #44, ADR-3.8) — a
presigned URL is meaningless if it's never actually exercised as a
real HTTP upload; every test here drives the real POST/GET flow a
browser would, not just asserts the SDK returned *something*.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from aether.adapters.minio.object_storage import MinioObjectStorage

pytestmark = pytest.mark.integration


def _upload(presigned, *, content: bytes, content_type: str) -> httpx.Response:
    files = {"file": ("upload.bin", content, content_type)}
    return httpx.post(presigned.url, data=presigned.fields, files=files, timeout=10.0)


async def test_presigned_upload_accepts_a_real_upload_and_object_becomes_retrievable(
    object_storage: MinioObjectStorage,
) -> None:
    key = f"test/{uuid.uuid4()}.txt"
    content = b"hello from a real presigned upload"

    presigned = object_storage.presign_upload(
        key=key, content_type="text/plain", max_size_bytes=1000, expires_seconds=900
    )
    resp = _upload(presigned, content=content, content_type="text/plain")
    assert resp.status_code in (200, 204), resp.text

    assert await object_storage.object_exists(key=key) is True

    download_url = object_storage.presign_download(key=key, expires_seconds=900)
    downloaded = httpx.get(download_url, timeout=10.0)
    assert downloaded.status_code == 200
    assert downloaded.content == content


async def test_presigned_upload_rejects_a_mismatched_content_type(
    object_storage: MinioObjectStorage,
) -> None:
    """The POST policy's Content-Type condition is validated against
    the *form field* value (which presign_upload embeds in
    presigned.fields), not the multipart file part's own MIME header —
    a client that tampers with the form field to claim a different
    type than what was authorized must be rejected."""
    key = f"test/{uuid.uuid4()}.txt"
    presigned = object_storage.presign_upload(
        key=key, content_type="text/plain", max_size_bytes=1000, expires_seconds=900
    )
    fields = {**presigned.fields, "Content-Type": "application/pdf"}
    files = {"file": ("upload.bin", b"x", "application/pdf")}

    resp = httpx.post(presigned.url, data=fields, files=files, timeout=10.0)

    assert resp.status_code >= 400
    assert await object_storage.object_exists(key=key) is False


async def test_presigned_upload_rejects_a_body_over_the_size_limit(
    object_storage: MinioObjectStorage,
) -> None:
    key = f"test/{uuid.uuid4()}.bin"
    presigned = object_storage.presign_upload(
        key=key, content_type="application/octet-stream", max_size_bytes=10, expires_seconds=900
    )

    resp = _upload(presigned, content=b"x" * 100, content_type="application/octet-stream")

    assert resp.status_code >= 400
    assert await object_storage.object_exists(key=key) is False


async def test_presigned_upload_rejects_an_upload_to_a_different_key(
    object_storage: MinioObjectStorage,
) -> None:
    """The POST policy's key condition is an equals-condition (not just
    starts-with) — a client can't reuse one presigned form to write to
    an arbitrary object name."""
    key = f"test/{uuid.uuid4()}.txt"
    other_key = f"test/{uuid.uuid4()}.txt"
    presigned = object_storage.presign_upload(
        key=key, content_type="text/plain", max_size_bytes=1000, expires_seconds=900
    )
    fields = {**presigned.fields, "key": other_key}
    files = {"file": ("upload.bin", b"x", "text/plain")}

    resp = httpx.post(presigned.url, data=fields, files=files, timeout=10.0)

    assert resp.status_code >= 400
    assert await object_storage.object_exists(key=other_key) is False


async def test_object_exists_is_false_for_an_unknown_key(
    object_storage: MinioObjectStorage,
) -> None:
    assert await object_storage.object_exists(key=f"test/{uuid.uuid4()}") is False


async def test_delete_removes_the_object_and_is_idempotent(
    object_storage: MinioObjectStorage,
) -> None:
    key = f"test/{uuid.uuid4()}.txt"
    presigned = object_storage.presign_upload(
        key=key, content_type="text/plain", max_size_bytes=1000, expires_seconds=900
    )
    _upload(presigned, content=b"to be deleted", content_type="text/plain")
    assert await object_storage.object_exists(key=key) is True

    await object_storage.delete(key=key)
    assert await object_storage.object_exists(key=key) is False

    await object_storage.delete(key=key)  # deleting an already-gone key must not raise


async def test_download_returns_the_exact_uploaded_bytes(
    object_storage: MinioObjectStorage,
) -> None:
    """Worker-only method (ADR-3.8: the API tier never reads file bytes
    directly, but the ingestion pipeline's worker process must)."""
    key = f"test/{uuid.uuid4()}.bin"
    content = bytes(range(256)) * 100  # binary content, not just text
    presigned = object_storage.presign_upload(
        key=key,
        content_type="application/octet-stream",
        max_size_bytes=len(content) + 10,
        expires_seconds=900,
    )
    _upload(presigned, content=content, content_type="application/octet-stream")

    downloaded = await object_storage.download(key=key)

    assert downloaded == content
