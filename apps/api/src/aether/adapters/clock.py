"""System clock adapter (Blueprint §3.3 ClockPort). Real time; tests use a
fake implementing the same Protocol for determinism (Ch.10 testing standard:
"frozen clocks via ClockPort")."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
