"""PostgresChunkSearch/PooledChunkSearch's translation of expected
infrastructure failures into ChunkSearchUnavailableError (Phase 2 of
docs/REMEDIATION_PLAN.md) — exercised against the real adapter code with
duck-typed stand-ins for asyncpg.Connection/Pool (this repo's existing
Fake* convention, not a mock library), not a fake standing in for the
adapter itself. Complements test_hybrid_search.py, which covers the
app-layer (HybridSearch) side of the same contract through the Port.
"""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from aether.adapters.postgres.chunk_search import PooledChunkSearch, PostgresChunkSearch
from aether.ports.retrieval import ChunkSearchUnavailableError

pytestmark = pytest.mark.unit

_EXPECTED_INFRA_FAILURES = [
    ConnectionError("refused"),
    TimeoutError("deadline exceeded"),
    asyncpg.PostgresConnectionError("connection lost"),
    asyncpg.QueryCanceledError("canceling statement due to statement timeout"),
    asyncpg.ObjectNotInPrerequisiteStateError("index is not yet valid"),
]


class _FailingConnection:
    """Duck-typed stand-in for asyncpg.Connection: only `.fetch()` is
    ever called by search_vector, so only it needs to exist here."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def fetch(self, *_args: object, **_kwargs: object) -> list[object]:
        raise self._error


@pytest.mark.parametrize("error", _EXPECTED_INFRA_FAILURES, ids=lambda e: type(e).__name__)
async def test_expected_infra_failures_translate_to_chunk_search_unavailable(
    error: Exception,
) -> None:
    adapter = PostgresChunkSearch(_FailingConnection(error))  # type: ignore[arg-type]

    with pytest.raises(ChunkSearchUnavailableError) as exc_info:
        await adapter.search_vector(uuid4(), embedding=[0.1, 0.2], limit=5)

    # The wrapper preserves the original exception via `raise ... from`,
    # so a caller (or a log/metric, see hybrid_search.py) can still see
    # which underlying failure actually happened.
    assert exc_info.value.__cause__ is error


@pytest.mark.parametrize(
    "error", [ValueError("bad argument"), NameError("undefined variable"), KeyError("oops")]
)
async def test_unexpected_exceptions_are_not_wrapped_or_swallowed(error: Exception) -> None:
    """A real bug in our own code (wrong argument, typo, ...) must
    surface as itself, not get relabeled as an infra outage."""
    adapter = PostgresChunkSearch(_FailingConnection(error))  # type: ignore[arg-type]

    with pytest.raises(type(error)):
        await adapter.search_vector(uuid4(), embedding=[0.1, 0.2], limit=5)


class _FailingPool:
    """Duck-typed stand-in for asyncpg.Pool whose acquire() itself fails
    — a real scenario (e.g. pool exhaustion hitting its own acquire
    timeout) that happens before PostgresChunkSearch.search_vector's own
    try/except ever gets a connection to work with."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def acquire(self) -> None:
        raise self._error


async def test_pool_acquire_timeout_also_translates_to_chunk_search_unavailable() -> None:
    pool = _FailingPool(TimeoutError("pool exhausted"))
    adapter = PooledChunkSearch(pool)  # type: ignore[arg-type]

    with pytest.raises(ChunkSearchUnavailableError):
        await adapter.search_vector(uuid4(), embedding=[0.1, 0.2], limit=5)
