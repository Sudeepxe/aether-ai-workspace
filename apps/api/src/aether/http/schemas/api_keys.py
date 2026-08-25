from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from aether.domain.entities import ApiKeyScope


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[ApiKeyScope] = Field(min_length=1)
    expires_at: datetime | None = None


class CreateApiKeyResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    prefix: str
    name: str
    scopes: list[ApiKeyScope]
    expires_at: datetime | None
    created_at: datetime
    raw_key: str
    """Shown exactly once — this field never appears on any other
    response (GET .../api-keys returns ApiKeyResponse, without it)."""


class ApiKeyResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    prefix: str
    name: str
    scopes: list[ApiKeyScope]
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyResponse]
