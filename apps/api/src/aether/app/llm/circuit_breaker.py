"""Per-provider circuit breaker (§3.2.4). Pure state machine — no I/O,
only a ClockPort — so it's directly unit-testable without a real
provider or a real clock.

States: CLOSED (normal) -> OPEN (after ``failure_threshold`` consecutive
failures; calls rejected outright) -> HALF_OPEN (after ``cooldown_seconds``
elapses, exactly one probe call is allowed through) -> CLOSED on success
or back to OPEN on failure.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from aether.ports.security import ClockPort

_DEFAULT_FAILURE_THRESHOLD = 3
_DEFAULT_COOLDOWN_SECONDS = 30


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        clock: ClockPort,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: int = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._clock = clock
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: datetime | None = None

    @property
    def state(self) -> BreakerState:
        return self._state

    def is_open(self) -> bool:
        """Also drives the OPEN -> HALF_OPEN transition: called once per
        routing decision, so "is it open" and "has the cooldown elapsed"
        are naturally checked together — no separate timer/poller."""
        if self._state is not BreakerState.OPEN:
            return False
        assert self._opened_at is not None  # noqa: S101 — invariant: OPEN is only entered alongside setting this
        elapsed = (self._clock.now() - self._opened_at).total_seconds()
        if elapsed >= self._cooldown_seconds:
            self._state = BreakerState.HALF_OPEN
            return False
        return True

    def record_success(self) -> None:
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if (
            self._state is BreakerState.HALF_OPEN
            or self._consecutive_failures >= self._failure_threshold
        ):
            self._state = BreakerState.OPEN
            self._opened_at = self._clock.now()
