from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aether.app.llm.circuit_breaker import BreakerState, CircuitBreaker
from tests.unit.fakes.auth import FakeClock

pytestmark = pytest.mark.unit

_FAILURE_THRESHOLD = 3
_COOLDOWN_SECONDS = 30


def _breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(
        clock=clock, failure_threshold=_FAILURE_THRESHOLD, cooldown_seconds=_COOLDOWN_SECONDS
    )


def test_starts_closed_and_allows_calls() -> None:
    breaker = _breaker(FakeClock(start=datetime.now(UTC)))
    assert breaker.state == BreakerState.CLOSED
    assert breaker.is_open() is False


def test_opens_after_consecutive_failure_threshold() -> None:
    breaker = _breaker(FakeClock(start=datetime.now(UTC)))
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == BreakerState.CLOSED  # below threshold
    assert breaker.is_open() is False

    breaker.record_failure()  # 3rd consecutive failure trips it
    assert breaker.state == BreakerState.OPEN
    assert breaker.is_open() is True


def test_success_resets_consecutive_failure_count() -> None:
    breaker = _breaker(FakeClock(start=datetime.now(UTC)))
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    # Only 2 consecutive failures since the reset — still below threshold.
    assert breaker.state == BreakerState.CLOSED
    assert breaker.is_open() is False


def test_open_transitions_to_half_open_after_cooldown_elapses() -> None:
    clock = FakeClock(start=datetime.now(UTC))
    breaker = _breaker(clock)
    for _ in range(_FAILURE_THRESHOLD):
        breaker.record_failure()
    assert breaker.is_open() is True

    clock.advance(timedelta(seconds=_COOLDOWN_SECONDS - 1))
    assert breaker.is_open() is True  # cooldown not yet elapsed
    assert breaker.state == BreakerState.OPEN

    clock.advance(timedelta(seconds=2))  # now past the cooldown
    assert breaker.is_open() is False  # is_open() itself drives the transition
    assert breaker.state == BreakerState.HALF_OPEN


def test_half_open_success_closes_the_breaker() -> None:
    clock = FakeClock(start=datetime.now(UTC))
    breaker = _breaker(clock)
    for _ in range(_FAILURE_THRESHOLD):
        breaker.record_failure()
    clock.advance(timedelta(seconds=_COOLDOWN_SECONDS + 1))
    assert breaker.is_open() is False
    assert breaker.state == BreakerState.HALF_OPEN

    breaker.record_success()

    assert breaker.state == BreakerState.CLOSED
    assert breaker.is_open() is False


def test_half_open_failure_reopens_immediately_without_a_fresh_threshold() -> None:
    clock = FakeClock(start=datetime.now(UTC))
    breaker = _breaker(clock)
    for _ in range(_FAILURE_THRESHOLD):
        breaker.record_failure()
    clock.advance(timedelta(seconds=_COOLDOWN_SECONDS + 1))
    assert breaker.is_open() is False  # -> HALF_OPEN

    # A single failed probe re-opens it — HALF_OPEN doesn't get a fresh
    # multi-failure grace period like CLOSED does.
    breaker.record_failure()

    assert breaker.state == BreakerState.OPEN
    assert breaker.is_open() is True


def test_reopening_from_half_open_restarts_the_cooldown_clock() -> None:
    clock = FakeClock(start=datetime.now(UTC))
    breaker = _breaker(clock)
    for _ in range(_FAILURE_THRESHOLD):
        breaker.record_failure()
    clock.advance(timedelta(seconds=_COOLDOWN_SECONDS + 1))
    assert breaker.is_open() is False  # -> HALF_OPEN
    breaker.record_failure()  # -> OPEN again, opened_at reset to "now"

    clock.advance(timedelta(seconds=_COOLDOWN_SECONDS - 1))
    assert breaker.is_open() is True  # cooldown restarted, not yet elapsed
