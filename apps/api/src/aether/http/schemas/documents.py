from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from aether.domain.entities import DocumentStatus

_MAX_SIZE_BYTES = 52_428_800  # 50MB, matching the documents CHECK constraint (FR-KB-1)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class InitiateUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(gt=0, le=_MAX_SIZE_BYTES)
    content_sha256: str

    @field_validator("content_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if not _SHA256_HEX.fullmatch(value):
            raise ValueError("content_sha256 must be 64 lowercase hex characters")
        return value


class InitiateUploadResponse(BaseModel):
    document_id: UUID
    object_key: str
    upload_url: str
    upload_fields: dict[str, str]


class ConfirmUploadRequest(BaseModel):
    document_id: UUID
    object_key: str = Field(min_length=1, max_length=600)
    filename: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(gt=0, le=_MAX_SIZE_BYTES)
    content_sha256: str

    @field_validator("content_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if not _SHA256_HEX.fullmatch(value):
            raise ValueError("content_sha256 must be 64 lowercase hex characters")
        return value


class DocumentResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    filename: str
    mime: str
    size_bytes: int
    status: DocumentStatus
    failure_stage: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    next_cursor: str | None
