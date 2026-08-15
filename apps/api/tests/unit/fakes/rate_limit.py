from __future__ import annotations

from aether.ports.rate_limit import RateLimitResult


class FakeRateLimiter:
    """Always allows by default (existing tests aren't exercising rate
    limiting, so it must be transparent to them); set ``deny_after`` to
    make it start refusing after N calls, for tests that need the
    denied path."""

    def __init__(self, *, deny_after: int | None = None) -> None:
        self._deny_after = deny_after
        self.calls: list[tuple[str, int, int]] = []

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        self.calls.append((key, limit, window_seconds))
        allowed = self._deny_after is None or len(self.calls) <= self._deny_after
        remaining = max(0, limit - len(self.calls)) if allowed else 0
        return RateLimitResult(allowed=allowed, limit=limit, remaining=remaining, reset_seconds=30)
