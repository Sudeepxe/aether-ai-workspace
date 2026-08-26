"""Reuses the real-Postgres/Redis testcontainers fixtures from
tests/integration/conftest.py — pytest only auto-discovers a conftest.py
for its own directory subtree, and contract/ is a sibling of
integration/, not a descendant, so these need an explicit re-export
rather than living in a third copy."""

from __future__ import annotations

from tests.integration.conftest import (  # noqa: F401 — re-exported fixtures
    postgres_url,
    redis_client,
    redis_url,
)
