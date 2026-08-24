from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from aether.domain.entities import DeletionJobStatus, MembershipRole


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class UpdateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    settings: dict[str, Any] = Field(default_factory=dict)
    model_policy: dict[str, Any] = Field(default_factory=dict)


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    settings: dict[str, Any]
    model_policy: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DeletionJobResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    requested_by: UUID
    status: DeletionJobStatus
    evidence: dict[str, Any]
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class MembershipResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    role: MembershipRole
    created_at: datetime
    updated_at: datetime


class UpdateMemberRoleRequest(BaseModel):
    role: MembershipRole


class CreateInvitationRequest(BaseModel):
    email: EmailStr
    role: MembershipRole


class InvitationResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    email: str
    role: MembershipRole
    expires_at: datetime
    created_at: datetime
