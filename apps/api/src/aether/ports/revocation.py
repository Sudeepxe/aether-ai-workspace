"""Session revocation port (Blueprint §3.2.2, ADR-3.6)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class RevocationPort(Protocol):
    async def deny(self, jti: UUID, *, ttl_seconds: int) -> None: ...

    async def is_denied(self, jti: UUID) -> bool: ...
