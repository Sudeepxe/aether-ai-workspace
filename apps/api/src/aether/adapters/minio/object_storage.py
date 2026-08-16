"""S3-compatible ObjectStoragePort implementation (§3.2.13, ADR-3.8).

Uses the ``minio`` client — S3-API-compatible, so this same adapter
targets MinIO in dev and real S3 (or R2) in a cloud profile by swapping
credentials/endpoint only, no code change (matching the "content-
addressed keys + presigned URLs" design's own portability intent).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from minio import Minio
from minio.datatypes import PostPolicy
from minio.error import S3Error

from aether.ports.storage import PresignedUpload


class MinioObjectStorage:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        bucket: str,
    ) -> None:
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket
        # The upload target URL a browser POSTs to — built from the same
        # endpoint/secure the client itself was configured with, not
        # read back out of the client (which exposes no public accessor
        # for it); in this project's dev topology the API process and
        # the browser share one host, so this is reachable from both.
        self._upload_base_url = f"{'https' if secure else 'http'}://{endpoint}/{bucket}"

    def presign_upload(
        self, *, key: str, content_type: str, max_size_bytes: int, expires_seconds: int
    ) -> PresignedUpload:
        policy = PostPolicy(self._bucket, _expiration(expires_seconds))
        policy.add_equals_condition("key", key)
        policy.add_equals_condition("Content-Type", content_type)
        policy.add_content_length_range_condition(1, max_size_bytes)
        # presigned_post_policy() returns only the signing fields
        # (x-amz-*, policy) — it does NOT include "key"/"Content-Type"
        # as actual form fields, even though the policy has *conditions*
        # requiring them. Without adding them explicitly here, the
        # upload fails server-side ("the name of the uploaded key is
        # missing") since S3/MinIO never receives a key to write to.
        fields = self._client.presigned_post_policy(policy)
        fields["key"] = key
        fields["Content-Type"] = content_type
        return PresignedUpload(url=self._upload_base_url, fields=fields)

    def presign_download(self, *, key: str, expires_seconds: int) -> str:
        return self._client.presigned_get_object(
            self._bucket, key, expires=timedelta(seconds=expires_seconds)
        )

    async def object_exists(self, *, key: str) -> bool:
        def _stat() -> bool:
            try:
                self._client.stat_object(self._bucket, key)
                return True
            except S3Error as exc:
                if exc.code == "NoSuchKey":
                    return False
                raise

        return await asyncio.to_thread(_stat)

    async def delete(self, *, key: str) -> None:
        # remove_object is already idempotent at the S3 API level (a
        # DELETE on a missing key succeeds, per spec) — no special
        # NoSuchKey handling needed here, unlike object_exists.
        await asyncio.to_thread(self._client.remove_object, self._bucket, key)

    async def ensure_bucket(self) -> None:
        """Idempotent bucket provisioning, called once at process
        startup — dev MinIO starts with no buckets; a real cloud profile
        would provision the bucket out-of-band (Terraform/console) and
        this becomes a no-op check, not the source of truth."""

        def _ensure() -> None:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)

        await asyncio.to_thread(_ensure)


def _expiration(expires_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=expires_seconds)
