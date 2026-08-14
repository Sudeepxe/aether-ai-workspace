"""Security-primitive ports: hashing, tokens, clock, id generation.

Kept behind ports (not called directly from app/) for the same reason the
blueprint's Ch.6 eval suite tests orchestration against fake LLMPort/
VectorSearchPort: app use cases stay testable with deterministic fakes,
and a library swap (e.g. a different JWT library) never touches app code.
ClockPort/IdPort are named explicitly in the blueprint's own port catalog
(§3.3 diagram); PasswordHasherPort/TokenPort are natural extensions of the
same pattern for Sprint 1's needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


class PasswordHasherPort(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password: str, password_hash: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    sub: UUID
    jti: UUID
    issued_at: datetime
    expires_at: datetime


class TokenPort(Protocol):
    """EdDSA-signed access JWTs (ADR-7.2). Algorithm and key set are fixed
    by the adapter — never selectable from caller or token input."""

    def issue_access_token(self, *, user_id: UUID, jti: UUID) -> str: ...

    def verify_access_token(self, token: str) -> AccessTokenClaims: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class IdPort(Protocol):
    def new_id(self) -> UUID: ...
