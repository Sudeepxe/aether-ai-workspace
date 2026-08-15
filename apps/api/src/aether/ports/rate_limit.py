"""Rate-limit port (§3.6.3, §3.2.12): Redis Lua atomic token buckets,
app-tier limiting by identity (user/workspace/API key) — coarse per-IP
anti-abuse is an edge-tier (reverse proxy) concern, out of this port's
scope by design, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int


class RateLimitPort(Protocol):
    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        """Atomically consumes one token from ``key``'s bucket (capacity
        ``limit``, refilling continuously to ``limit`` every
        ``window_seconds``) and reports whether the request is allowed."""
        ...
