"""Domain-level typed errors (Blueprint §3.6.1 taxonomy).

Use cases raise these; the HTTP layer maps them to Problem+JSON responses.
Domain errors never carry enumeration-unsafe detail (e.g. "user not
found" vs "wrong password" — see AuthenticationFailedError, deliberately one
error for both cases per §7.4/§7.5 enumeration-safety).
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain-layer errors."""


class AuthenticationFailedError(DomainError):
    """Login failed — deliberately raised for both "no such user" and
    "wrong password" so the HTTP layer cannot leak which one occurred."""


class EmailAlreadyRegisteredError(DomainError):
    """Registration attempted with an email that already has an account."""


class InvalidRefreshTokenError(DomainError):
    """A refresh token was not found, expired, or otherwise unusable."""


class InvalidAccessTokenError(DomainError):
    """An access token failed verification — expired, bad signature, wrong
    algorithm, unknown kid, or malformed. One error for all cases: callers
    (the HTTP layer) must not be able to distinguish *why* a token failed,
    only that it did (same enumeration-safety posture as AuthenticationFailedError)."""


class RefreshTokenReusedError(DomainError):
    """A refresh token already marked used was presented again outside the
    grace window — the whole family is treated as compromised (ADR-7.2)."""


class UserNotFoundError(DomainError):
    """Referenced user id does not exist (internal-use; never surfaced to
    an unauthenticated caller as distinct from AuthenticationFailedError)."""


class WorkspaceNotFoundError(DomainError):
    """Referenced workspace id does not exist (or the caller has no
    membership in it — RLS/route guards make those the same outcome)."""


class MembershipNotFoundError(DomainError):
    """Target user is not a member of the workspace."""


class LastOwnerProtectionError(DomainError):
    """Refused: this would leave the workspace with zero Owners (FR-ID-4
    invariant — every workspace has >= 1 Owner at all times)."""


class WorkspaceConcurrencyConflictError(DomainError):
    """An If-Match/ETag precondition on a workspace mutation did not match
    the current state — someone else changed it first."""


class InvalidInvitationError(DomainError):
    """An invitation token was unknown, expired, or already consumed. One
    error for all three cases, deliberately — distinguishing them in the
    response would let a caller enumerate valid-but-expired invitations
    (same enumeration-safety posture as AuthenticationFailedError)."""


class InvalidPasswordResetTokenError(DomainError):
    """A password-reset token was unknown, expired, or already consumed.
    One error for all three cases (ADR-11.1: enumeration-safe path)."""


class ThreadNotFoundError(DomainError):
    """Referenced thread id does not exist in this workspace (or is
    soft-deleted)."""


class GenerationNotFoundError(DomainError):
    """Referenced generation id has no live entry in the Redis stream
    buffer — either it never existed, already completed and its buffer
    TTL expired, or it belongs to a different workspace."""


class BudgetExhaustedError(DomainError):
    """Refused before any provider call (§3.2.14): this request's cost
    ceiling estimate would exceed the workspace's (or the global) monthly
    limit. Hard-limit enforcement, not a warning — the soft-limit case
    admits the request and carries a banner/header instead."""


class NoProviderAvailableError(DomainError):
    """Every provider in the routing fallback chain has an open circuit
    breaker (§3.2.4) — the router-level equivalent of a full outage."""


class BudgetConcurrencyConflictError(DomainError):
    """An If-Match/ETag precondition on a budget mutation did not match
    the current state — same posture as WorkspaceConcurrencyConflictError,
    kept as its own type since budgets and workspaces are distinct
    entities with independently evolving updated_at values."""


class DocumentNotFoundError(DomainError):
    """Referenced document id does not exist in this workspace (or is
    soft-deleted)."""


class DocumentUploadIncompleteError(DomainError):
    """documents:confirm was called but the object storage key it names
    doesn't actually exist yet — the client claims an upload finished
    that the storage backend has no record of."""


class MessageNotFoundError(DomainError):
    """Referenced message id does not exist in this workspace/thread."""


class FeedbackNotEligibleError(DomainError):
    """Feedback was submitted against a non-assistant message — only an
    assistant's own turns are feedback-eligible (FR-CH-6): feedback on a
    user's own message is meaningless."""
