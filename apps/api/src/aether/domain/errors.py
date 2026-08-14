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
