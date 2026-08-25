"""Fixtures for the chaos-lite suite (S9 #97, §10.5's chaos-lite row).

Deliberately its own function-scoped Redis container, not the main
integration suite's session-scoped ``redis_client`` fixture
(``tests/integration/conftest.py``) — that one is shared across the
whole test session, so actually killing it here would break every other
integration test that happens to run afterward. Each chaos test gets a
fresh, disposable container it's free to kill.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from testcontainers.redis import RedisContainer

REDIS_IMAGE = (
    "redis:7-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2"
)


@pytest.fixture()
def killable_redis() -> Iterator[RedisContainer]:
    with RedisContainer(REDIS_IMAGE) as container:
        yield container
