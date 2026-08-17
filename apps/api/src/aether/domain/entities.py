"""Sprint 1 domain entities (Blueprint §8.1 schema catalog, Sprint 1 slice).

Pure dataclasses — no I/O, no third-party imports beyond the stdlib, per
the domain-purity import rule (ADR-3.4, enforced by import-linter).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
class Citation:
    """One grounded message's reference to a retrieved chunk (ADR-8.6,
    §8.1). ``document_title``/``section_path``/``page_start``/
    ``page_end`` are a provenance *snapshot*, captured once at write
    time — not a live join to ``chunks`` — so a citation survives
    intact even after its source document/chunk is later deleted.
    ``chunk_id`` is nullable for exactly that reason: null means "the
    source this once pointed to is gone" (the UI renders a "source
    removed" state), not "this citation is broken"."""

    id: UUID
    workspace_id: UUID
    message_id: UUID
    chunk_id: UUID | None
    document_title: str
    section_path: str
    page_start: int | None
    page_end: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CitationDraft:
    """The input shape for creating a citation — deliberately distinct
    from ``Citation`` (the persisted row), matching ``ChunkDraft``'s
    precedent in this file: no id/workspace_id/message_id/created_at,
    those are persistence concerns the repository adapter assigns."""

    chunk_id: UUID | None
    document_title: str
    section_path: str
    page_start: int | None
    page_end: int | None


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


class UsageEventKind(StrEnum):
    CHAT = "chat"
    EMBED = "embed"
    REWRITE = "rewrite"
    COMPACT = "compact"


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """One append-only ledger row (FR-AD-2/3, §8.1). ``cost_microcents``
    is 1e-6 of a US cent (1e-8 of a dollar) — fine enough granularity
    that even the cheapest per-token pricing never rounds to zero."""

    id: UUID
    workspace_id: UUID
    user_id: UUID | None
    kind: UsageEventKind
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_microcents: int
    generation_id: UUID | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class Budget:
    """One row per workspace (§3.2.14). ``settled_microcents`` is
    settled synchronously, per usage event, by the request that
    generated it — see adapters/postgres/usage_ledger.py's module
    docstring for why that's still correct under concurrent load (each
    settlement is one atomic transaction) even though §8's F-4 finding
    calls for batching it for throughput, not correctness."""

    workspace_id: UUID
    monthly_limit_microcents: int
    soft_pct: int
    current_period_start: date
    settled_microcents: int
    updated_at: datetime


class DocumentStatus(StrEnum):
    """A document's ingestion-pipeline stage (FR-KB-2's visible
    pipeline, §3.2.7). Each value is both "where it is" while in
    progress and, for QUEUED..EMBEDDING, "what's about to run next" —
    the terminal states are READY and FAILED."""

    QUEUED = "queued"
    SCANNING = "scanning"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Document:
    """A workspace's uploaded source file and its ingestion state
    (§8.1). ``object_key`` is content-addressed (SHA-256, ADR-3.8) —
    ``content_sha256`` and the key's derivation are the same hash, kept
    as separate columns because one names *what* was uploaded (dedupe
    key) and the other names *where* the bytes live (storage key),
    which happen to coincide today but are conceptually distinct.
    ``version``/``superseded_by`` exist for FR-KB-6 (re-ingestion on
    update) — Phase 2, unused until then but present now because
    retrofitting a version column after the fact is the kind of thing
    this project's schema discipline avoids."""

    id: UUID
    workspace_id: UUID
    filename: str
    content_sha256: str
    mime: str
    size_bytes: int
    object_key: str
    status: DocumentStatus
    failure_stage: str | None
    failure_reason: str | None
    version: int
    superseded_by: UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable unit of a document (§8.1, ADR-6.2). Provenance
    (``section_path``, ``page_start``/``page_end``, ``char_start``/
    ``char_end``) is captured at chunk-creation time, not
    reverse-engineered later — pages are nullable since not every
    source format has them (MD/TXT/HTML don't; PDF does).
    ``embedding`` is a plain list of floats at the domain layer (never
    a pgvector-specific type — domain stays pure, ADR-3.4); the
    Postgres adapter handles the actual vector-column conversion.
    ``embedding_model``/``embedding_version`` are None until the
    embedding stage completes; a chunk row exists (for chunking-stage
    provenance/debugging) before it has a vector."""

    id: UUID
    workspace_id: UUID
    document_id: UUID
    section_path: str
    page_start: int | None
    page_end: int | None
    char_start: int
    char_end: int
    content: str
    content_sha256: str
    token_count: int
    embedding: list[float] | None
    embedding_model: str | None
    embedding_version: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """The structure-aware chunker's own output shape (ADR-6.2,
    app/ingestion/chunking.py) — deliberately distinct from ``Chunk``
    above (the full persisted-row shape). A draft carries everything
    the *algorithm* produces; it has no identity yet (no id,
    workspace_id, document_id, content_sha256) — those are persistence
    concerns the repository adapter assigns on insert, not something
    the pure chunking function should need to know about."""

    content: str
    section_path: str
    page_start: int | None
    page_end: int | None
    char_start: int
    char_end: int
    token_count: int
