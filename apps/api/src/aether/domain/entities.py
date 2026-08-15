"""Sprint 1 domain entities (Blueprint §8.1 schema catalog, Sprint 1 slice).

Pure dataclasses — no I/O, no third-party imports beyond the stdlib, per
the domain-purity import rule (ADR-3.4, enforced by import-linter).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class MembershipRole(StrEnum):
    """RBAC roles, ordered least to most privileged (Blueprint §7.3)."""

    VIEWER = "viewer"
    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    email: str
    display_name: str
    password_hash: str | None
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Identity:
    id: UUID
    user_id: UUID
    provider: str
    provider_subject: str
    email_verified: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Workspace:
    id: UUID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class Membership:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    role: MembershipRole
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """Represents one node in a refresh-token family (ADR-7.2).

    ``token_hash`` is the hash of the opaque token value — the plaintext
    token is never persisted or logged, only returned to the client once
    at issuance time.
    """

    id: UUID
    user_id: UUID
    family_id: UUID
    token_hash: str
    device_fingerprint: str
    used_at: datetime | None
    successor_id: UUID | None
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime
