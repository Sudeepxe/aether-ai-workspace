"""Sprint 1 domain entities (Blueprint §8.1 schema catalog, Sprint 1 slice).

Pure dataclasses — no I/O, no third-party imports beyond the stdlib, per
the domain-purity import rule (ADR-3.4, enforced by import-linter).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
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
    settings: dict[str, Any]
    model_policy: dict[str, Any]
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
class Invitation:
    """A single-use, expiring workspace invitation (FR-ID-3, §8.1)."""

    id: UUID
    workspace_id: UUID
    email: str
    role: MembershipRole
    token_hash: str
    invited_by: UUID
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable audit-log entry (FR-AD-1, §8.1). ``workspace_id`` is
    None for system/auth-plane events (e.g. login) that precede any
    workspace context."""

    id: UUID
    workspace_id: UUID | None
    actor_user_id: UUID | None
    actor_key_id: UUID | None
    action: str
    target_type: str
    target_id: UUID
    metadata: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PasswordResetToken:
    """A single-use, hashed, 30-minute-TTL password-reset token (ADR-11.1).

    Deliberately its own table, not a reuse of ``invitations`` or
    ``refresh_tokens`` — the shape is superficially similar (single-use,
    hashed, expiring) but the semantics (who may consume it, what
    consuming it does) are distinct enough that overloading an existing
    table would only save one small migration at the cost of conflating
    two different security-sensitive flows in one table's grants and
    RLS-exemption reasoning.
    """

    id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageStatus(StrEnum):
    """A persisted message's terminal state (§8.1). Deliberately excludes
    'error': an error before any token flowed persists no message row at
    all, and an error mid-stream persists whatever content already
    accumulated as PARTIAL — 'error' exists only as a done-*event*
    status (see domain/streaming.py's GenerationStatus), never a
    persisted message status."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Thread:
    id: UUID
    workspace_id: UUID
    created_by: UUID
    title: str | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class Message:
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    seq: int
    role: MessageRole
    content: str
    status: MessageStatus
    client_message_id: str | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_microcents: int | None
    grounded: bool
    created_at: datetime


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
