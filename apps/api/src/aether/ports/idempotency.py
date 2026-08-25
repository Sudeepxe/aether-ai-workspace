"""Idempotency-replay-store port (ADR-4.6, §4.2's conventions table):
Redis-backed, 24h TTL — the generic mechanism behind every plain
mutating-POST route's ``Idempotency-Key`` header support. Deliberately
separate from the two existing narrower, already-correct mechanisms
(chat's client-generated ``message_id``, document-confirm's
document-row-is-the-guard idempotency) — those solve a more specific
problem than "replay the exact prior HTTP response" and aren't built on
this port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IdempotencySnapshot:
    body_sha256: str
    status_code: int
    response_body: str


class IdempotencyStorePort(Protocol):
    async def get(self, key: str) -> IdempotencySnapshot | None: ...

    async def set(self, key: str, snapshot: IdempotencySnapshot, *, ttl_seconds: int) -> None: ...
